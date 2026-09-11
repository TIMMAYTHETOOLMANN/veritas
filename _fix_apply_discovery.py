#!/usr/bin/env python3
"""Apply discovery logic to scan_once()."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace the Phase 1 section
old_phase1 = '''            # Phase 1: Discovery — get pools to scan
            pools = self._get_scan_pools()
            stats["pools_seen"] = len(pools)

            # Phase 2: Route generation'''

new_phase1 = '''            # Phase 1: Discovery — query chain for pools
            # Run discovery if:
            #   - No pools exist, OR
            #   - No pools have actual reserves (reserve0 > 0 AND reserve1 > 0)
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = len(pools_with_reserves) < 2
            
            if needs_discovery:
                # Discover pools with RPC failover
                discovered = []
                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}
                
                # Get all healthy endpoints and try each
                healthy_urls = self.rpc_health.get_healthy_urls()
                
                for url in healthy_urls:
                    from core.rpc import RPC
                    rpc_disc = RPC(url, timeout=30, retries=1)
                    
                    self.market_discovery = MarketDiscovery(
                        rpc_disc, self.pool_registry, self.chain_id
                    )
                    discovered = self.market_discovery.discover_and_refresh(
                        self._hot_tokens,
                        venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],
                    )
                    discovery_stats = self.market_discovery.stats()
                    
                    # If we found pools, stop
                    if discovered:
                        break
                
                stats["pools_discovered"] = len(discovered)
                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)
            else:
                stats["pools_discovered"] = 0

            # Get all pools for scanning
            pools = self.pool_registry.get_live_pools(min_usd_depth=0)
            stats["pools_seen"] = len(pools)

            # Phase 2: Route generation'''

content = content.replace(old_phase1, new_phase1)

pathlib.Path('veritas_engine.py').write_text(content)

# Verify
verify = pathlib.Path('veritas_engine.py').read_text()
if 'discover_and_refresh' in verify and 'needs_discovery' in verify:
    print("Discovery logic applied successfully")
else:
    print("WARNING: Discovery logic may not have applied")
