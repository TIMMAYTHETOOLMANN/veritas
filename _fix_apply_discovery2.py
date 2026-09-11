#!/usr/bin/env python3
"""Apply discovery logic to scan_once() - precise version."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Use exact content from the file
old = '            # Phase 1: Discovery — get pools to scan\n            pools = self._get_scan_pools()\n            stats["pools_seen"] = len(pools)'

new = '''            # Phase 1: Discovery — query chain for pools
            # Run discovery if no pools have actual reserves
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = len(pools_with_reserves) < 2
            
            if needs_discovery:
                # Discover pools with RPC failover
                discovered = []
                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}
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
                    if discovered:
                        break
                
                stats["pools_discovered"] = len(discovered)
                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)
            else:
                stats["pools_discovered"] = 0

            # Get all pools for scanning
            pools = self.pool_registry.get_live_pools(min_usd_depth=0)
            stats["pools_seen"] = len(pools)'''

content = content.replace(old, new)

pathlib.Path('veritas_engine.py').write_text(content)

# Verify
verify = pathlib.Path('veritas_engine.py').read_text()
if 'discover_and_refresh' in verify:
    print("SUCCESS: Discovery logic applied")
else:
    print("FAILED: Discovery logic not applied")
    # Debug: show what's actually there
    lines = verify.split('\n')
    for i, line in enumerate(lines):
        if 'Phase 1' in line:
            print(f"Line {i+1}: {repr(line)}")
