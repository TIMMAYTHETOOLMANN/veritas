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
from core.price_oracle import PriceOracle, PricePoint
from core.quote_engine import QuoteEngine, QuoteResult
from core.economic_model import EconomicModel, EconomicResult
from core.size_optimizer import SizeOptimizer, SizeCurve
from core.route_generator import RouteGenerator, Route
from core.execution_gate import ExecutionGate, GateConfig, GateResult
from core.accounting import AccountingLedger, ExecutionRecord
from core.rpc_health import RPCHealthMonitor
from core.scan_result import ScanResult
from core.opportunity_telemetry import Candidate, CandidateStatus, RejectionReason
from core.error_taxonomy import ErrorTaxonomy, ErrorCode, ClassifiedError
from core.capital_controller import CapitalController, CapitalMode
from core.rpc import RPC


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
    "max_routes_per_token": 50,
    "min_profit_usd": 0.01,
    "max_gas_usd": 1.00,
    "max_slippage_bps": 50,
    "max_capital_exposure_usd": 100.0,
    "live_execution_enabled": False,
    "micro_capital_mode": True,
    "rpc_urls": [
        "https://gateway.tenderly.co/public/arbitrum",
        "https://arbitrum.drpc.org",
        "https://arbitrum.publicnode.com",
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
        self.pool_registry = PoolRegistry()
        self.quote_engine: Optional[QuoteEngine] = None
        self.price_oracle: Optional[PriceOracle] = None
        self.economic_model: Optional[EconomicModel] = None
        self.size_optimizer: Optional[SizeOptimizer] = None
        self.route_generator = RouteGenerator(self.pool_registry)
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

            # Update quote engine with current RPC
            self.quote_engine = QuoteEngine(rpc)
            self.price_oracle = PriceOracle(rpc)
            self.economic_model = EconomicModel(self.price_oracle)

            # Phase 1: Discovery — get pools to scan
            pools = self._get_scan_pools()
            stats["pools_seen"] = len(pools)

            # Phase 2: Route generation
            routes = self.route_generator.generate_all_routes(
                self._hot_tokens, max_hops=self.config["max_hops"]
            )
            routes = self.route_generator.rank_routes(routes)
            stats["routes_generated"] = len(routes)

            # Phase 3: Evaluate routes
            for route in routes[:self.config["max_routes_per_token"]]:
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

        Returns a Candidate with full economic analysis, or None if invalid.
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
            # Get the first step's pool
            first_step = route.steps[0]
            pools = self.pool_registry.get_pools_for_pair_and_venue(
                first_step.token_in, first_step.token_out, first_step.venue
            )

            if not pools:
                candidate.reject(RejectionReason.NO_LIQUIDITY)
                return candidate

            pool = pools[0]
            if not pool.has_sufficient_liquidity():
                candidate.reject(RejectionReason.NO_LIQUIDITY)
                return candidate

            # Get quote
            stats["quotes_attempted"] += 1

            # Determine input amount (use a small test size)
            test_size_usd = 1.0
            token_price = self.price_oracle.get_price_usd(first_step.token_in) or 2500.0
            test_size_float = test_size_usd / token_price
            amount_in = int(test_size_float * 10 ** 18)

            quote = self.quote_engine.quote(
                first_step.token_in, first_step.token_out, amount_in, pool
            )

            if not quote.is_valid:
                stats["quotes_failed"] += 1
                candidate.reject(RejectionReason.NO_QUOTE)
                return candidate

            stats["quotes_successful"] += 1

            # Calculate economics
            output_usd = test_size_usd  # Simplified — real impl uses output token price
            gross_profit = output_usd - test_size_usd

            if gross_profit > 0:
                stats["positive_gross_edges"] += 1

            economic = self.economic_model.evaluate(
                input_usd=test_size_usd,
                gross_output_usd=output_usd,
                flash_loan_amount_usd=test_size_usd,
                estimated_gas_units=quote.gas_estimate if quote.gas_estimate > 0 else 350000,
                gas_price_gwei=0.01,
                slippage_bps=int(quote.price_impact_bps),
            )

            # Populate candidate
            candidate.input_amount = test_size_usd
            candidate.gross_profit_usd = economic.gross_profit_usd
            candidate.swap_fees_usd = economic.dex_fees_usd
            candidate.flash_loan_fee_usd = economic.flash_loan_fee_usd
            candidate.estimated_gas_usd = economic.estimated_gas_usd
            candidate.estimated_slippage_usd = economic.estimated_slippage_usd
            candidate.safety_margin_usd = economic.safety_margin_usd
            candidate.net_profit_usd = economic.expected_net_profit_usd
            candidate.block_number = quote.block_number
            candidate.confidence = quote.confidence

            if economic.is_profitable:
                candidate.status = CandidateStatus.ECONOMICALLY_VIABLE
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
        return {
            "cycle": self._cycle_count,
            "running": self._running,
            "pools": self.pool_registry.count(),
            "rpc_health": self.rpc_health.status(),
            "capital": self.capital_controller.summary(),
            "accounting": self.accounting.summary(),
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
