#!/usr/bin/env python3
"""Fix the discovery failover loop to try all endpoints until pools are found."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

old_failover = '''            if needs_discovery:
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

new_failover = '''            if needs_discovery:
                # Discover pools with RPC failover
                # Try each healthy RPC endpoint until pools are found
                discovered = []
                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}
                
                # Get all healthy endpoints
                healthy_urls = self.rpc_health.get_healthy_urls()
                
                for url in healthy_urls:
                    from core.rpc import RPC
                    rpc = RPC(url, timeout=30, retries=1)
                    
                    self.market_discovery = MarketDiscovery(
                        rpc, self.pool_registry, self.chain_id
                    )
                    discovered = self.market_discovery.discover_pools(
                        self._hot_tokens,
                        venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],
                    )
                    discovery_stats = self.market_discovery.stats()
                    
                    # If we found pools, stop
                    if discovered:
                        break
                    
                    # No pools found — try next endpoint
                    # (might be rate-limited or have empty results)
                
                stats["pools_discovered"] = len(discovered)
                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)
            else:
                stats["pools_discovered"] = 0'''

content = content.replace(old_failover, new_failover)

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed discovery failover loop")
