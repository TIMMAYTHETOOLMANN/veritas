#!/usr/bin/env python3
"""Add get_pools_with_reserves() and fix discovery check."""
import pathlib

content = pathlib.Path('core/pool.py').read_text()

# Add get_pools_with_reserves() method after get_live_pools()
old_method = '''    def get_hot_pools(self, min_edge_count: int = 1) -> List[PoolMetadata]:
        """Get pools that have historically produced edges."""
        return [p for p in self._pools.values()
                if p.historical_edge_count >= min_edge_count]'''

new_method = '''    def get_pools_with_reserves(self) -> List[PoolMetadata]:
        """Get pools that have non-zero reserves (actual on-chain liquidity)."""
        return [p for p in self._pools.values()
                if p.reserve0 > 0 and p.reserve1 > 0]

    def get_hot_pools(self, min_edge_count: int = 1) -> List[PoolMetadata]:
        """Get pools that have historically produced edges."""
        return [p for p in self._pools.values()
                if p.historical_edge_count >= min_edge_count]'''

content = content.replace(old_method, new_method)

pathlib.Path('core/pool.py').write_text(content)

# Now fix the engine to use get_pools_with_reserves()
engine_content = pathlib.Path('veritas_engine.py').read_text()

old_check = '''            # Phase 1: Discovery — query chain for pools
            # Run discovery if:
            #   - No pools exist, OR
            #   - No pools have sufficient liquidity (reserves > 0)
            # This ensures we always have fresh chain data for routing.
            pools_with_liquidity = self.pool_registry.get_live_pools(min_usd_depth=100.0)
            needs_discovery = len(pools_with_liquidity) < 2'''

new_check = '''            # Phase 1: Discovery — query chain for pools
            # Run discovery if:
            #   - No pools exist, OR
            #   - No pools have actual reserves (reserve0 > 0 AND reserve1 > 0)
            # This ensures we always have fresh chain data for routing.
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = len(pools_with_reserves) < 2'''

engine_content = engine_content.replace(old_check, new_check)

pathlib.Path('veritas_engine.py').write_text(engine_content)
print("Fixed pool liquidity check and discovery threshold")
