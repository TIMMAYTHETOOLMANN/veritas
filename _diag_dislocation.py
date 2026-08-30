import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC
import arb_engine, v3_layer

rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=30, retries=2)
from_addr = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
WETH = arb_engine.WETH

t = time.time()
v3_raw, v2_pools = arb_engine._load_registry_pools(rpc)
v3 = [p for p in v3_raw if p["liquidity"] >= v3_layer.MIN_POOL_LIQUIDITY]
print(f"load+filter {time.time()-t:.1f}s | v3={len(v3)} v2={len(v2_pools)}", flush=True)

# Build venues exactly as scan does
quotes = {}
for p in v3:
    quotes.setdefault(p["quote"], []).append((p["name"], 1, p["pool"], p["fee"], p["sort_key"], None))
for p in v2_pools:
    mid = None
    if p.get("weth_reserve") and p.get("quote_reserve"):
        mid = p["quote_reserve"] / p["weth_reserve"]
    quotes.setdefault(p["quote"], []).append((p["name"], 0, p["address"], 0, p["sort_key"], mid))

print(f"distinct quote tokens: {len(quotes)}", flush=True)

# Count pairs, and measure how many survive 35bps and how much dislocation exists
total_pairs = 0
survive = 0
disl_dist = []
t = time.time()
for quote, venues in quotes.items():
    if len(venues) < 2:
        continue
    venues.sort(key=lambda x: x[4], reverse=True)
    v2_v = [v for v in venues if v[1] == 0]
    v3_v = [v for v in venues if v[1] == 1]
    if v2_v:
        keep = [v2_v[0]] + [v for v in v3_v] + [v for v in v2_v[1:]]
        venues = keep[:8]
    else:
        venues = venues[:8]
    for i in range(len(venues)):
        for j in range(i+1, len(venues)):
            total_pairs += 1
            b, s = venues[i], venues[j]
            bm = b[5]
            sm = s[5]
            if bm and sm:
                d = abs(bm/sm - 1.0)*1e4
                disl_dist.append(d)
                if d >= 35.0:
                    survive += 1
print(f"pair loop built in {time.time()-t:.1f}s | total_pairs={total_pairs} survive_35bps={survive}", flush=True)
if disl_dist:
    disl_dist.sort()
    import statistics
    print(f"dislocation bps: min={disl_dist[0]:.1f} median={statistics.median(disl_dist):.1f} "
          f"p90={disl_dist[int(len(disl_dist)*0.9)]:.1f} max={disl_dist[-1]:.1f}", flush=True)
    # how many have mid==None (V3 venues needing a quote)?
n_none = sum(1 for qq, vv in quotes.items() for v in vv if v[5] is None and v[1]==1)
print(f"V3 venues needing mid-quote (mid=None): {n_none}", flush=True)