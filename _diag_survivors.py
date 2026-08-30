import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC
import arb_engine, v3_layer

rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=30, retries=2)
WETH = arb_engine.WETH

v3_raw, v2_pools = arb_engine._load_registry_pools(rpc)
v3 = [p for p in v3_raw if p["liquidity"] >= v3_layer.MIN_POOL_LIQUIDITY]

quotes = {}
namemap = {}   # (kind,addr) -> name for printing
for p in v3:
    quotes.setdefault(p["quote"], []).append((p["name"], 1, p["pool"], p["fee"], p["sort_key"], None))
    namemap[(1, p["pool"])] = p["name"]
for p in v2_pools:
    mid = p["quote_reserve"] / p["weth_reserve"] if (p.get("weth_reserve") and p.get("quote_reserve")) else None
    quotes.setdefault(p["quote"], []).append((p["name"], 0, p["address"], 0, p["sort_key"], mid))
    namemap[(0, p["address"])] = p["name"]

print("=== PAIRS WITH >=35bps dislocation (stored-reserve based) ===")
for quote, venues in quotes.items():
    if len(venues) < 2: continue
    venues.sort(key=lambda x: x[4], reverse=True)
    v2_v=[v for v in venues if v[1]==0]; v3_v=[v for v in venues if v[1]==1]
    if v2_v: venues=( [v2_v[0]] + [v for v in v3_v] + [v for v in v2_v[1:]] )[:8]
    else: venues=venues[:8]
    qsym = arb_engine.token_symbol(quote)
    for i in range(len(venues)):
        for j in range(i+1,len(venues)):
            b,s=venues[i],venues[j]
            bm,sm=b[5],s[5]
            if bm and sm:
                d=abs(bm/sm-1.0)*1e4
                if d>=35.0:
                    print(f"  {qsym:12s} | {b[0]:28s}({bm:.6g}) vs {s[0]:28s}({sm:.6g}) | {d:.1f} bps  kinds=({b[1]},{s[1]})", flush=True)