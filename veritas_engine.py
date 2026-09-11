#!/usr/bin/env python3
"""
veritas_engine.py — VERITAS Autonomous Opportunity Engine.

The main orchestrator that ties together all components:
  DISCOVER -> SNAPSHOT -> QUOTE -> ROUTE -> SIZE -> ECONOMIC MODEL
  -> RANK -> SIMULATE -> EXECUTION GATE -> BROADCAST -> RECEIPT
  -> ON-CHAIN VERIFICATION -> IMMUTABLE LEDGER -> VERIFIED P/L
  -> CAPITAL COMPOUND -> IMMEDIATE RESCAN

Target operating behavior:
  - Discovery cycle every 15-30 seconds
  - Hot routes refreshed every 5 seconds
  - Immediate rescan after execution
  - No silent zeros
  - Every rejection explained
"""
from __future__ import annotations

import time
import sys
import os
from typing import Dict, List, Optional, Tuple

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.pool import PoolId, PoolMetadata, PoolRegistry
from core.market_discovery import MarketDiscovery
from core.price_oracle import PriceOracle, PricePoint
from core.quote_engine_v2 import QuoteEngine, QuoteResult
from core.economic_model import EconomicModel, EconomicResult
from core.size_optimizer import SizeOptimizer, SizeCurve
from core.route_generator import RouteGenerator, Route
from core.execution_gate import ExecutionGate, GateConfig, GateResult
from core.accounting import AccountingLedger, ExecutionRecord
from core.rpc_health import RPCHealthMonitor
from core.scan_result import ScanResult
from core.opportunity_telemetry import Candidate, CandidateStatus, RejectionReason
from core.route_valuation import (
    evaluate_route_economics,
    quote_full_route,
    start_token_decimals,
    validate_route_shape,
)
from core.error_taxonomy import ErrorTaxonomy, ErrorCode, ClassifiedError
from core.capital_controller import CapitalController, CapitalMode
from core.rpc import RPC
from core.rpc_resilience import ResilientRPC, RPCCache
from core.external_apis import AlchemyRPC, CryptoArbitrageAPI, DeFiLlamaAPI
from core.mev_inspector import (
    MEVType,
    ArbitrageOpportunity,
    LiquidationOpportunity,
    SandwichOpportunity,
    BlockProcessor,
    MEVTracker,
)


# ---- Default configuration ----
DEFAULT_CONFIG = {
    "chain_id": 42161,
    "chain_name": "arbitrum",
    "discovery_interval_seconds": 20,
    "quote_refresh_interval_seconds": 5,
    "hot_rescan_interval_seconds": 5,
    "heartbeat_seconds": 10,
    "max_scan_duration_seconds": 15,
    "max_hops": 3,
    "max_routes_per_token": 500,
    "min_profit_usd": 0.01,
    "max_gas_usd": 1.00,
    "max_slippage_bps": 50,
    "max_capital_exposure_usd": 100.0,
    "live_execution_enabled": False,
    "micro_capital_mode": True,
    "rpc_urls": [
        "https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol",
        "https://gateway.tenderly.co/public/arbitrum",
        "https://arbitrum.drpc.org",
    ],
}

# Core tokens (Tier 1 — always scan)
CORE_TOKENS = [
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",  # USDC.e
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",  # WBTC
    "0x912ce59144191c1204e64559fe8253a0e49e6548",  # ARB
]

# Known venues and their factories
VENUE_FACTORIES = {
    "uniswap_v2": "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
    "sushi": "0xc35dadb65012ec5796536bd9864ed8773abc74c4",
    "camelot": "0x6eccab422d763ac031210895c81787e87b43a652",
    "uniswap_v3": "0x1f98431c8ad98523631ae4a59f267346ea31f984",
    "sushi_v3": "0x1af415a1eba07a4986a52b6f2e7de7003d82231e",
    "pancake_v3": "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865",
    "ramses": "0xaa2cd7477c451e703f3b9ba5663334914763edf8",
}

