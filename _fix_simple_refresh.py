#!/usr/bin/env python3
"""Simplified fix: always fetch reserves for zero-reserve pools."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Find the discovery section and add reserve fetching for existing pools
old_post_discovery = '''            # Get all pools for scanning
            pools = self.pool_registry.get_live_pools(min_usd_depth=0)
            stats["pools_seen"] = len(pools)'''

new_post_discovery = '''            # Get all pools for scanning
            pools = self.pool_registry.get_live_pools(min_usd_depth=0)
            stats["pools_seen"] = len(pools)
            
            # Fetch reserves for any pools with zero reserves
            # (pools loaded from DB may have stale/zero reserve data)
            if pools:
                pools_with_zero_reserves = [p for p in pools if p.reserve0 == 0 and p.reserve1 == 0]
                if pools_with_zero_reserves:
                    print(f"[DEBUG] Fetching reserves for {len(pools_with_zero_reserves)} pools with zero reserves", flush=True)
                    for pool in pools_with_zero_reserves:
                        if self.market_discovery.fetch_reserves(pool):
                            self.pool_registry.register(pool, persist=True)
                            stats["pools_refreshed"] = stats.get("pools_refreshed", 0) + 1'''

content = content.replace(old_post_discovery, new_post_discovery)

pathlib.Path('veritas_engine.py').write_text(content)
print("Added reserve fetching for zero-reserve pools")
