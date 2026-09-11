#!/usr/bin/env python3
"""Skip reserve fetching for V3 pools - use slot0 instead."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Update the zero-reserve fetch logic to handle V3 pools
old = '''            # Fetch reserves for pools with zero reserves
            pools_with_zero_reserves = [p for p in pools if p.reserve0 == 0 and p.reserve1 == 0]
            if pools_with_zero_reserves:
                for pool in pools_with_zero_reserves:
                    if self.market_discovery.fetch_reserves(pool):
                        self.pool_registry.register(pool, persist=True)
                        stats["pools_refreshed"] = stats.get("pools_refreshed", 0) + 1'''

new = '''            # For V3 pools, verify activity via slot0() instead of reserves
            v3_pools_needing_verify = [p for p in pools if p.kind == "v3" and p.liquidity == 0]
            if v3_pools_needing_verify:
                for pool in v3_pools_needing_verify:
                    try:
                        rpc = self.resilient_rpc.get_healthy_rpc() if self.resilient_rpc else rpc
                        slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
                        if slot0 and len(slot0) >= 66:
                            sqrt_px = int(slot0[2:66], 16)
                            if sqrt_px > 0:
                                pool.liquidity = 1  # Mark as active
                                self.pool_registry.register(pool, persist=True)
                                stats["pools_refreshed"] = stats.get("pools_refreshed", 0) + 1
                    except Exception:
                        continue'''

content = content.replace(old, new)

pathlib.Path('veritas_engine.py').write_text(content)
print("Updated to verify V3 pools via slot0")