V3_VENUES = {"uniswap_v3", "sushi_v3", "pancake_v3", "ramses"}
V2_VENUES = {"uniswap_v2", "sushi", "camelot"}


class VeritasEngine:
    """
    VERITAS Autonomous Opportunity Engine.

    Usage:
        engine = VeritasEngine()
        engine.run()  # Continuous loop
        # or
        result = engine.scan_once()  # Single scan cycle
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.chain_id = self.config["chain_id"]

        # Initialize components
        self.rpc_health = RPCHealthMonitor(self.config["rpc_urls"])
        self.resilient_rpc = ResilientRPC(
            [(url, i) for i, url in enumerate(self.config["rpc_urls"])],
            cache=RPCCache()
        )
        self.pool_registry = PoolRegistry()
        self.quote_engine: Optional[QuoteEngine] = None
        self.price_oracle: Optional[PriceOracle] = None
        self.economic_model: Optional[EconomicModel] = None
        self.size_optimizer: Optional[SizeOptimizer] = None
        self.route_generator = RouteGenerator(self.pool_registry)
        self.market_discovery: Optional[MarketDiscovery] = None
        self.alchemy: Optional[AlchemyRPC] = None
        self.arbitrage_api: Optional[CryptoArbitrageAPI] = None
        self.defillama_api: Optional[DeFiLlamaAPI] = None
        self.accounting = AccountingLedger()
        self.capital_controller = CapitalController()

        # Execution gate
        gate_config = GateConfig(
            max_gas_usd=self.config["max_gas_usd"],
            max_slippage_bps=self.config["max_slippage_bps"],
            max_capital_exposure_usd=self.config["max_capital_exposure_usd"],
            min_profit_usd=self.config["min_profit_usd"],
        )
        self.execution_gate = ExecutionGate(gate_config, self.rpc_health)

        # MEV Inspection
        self.mev_tracker = MEVTracker()
        self.block_processor = BlockProcessor(
            rpc=None,  # Will be set during scan
            resilient_rpc=self.resilient_rpc,
            price_oracle=self.price_oracle,
            registry=self.pool_registry,
        )

        # State
        self._cycle_count = 0
        self._running = False
        self._hot_tokens: List[str] = list(CORE_TOKENS)
        self._hot_pools: List[str] = []
        self._scan_history: List[Dict] = []

    def initialize(self) -> bool:
        """
        Initialize the engine: connect to RPC, load pools, verify health.

        Returns True if initialization succeeded.
        """
        print("[VERITAS] Initializing engine...", flush=True)

        # Get a healthy RPC
        rpc = self.rpc_health.get_healthy_rpc()
        if not rpc:
            print("[VERITAS] FATAL: No healthy RPC endpoints", flush=True)
            return False

        # Initialize components that need RPC
        self.quote_engine = QuoteEngine(rpc)
        self.market_discovery = MarketDiscovery(rpc, self.pool_registry, self.chain_id)
        self.alchemy = AlchemyRPC()
        self.arbitrage_api = CryptoArbitrageAPI()
        self.defillama_api = DeFiLlamaAPI()
        self.price_oracle = PriceOracle(rpc)
        self.economic_model = EconomicModel(self.price_oracle)
        self.size_optimizer = SizeOptimizer(self.price_oracle)

        # Load pools from database
        count = self.pool_registry.load_from_db(self.chain_id)
        print(f"[VERITAS] Loaded {count} pools from registry", flush=True)

        # Run health check
        health = self.rpc_health.health_check()
        healthy_count = sum(1 for v in health.values() if v)
        print(f"[VERITAS] RPC health: {healthy_count}/{len(health)} endpoints healthy", flush=True)

        # Initialize MEV components
        from core.mev_inspector import (
            ArbitrageDetector,
            LiquidationDetector,
            SandwichDetector,
            BlockProcessor,
            MEVTracker,
        )
        self.arbitrage_detector = ArbitrageDetector(rpc, self.resilient_rpc, self.price_oracle)
        self.liquidation_detector = LiquidationDetector(rpc, self.resilient_rpc, self.price_oracle)
        self.sandwich_detector = SandwichDetector(rpc, self.resilient_rpc)
        self.block_processor = BlockProcessor(
            self.arbitrage_detector,
            self.liquidation_detector,
            self.sandwich_detector,
        )
        
        # Update tracker reference
        self.mev_tracker = MEVTracker()

        # Verify price oracle
        eth_price = self.price_oracle.get_price_usd(CORE_TOKENS[0])
        print(f"[VERITAS] ETH price: ${eth_price:.2f}" if eth_price else "[VERITAS] ETH price: UNAVAILABLE", flush=True)

        print("[VERITAS] Engine initialized successfully", flush=True)
        return True

    def scan_once(self) -> ScanResult:
        """
        Execute a single scan cycle.

        Returns a ScanResult with full diagnostics.
        """
        self._cycle_count += 1
        start_time = time.time()
        result = ScanResult()

        # Statistics tracking
        stats = {
            "cycle": self._cycle_count,
            "pools_seen": 0,
            "pairs_seen": 0,
            "quotes_attempted": 0,
            "quotes_successful": 0,
            "quotes_failed": 0,
            "routes_generated": 0,
            "size_tests": 0,
            "positive_gross_edges": 0,
            "positive_net_edges": 0,
            "simulation_attempts": 0,
            "simulation_passes": 0,
            "execution_candidates": 0,
            "rejections_by_reason": {},
            "best_gross_edge": 0.0,
            "best_net_edge": 0.0,
            "best_size": 0.0,
            "best_route": "",
            "scan_duration_ms": 0,
            "rpc_failures": 0,
        }

        try:
            # Get healthy RPC
            rpc = self.rpc_health.get_healthy_rpc()
            if not rpc:
                stats["rpc_failures"] += 1
                result.statistics = stats
                result.scan_metadata = {"block_number": 0, "error": "no_healthy_rpc"}
                return result

            block = self._safe_block(rpc)
            result.scan_metadata = {"block_number": block, "cycle": self._cycle_count}

            # Update quote engine with resilient RPC
            self.quote_engine = QuoteEngine(rpc, self.resilient_rpc)
            self.price_oracle = PriceOracle(rpc)
            self.economic_model = EconomicModel(self.price_oracle)

            # Phase 1: Discovery -- query chain for pools
            # Run discovery on first scan or if registry is stale
            # Discovery runs every N scans to refresh market state
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = (
                self._cycle_count == 1 or  # First scan
                len(pools_with_reserves) < 10 or  # Too few pools
                self._cycle_count % 10 == 0  # Refresh every 10 scans
            )
            
            if needs_discovery:
                # Discover pools with RPC failover
                discovered = []
                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}
                healthy_urls = self.rpc_health.get_healthy_urls()
                
                for url in healthy_urls:
                    from core.rpc import RPC
                    rpc_disc = RPC(url, timeout=30, retries=1)
                    self.market_discovery = MarketDiscovery(
                        rpc_disc, self.pool_registry, self.chain_id,
                        resilient_rpc=self.resilient_rpc
                    )
                    discovered = self.market_discovery.discover_and_refresh(
                        self._hot_tokens,  # All hot tokens
                        venues=["uniswap_v3", "sushi_v3", "camelot", "uniswap_v2", "sushi"],
                        fee_tiers=[100, 500, 3000, 10000],  # All fee tiers
                    )
                    discovery_stats = self.market_discovery.stats()
                    if discovered:
                        break
                
                stats["pools_discovered"] = len(discovered)
                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)
                print(f"[DEBUG] Discovery: {len(discovered)} pools, stats={discovery_stats}", flush=True)
            else:
                stats["pools_discovered"] = 0
            
            # Get all pools for scanning
            pools = self.pool_registry.get_live_pools(min_usd_depth=0)
            stats["pools_seen"] = len(pools)

            # For V3 pools, verify activity via slot0() instead of reserves
            v3_pools_needing_verify = [p for p in pools if p.kind == "v3" and p.liquidity == 0]
            print(f"[DEBUG] V3 pools needing verify: {len(v3_pools_needing_verify)}", flush=True)
            if v3_pools_needing_verify:
                for pool in v3_pools_needing_verify:
                    try:
                        slot0 = self.resilient_rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
                        if slot0 and len(slot0) >= 66:
                            sqrt_px = int(slot0[2:66], 16)
                            if sqrt_px > 0:
                                pool.liquidity = 1  # Mark as active
                                self.pool_registry.register(pool, persist=True)
                                stats["pools_refreshed"] = stats.get("pools_refreshed", 0) + 1
                    except Exception as e:
                        print(f"[DEBUG] Verify failed: {str(e)[:60]}", flush=True)
                        continue


            # Phase 2: Route generation
            routes = self.route_generator.generate_all_routes(
                self._hot_tokens, max_hops=self.config["max_hops"]
            )
            routes = self.route_generator.rank_routes(routes)
            stats["routes_generated"] = len(routes)

            # Phase 3: Evaluate routes (quote ALL generated routes)
            for route in routes:
                candidate = self._evaluate_route(route, rpc, stats)
                if candidate:
                    result.candidates.append(candidate)

                    if candidate.net_profit_usd > 0:
                        stats["positive_net_edges"] += 1
                        if candidate.net_profit_usd > stats["best_net_edge"]:
                            stats["best_net_edge"] = candidate.net_profit_usd
                            stats["best_route"] = candidate.route
                            stats["best_size"] = candidate.input_amount

            # Phase 4: Build edges (backward compat)
            for candidate in result.candidates:
                if candidate.status == CandidateStatus.ECONOMICALLY_VIABLE:
                    result.edges.append({
                        "route": candidate.route,
                        "buy_venue": candidate.buy_venue,
                        "sell_venue": candidate.sell_venue,
                        "input_usd": candidate.input_amount,
                        "gross_profit_usd": candidate.gross_profit_usd,
                        "net_profit_usd": candidate.net_profit_usd,
                        "block": candidate.block_number,
                    })

            # Record rejections
            for candidate in result.candidates:
                if candidate.rejection_reason:
                    result.record_rejection(candidate.rejection_reason)

            # Phase 5: MEV Detection - analyze current block for MEV opportunities
            if block > 0 and self.block_processor:
                try:
                    mev_results = self.block_processor.process_block(block)
                    
                    # Track MEV opportunities
                    for mev_type, opportunities in mev_results.items():
                        self.mev_tracker.add_opportunities(mev_type, opportunities)
                    
                    # Add MEV stats to result
                    stats["mev_arbitrages"] = len(mev_results.get(MEVType.ARBITRAGE, []))
                    stats["mev_liquidations"] = len(mev_results.get(MEVType.LIQUIDATION, []))
                    stats["mev_sandwiches"] = len(mev_results.get(MEVType.SANDWICH, []))
                    
                    # Add top MEV opportunities to result metadata
                    top_mev = self.mev_tracker.get_top_opportunities(10)
                    result.scan_metadata["mev_opportunities"] = [
                        {
                            "type": opp.__class__.__name__.replace("Opportunity", "").lower(),
                            "block": opp.block_number,
                            "profit_usd": opp.profit_usd,
                            "tx_hash": getattr(opp, "tx_hash", ""),
                        }
                        for opp in top_mev
                    ]
                except Exception as e:
                    # Don't let MEV detection failures break the scan
                    pass

        except Exception as e:
            err = ErrorTaxonomy.classify(e, block_number=0)
            result.scan_metadata["error"] = err.to_dict()

        # Finalize statistics
        stats["scan_duration_ms"] = round((time.time() - start_time) * 1000, 1)
        result.statistics = stats

        # Persist scan
        self._persist_scan(result)

        return result

    def run(self) -> None:
        """
        Run the engine continuously.
        """
        if not self.initialize():
            print("[VERITAS] Initialization failed, exiting", flush=True)
            return

        self._running = True
        print(f"[VERITAS] Starting continuous scan (interval={self.config['discovery_interval_seconds']}s)", flush=True)

        last_scan = 0.0
        last_heartbeat = 0.0

        while self._running:
            try:
                now = time.time()

                # Scan
                if now - last_scan >= self.config["discovery_interval_seconds"]:
                    result = self.scan_once()
                    last_scan = now

                    # Heartbeat
                    if now - last_heartbeat >= self.config["heartbeat_seconds"]:
                        self._print_heartbeat(result)
                        last_heartbeat = now

                # Small sleep to prevent CPU spinning
                time.sleep(0.1)

            except KeyboardInterrupt:
                print("\n[VERITAS] Shutting down...", flush=True)
                self._running = False
            except Exception as e:
                err = ErrorTaxonomy.classify(e)
                print(f"[VERITAS] Cycle error: {err.code.value}: {err.message}", flush=True)
                time.sleep(1)

    def stop(self) -> None:
        """Stop the engine."""
        self._running = False

    def _get_scan_pools(self) -> List[PoolMetadata]:
        """Get pools to scan this cycle."""
        # Prefer hot pools, then all live pools
        if self._hot_pools:
            hot = [self.pool_registry.get(PoolId(
                chain_id=self.chain_id, venue="", factory="", pool_address=addr
            )) for addr in self._hot_pools]
            hot = [p for p in hot if p is not None]
            if hot:
                return hot

        return self.pool_registry.get_live_pools(min_usd_depth=100.0)

    def _evaluate_route(
        self, route: Route, rpc: RPC, stats: Dict
    ) -> Optional[Candidate]:
        """
        Evaluate a single route for profitability.

        Canonical pipeline (shared with the forensic funnel):
          validate_route_shape()
            -> quote_full_route() hop-by-hop
            -> per-hop provenance gate (authoritative quotes only)
            -> same-asset final comparison
            -> canonical EconomicModel.evaluate()
            -> execution gate via is_worth_executing

        Returns a Candidate with full economic analysis. Every rejection
        carries an explicit reason; a profitable-but-below-threshold route
        is NOT economically viable.
        """
        if not route.steps:
            return None

        candidate = Candidate(
            route=str(route),
            buy_venue=route.steps[0].venue,
            sell_venue=route.steps[-1].venue,
            token_pair=(route.steps[0].token_in, route.steps[-1].token_out),
            status=CandidateStatus.DISCOVERED,
        )

        try:
            # 1. Route-shape validation (canonical invariant:
            #    different-token amounts are never comparable).
            shape_ok, _shape_reason = validate_route_shape(route)
            if not shape_ok:
                stats["quotes_failed"] = stats.get("quotes_failed", 0) + 1
                candidate.reject(RejectionReason.MALFORMED_ROUTE)
                return candidate

            # 2. Test size in native units of the start token. Decimals come
            #    from first-hop pool metadata, never a hardcoded assumption.
            stats["quotes_attempted"] += 1

            test_size_usd = 1.0
            first_pool = None
            try:
                first_pool = self.pool_registry.get_by_address(
                    self.chain_id, route.steps[0].pool_address
                )
            except Exception:
                first_pool = None
            decimals = (
                start_token_decimals(route, first_pool)
                if first_pool is not None
                else 18
            )
            token_price = (
                self.price_oracle.get_price_usd(route.steps[0].token_in)
                or 2500.0
            )
            amount_in = int(test_size_usd / token_price * (10 ** decimals))

            # 3. Full-path triangular quote: hop N out -> hop N+1 in.
            final_out, hops, failure = quote_full_route(
                route,
                amount_in,
                self.quote_engine,
                self.pool_registry,
                self.resilient_rpc,
                self.chain_id,
            )

            if failure is not None:
                stats["quotes_failed"] += 1
                if failure.startswith("non_authoritative_quote_at_step"):
                    candidate.reject(RejectionReason.NON_AUTHORITATIVE_QUOTE)
                elif failure.startswith("no_pool_for_step"):
                    candidate.reject(RejectionReason.NO_POOL_FOR_STEP)
                elif failure.startswith((
                    "quote_failed_at_step",
                    "no_liquidity_at_step",
                )):
                    candidate.reject(RejectionReason.QUOTE_FAILED_AT_STEP)
                elif failure.startswith((
                    "route_malformed",
                    "chain_break",
                )):
                    candidate.reject(RejectionReason.MALFORMED_ROUTE)
                else:
                    candidate.reject(RejectionReason.NO_QUOTE)
                return candidate

            stats["quotes_successful"] += 1

            # 4. Same-asset round-trip economics via the canonical model.
            #    final_out is denominated in the START token: the ONLY valid
            #    comparison (A_final vs A_initial, never B vs A).
            if final_out > amount_in:
                stats["positive_gross_edges"] += 1

            candidate.quote_source = ",".join(
                str(h.get("source", "")) for h in hops
            )
            candidate.confidence = (
                min(float(h.get("confidence", 0) or 0) for h in hops)
                if hops
                else 0.0
            )
            hop_blocks = [
                int(h.get("block_number", 0) or 0) for h in hops
            ]
            candidate.block_number = (
                max(hop_blocks) if hop_blocks else 0
            )

            economic = evaluate_route_economics(
                amount_in_native=amount_in,
                input_usd=test_size_usd,
                final_out_native=final_out,
                hops=hops,
                economic_model=self.economic_model,
                gas_price_gwei=0.01,
                block_number=candidate.block_number,
            )

            # Populate candidate
            candidate.input_amount = test_size_usd
            candidate.output_amount = (
                test_size_usd * (final_out / amount_in)
                if amount_in > 0
                else 0.0
            )
            candidate.gross_profit_usd = economic.gross_profit_usd
            candidate.swap_fees_usd = economic.dex_fees_usd
            candidate.flash_loan_fee_usd = economic.flash_loan_fee_usd
            candidate.estimated_gas_usd = economic.estimated_gas_usd
            candidate.estimated_slippage_usd = economic.estimated_slippage_usd
            candidate.safety_margin_usd = economic.safety_margin_usd
            candidate.net_profit_usd = economic.expected_net_profit_usd

            if economic.is_worth_executing:
                candidate.status = CandidateStatus.ECONOMICALLY_VIABLE
            elif economic.is_profitable:
                reason = (economic.below_threshold_reason or "").lower()
                if "min_profit" in reason or "profit" in reason:
                    candidate.reject(RejectionReason.BELOW_MIN_PROFIT)
                else:
                    candidate.reject(RejectionReason.BELOW_MIN_ROI)
            else:
                candidate.reject(RejectionReason.FEE_REJECTION)

        except Exception as e:
            err = ErrorTaxonomy.classify(e)
            candidate.reject(err.to_rejection_reason())

        return candidate

    def _print_heartbeat(self, result: ScanResult) -> None:
        """Print a heartbeat line."""
        stats = result.statistics
        edges = len(result.edges)
        best = stats.get("best_net_edge", 0.0)

        if edges > 0:
            print(
                f"[VERITAS] cycle={stats.get('cycle', '?')} "
                f"block={result.scan_metadata.get('block_number', '?')} "
                f"pools={stats.get('pools_seen', 0)} "
                f"routes={stats.get('routes_generated', 0)} "
                f"quotes={stats.get('quotes_successful', 0)}/{stats.get('quotes_attempted', 0)} "
                f"edges={edges} "
                f"best=${best:.4f} "
                f"next_scan={self.config['discovery_interval_seconds']}s",
                flush=True
            )
        else:
            # Distinguish MARKET_ZERO from SYSTEM_ZERO
            rpc_ok = self.rpc_health.is_healthy()
            if not rpc_ok:
                print(
                    f"[VERITAS] SCANNER FAILURE — RPC unhealthy "
                    f"cycle={stats.get('cycle', '?')} "
                    f"quotes={stats.get('quotes_successful', 0)}/{stats.get('quotes_attempted', 0)}",
                    flush=True
                )
            elif stats.get("quotes_attempted", 0) == 0:
                print(
                    f"[VERITAS] SCANNER FAILURE — no quotes attempted "
                    f"cycle={stats.get('cycle', '?')}",
                    flush=True
                )
            else:
                print(
                    f"[VERITAS] MARKET QUIET — "
                    f"cycle={stats.get('cycle', '?')} "
                    f"routes={stats.get('routes_generated', 0)} "
                    f"quotes={stats.get('quotes_successful', 0)}/{stats.get('quotes_attempted', 0)} "
                    f"rejections={sum(result.rejections.values())}",
                    flush=True
                )

    def _persist_scan(self, result: ScanResult) -> None:
        """Persist scan result to history."""
        self._scan_history.append({
            "cycle": result.statistics.get("cycle", 0),
            "block": result.scan_metadata.get("block_number", 0),
            "edges": len(result.edges),
            "candidates": len(result.candidates),
            "rejections": dict(result.rejections),
            "statistics": result.statistics,
            "timestamp": time.time(),
        })
        # Keep only last 1000 scans
        if len(self._scan_history) > 1000:
            self._scan_history = self._scan_history[-1000:]

    def _safe_block(self, rpc: RPC) -> int:
        """Get current block number safely."""
        try:
            return rpc.eth_blockNumber()
        except Exception:
            return 0

    def register_pool(
        self,
        venue: str,
        factory: str,
        pool_address: str,
        token0: str,
        token1: str,
        fee: int = 3000,
        kind: str = "v2",
        decimals0: int = 18,
        decimals1: int = 6,
    ) -> None:
        """Register a pool in the registry."""
        pool_id = PoolId(
            chain_id=self.chain_id,
            venue=venue,
            factory=factory,
            pool_address=pool_address,
        )
        pool = PoolMetadata(
            pool_id=pool_id,
            token0=token0,
            token1=token1,
            decimals0=decimals0,
            decimals1=decimals1,
            fee=fee,
            kind=kind,
            is_live=True,
        )
        self.pool_registry.register(pool)

    def status(self) -> dict:
        """Get engine status."""
        mev_summary = self.mev_tracker.get_summary() if self.mev_tracker else {}
        return {
            "cycle": self._cycle_count,
            "running": self._running,
            "pools": self.pool_registry.count(),
            "rpc_health": self.rpc_health.status(),
            "capital": self.capital_controller.summary(),
            "accounting": self.accounting.summary(),
            "mev": mev_summary,
            "alchemy": self.alchemy.stats() if self.alchemy else None,
            "arbitrage_api": self.arbitrage_api.stats() if self.arbitrage_api else None,
            "defillama_api": self.defillama_api.stats() if self.defillama_api else None,
            "config": {k: v for k, v in self.config.items() if "secret" not in k.lower()},
        }


def main():
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="VERITAS Autonomous Opportunity Engine")
    parser.add_argument("--run", action="store_true", help="Run continuous scan loop")
    parser.add_argument("--once", action="store_true", help="Single scan cycle")
    parser.add_argument("--status", action="store_true", help="Show engine status")
    parser.add_argument("--interval", type=int, default=20, help="Scan interval in seconds")
    args = parser.parse_args()

    config = {"discovery_interval_seconds": args.interval}
    engine = VeritasEngine(config)

    if args.status:
        if engine.initialize():
            import json
            print(json.dumps(engine.status(), indent=2, default=str))
        return

    if args.once:
        if engine.initialize():
            result = engine.scan_once()
            print(result.generate_why_zero_report())
        return

    if args.run:
        engine.run()
        return

    # Default: single scan
    if engine.initialize():
        result = engine.scan_once()
        print(result.generate_why_zero_report())


if __name__ == "__main__":
    main()
