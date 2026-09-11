#!/usr/bin/env python3
"""Fix discovery threshold: check for pools with sufficient liquidity, not just pool count."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace the discovery logic in scan_once()
old_logic = '''            # Phase 1: Discovery — query chain for pools
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

new_logic = '''            # Phase 1: Discovery — query chain for pools
            # Run discovery if:
            #   - No pools exist, OR
            #   - No pools have sufficient liquidity (reserves > 0)
            # This ensures we always have fresh chain data for routing.
            pools_with_liquidity = self.pool_registry.get_live_pools(min_usd_depth=100.0)
            needs_discovery = len(pools_with_liquidity) < 2
            
            if needs_discovery:
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

content = content.replace(old_logic, new_logic)

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed discovery threshold logic")
