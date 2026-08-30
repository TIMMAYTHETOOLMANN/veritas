import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC
import arb_engine

rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=30, retries=2)
t0 = time.time()
edges, report = arb_engine.scan_cross_venue(
    rpc, 2431.88, 0.05, size_steps=6, max_venues_per_quote=4,
    use_multi_hop=True, use_parallel=False)
dt = time.time() - t0
print(f"elapsed {dt:.1f}s | rows={len(report)} | edges={len(edges)}", flush=True)

have_net = [r for r in report if "net_usd" in r]
print(f"rows with net computed: {len(have_net)}", flush=True)
if have_net:
    nets = sorted(r["net_usd"] for r in have_net)
    print(f"net_usd range: {nets[0]:.4f} .. {nets[-1]:.4f}  | net>0: {sum(1 for n in nets if n>0)}", flush=True)
    # show the top rows by net regardless of sign
    top = sorted(have_net, key=lambda r: r["net_usd"], reverse=True)[:10]
    for r in top:
        print(f"  {r.get('pair'):18s} {r.get('venue_buy'):22s} -> {r.get('venue_sell'):22s} "
              f"net=${r.get('net_usd'):+.4f} gross=${r.get('gross_usd'):.4f} size={r.get('size_weth')}W", flush=True)
if edges:
    print("EDGES:", flush=True)
    for e in edges[:10]:
        print("  ", e.get("pair"), e.get("venue_buy"), "->", e.get("venue_sell"),
              "net=$", e.get("net_usd"), "size", e.get("size_weth"), "WETH", flush=True)
else:
    print("NO EDGES", flush=True)