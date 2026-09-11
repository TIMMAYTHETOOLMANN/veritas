#!/usr/bin/env python3
"""Add RPC failover to discovery in scan_once()."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace the discovery section with failover logic
old_discovery = '''            # Phase 1: Discovery — query chain for pools
            # Run discovery if:
            #   - No pools exist, OR
            #   - No pools have actual reserves (reserve0 > 0 AND reserve1 > 0)
            # This ensures we always have fresh chain data for routing.
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = len(pools_with_reserves) < 2
            
            if needs_discovery:
                # Discover pools from chain
                discovered = self.market_discovery.discover_pools(
                    self._hot_tokens,
                    venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],
                )
                stats["pools_discovered"] = len(discovered)
            else:
                stats["pools_discovered"] = 0'''

new_discovery = '''            # Phase 1: Discovery — query chain for pools
            # Run discovery if:
            #   - No pools exist, OR
            #   - No pools have actual reserves (reserve0 > 0 AND reserve1 > 0)
            # This ensures we always have fresh chain data for routing.
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = len(pools_with_reserves) < 2
            
            if needs_discovery:
                # Discover pools with RPC failover
                # Try each healthy RPC endpoint until discovery succeeds
                discovered = []
                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}
                
                # Get unique RPC URLs from health monitor
                tried_urls = set()
                for _ in range(len(self.rpc_health.endpoints)):
                    rpc = self.rpc_health.get_healthy_rpc()
                    if rpc.url in tried_urls:
                        continue
                    tried_urls.add(rpc.url)
                    
                    self.market_discovery = MarketDiscovery(
                        rpc, self.pool_registry, self.chain_id
                    )
                    discovered = self.market_discovery.discover_pools(
                        self._hot_tokens,
                        venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],
                    )
                    discovery_stats = self.market_discovery.stats()
                    
                    # If we found pools or hit a non-rate-limit error, stop
                    if discovered or discovery_stats.get("rate_limits", 0) == 0:
                        break
                    
                    # Rate limited — try next endpoint
                    self.rpc_health.record_error(rpc.url, "rate limit", is_timeout=False)
                
                stats["pools_discovered"] = len(discovered)
                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)
            else:
                stats["pools_discovered"] = 0'''

content = content.replace(old_discovery, new_discovery)

pathlib.Path('veritas_engine.py').write_text(content)
print("Added RPC failover to discovery")
