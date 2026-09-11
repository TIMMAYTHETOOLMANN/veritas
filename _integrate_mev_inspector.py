#!/usr/bin/env python3
"""Integrate MEV Inspector into VERITAS engine."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# 1. Add MEV inspector imports
old_imports = '''from core.error_taxonomy import ErrorTaxonomy, ErrorCode, ClassifiedError
from core.capital_controller import CapitalController, CapitalMode
from core.rpc import RPC
from core.rpc_resilience import ResilientRPC, RPCCache
from core.external_apis import AlchemyRPC, CryptoArbitrageAPI, DeFiLlamaAPI'''

new_imports = '''from core.error_taxonomy import ErrorTaxonomy, ErrorCode, ClassifiedError
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
)'''

content = content.replace(old_imports, new_imports)

# 2. Update __init__ to add MEV components
old_init = '''        # Execution gate
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
        self._scan_history: List[Dict] = []'''

new_init = '''        # Execution gate
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
        self._scan_history: List[Dict] = []'''

content = content.replace(old_init, new_init)

# Update initialize() to create BlockProcessor with RPC
old_init_scan = '''            # Update quote engine with current RPC
            self.quote_engine = QuoteEngine(rpc)
            self.market_discovery = MarketDiscovery(rpc, self.pool_registry, self.chain_id)
            self.alchemy = AlchemyRPC()
            self.arbitrage_api = CryptoArbitrageAPI()
            self.defillama_api = DeFiLlamaAPI()
            self.price_oracle = PriceOracle(rpc)
            self.economic_model = EconomicModel(self.price_oracle)
            self.size_optimizer = SizeOptimizer(self.price_oracle)'''

new_init_scan = '''            # Update quote engine with current RPC
            self.quote_engine = QuoteEngine(rpc)
            self.market_discovery = MarketDiscovery(rpc, self.pool_registry, self.chain_id)
            self.alchemy = AlchemyRPC()
            self.arbitrage_api = CryptoArbitrageAPI()
            self.defillama_api = DeFiLlamaAPI()
            self.price_oracle = PriceOracle(rpc)
            self.economic_model = EconomicModel(self.price_oracle)
            self.size_optimizer = SizeOptimizer(self.price_oracle)

            # Update MEV block processor with current RPC
            self.block_processor = BlockProcessor(
                rpc=rpc,
                resilient_rpc=self.resilient_rpc,
                price_oracle=self.price_oracle,
                registry=self.pool_registry,
            )'''

content = content.replace(old_init_scan, new_init_scan)

# Add MEV processing in scan_once after route evaluation
old_scan_phase = '''            # Phase 4: Build edges (backward compat)
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
                    })'''

new_scan = '''            # Phase 4: Build edges (backward compat)
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

            # Phase 5: MEV Detection (arbitrage, liquidation, sandwich)
            if self.block_processor:
                mev_results = self.block_processor.process_block(block)
                
                # Track MEV opportunities
                for mev_type, opportunities in mev_results.items():
                    self.mev_tracker.add_opportunities(mev_type, opportunities)
                
                # Log MEV summary
                mev_summary = self.mev_tracker.get_summary()
                stats["mev_detected"] = mev_summary["total_detected"]
                stats["mev_profit_usd"] = mev_summary["total_profit_usd"]
                
                # Add MEV opportunities to result
                for mev_type, opportunities in mev_results.items():
                    for opp in opportunities:
                        result.candidates.append(Candidate(
                            route=f"MEV:{mev_type.value}:{getattr(opp, 'route', 'N/A')}",
                            buy_venue=getattr(opp, 'venue', 'unknown'),
                            sell_venue=getattr(opp, 'venue', 'unknown'),
                            token_pair=("MEV", mev_type.value),
                            input_amount=getattr(opp, 'profit_usd', 0),
                            gross_profit_usd=getattr(opp, 'profit_usd', 0),
                            net_profit_usd=getattr(opp, 'profit_usd', 0),
                            status=CandidateStatus.ECONOMICALLY_VIABLE,
                            block_number=block,
                        ))'''

content = content.replace(old_imports, new_imports)

pathlib.Path('veritas_engine.py').write_text(content)
print("Integrated MEV Inspector into VERITAS engine")
