#!/usr/bin/env python3
"""Wire market discovery into the engine."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Add import for MarketDiscovery
content = content.replace(
    'from core.pool import PoolId, PoolMetadata, PoolRegistry',
    'from core.pool import PoolId, PoolMetadata, PoolRegistry\nfrom core.market_discovery import MarketDiscovery'
)

# Add discovery initialization in __init__
content = content.replace(
    '        self.route_generator = RouteGenerator(self.pool_registry)',
    '        self.route_generator = RouteGenerator(self.pool_registry)\n        self.market_discovery: Optional[MarketDiscovery] = None'
)

# Add discovery initialization in initialize()
content = content.replace(
    '        # Initialize components that need RPC\n        self.quote_engine = QuoteEngine(rpc)',
    '        # Initialize components that need RPC\n        self.quote_engine = QuoteEngine(rpc)\n        self.market_discovery = MarketDiscovery(rpc, self.pool_registry, self.chain_id)'
)

# Replace scan_once() Phase 1 to actually discover pools
old_phase1 = '''        try:
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
            stats["pools_seen"] = len(pools)'''

new_phase1 = '''        try:
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
            self.market_discovery = MarketDiscovery(rpc, self.pool_registry, self.chain_id)

            # Phase 1: Discovery — query chain for pools
            # If registry has fewer than MIN_POOLS_FOR_ROUTING, discover from chain
            MIN_POOLS_FOR_ROUTING = 2
            existing_pools = self.pool_registry.get_live_pools(min_usd_depth=0)
            
            if len(existing_pools) < MIN_POOLS_FOR_ROUTING:
                # Discover pools from chain
                discovered = self.market_discovery.discover_pools(
                    self._hot_tokens,
                    venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],
                )
                stats["pools_discovered"] = len(discovered)
            else:
                stats["pools_discovered"] = 0

            # Get all pools for scanning (freshly discovered + existing)
            pools = self.pool_registry.get_live_pools(min_usd_depth=0)
            stats["pools_seen"] = len(pools)'''

content = content.replace(old_phase1, new_phase1)

pathlib.Path('veritas_engine.py').write_text(content)
print("Wired discovery into engine")
