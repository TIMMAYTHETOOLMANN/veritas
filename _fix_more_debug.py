#!/usr/bin/env python3
"""Add more debug output."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Add debug after pools_with_zero_reserves
old_code = '''            # Fetch reserves for pools with zero reserves
            pools_with_zero_reserves = [p for p in pools if p.reserve0 == 0 and p.reserve1 == 0]
            if pools_with_zero_reserves:
                for pool in pools_with_zero_reserves:
                    if self.market_discovery.fetch_reserves(pool):
                        self.pool_registry.register(pool, persist=True)
                        stats["pools_refreshed"] = stats.get("pools_refreshed", 0) + 1'''

new_code = '''            # Fetch reserves for pools with zero reserves
            pools_with_zero_reserves = [p for p in pools if p.reserve0 == 0 and p.reserve1 == 0]
            print(f"[DEBUG] pools={len(pools)}, zero_reserve_pools={len(pools_with_zero_reserves)}", flush=True)
            if pools_with_zero_reserves:
                for pool in pools_with_zero_reserves:
                    print(f"[DEBUG] Fetching reserves for {pool.pool_id.pool_address[:20]}...", flush=True)
                    if self.market_discovery.fetch_reserves(pool):
                        self.pool_registry.register(pool, persist=True)
                        stats["pools_refreshed"] = stats.get("pools_refreshed", 0) + 1
                        print(f"[DEBUG] Success! reserve0={pool.reserve0}, reserve1={pool.reserve1}", flush=True)
                    else:
                        print(f"[DEBUG] Failed to fetch reserves", flush=True)'''

content = content.replace(old_code, new_code)

pathlib.Path('veritas_engine.py').write_text(content)
print("Added more debug output")
