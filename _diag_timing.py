import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC
import arb_engine

rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=30, retries=2)

def step(name, fn):
    t = time.time()
    r = fn()
    print(f"[{time.time()-t:6.1f}s] {name} -> {r if r is not None else ''}", flush=True)
    return r

step("refresh_token_universe", lambda: arb_engine.refresh_token_universe(rpc))
v3, v2 = step("_load_registry_pools", lambda: arb_engine._load_registry_pools(rpc))
print("  v3 census raw:", len(v3), " v2 pools:", len(v2), flush=True)

import v3_layer
v3_census = [p for p in v3 if p["liquidity"] >= v3_layer.MIN_POOL_LIQUIDITY]
print("  v3 after liquidity filter:", len(v3_census), flush=True)