#!/usr/bin/env python3
"""Debug V3 pool verification."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Add debug output
old = '''            # For V3 pools, verify activity via slot0() instead of reserves
            v3_pools_needing_verify = [p for p in pools if p.kind == "v3" and p.liquidity == 0]
            if v3_pools_needing_verify:'''

new = '''            # For V3 pools, verify activity via slot0() instead of reserves
            v3_pools_needing_verify = [p for p in pools if p.kind == "v3" and p.liquidity == 0]
            print(f"[DEBUG] V3 pools needing verify: {len(v3_pools_needing_verify)}", flush=True)
            if v3_pools_needing_verify:'''

content = content.replace(old, new)

pathlib.Path('veritas_engine.py').write_text(content)
print("Added debug")
