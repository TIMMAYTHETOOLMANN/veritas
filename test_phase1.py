import sys, time
sys.path.insert(0, '.')
from core.rpc import RPC
import arb_engine

rpc = RPC('https://arb1.arbitrum.io/rpc', timeout=3, retries=1)

print("=== Test 1: discover_pools ===")
start = time.time()
pools = arb_engine.discover_pools(rpc)
t1 = time.time() - start
print(f"Pools: {len(pools)} in {t1:.2f}s")
for p in pools[:3]:
    print(f"  {p['name']}: WETH reserve={p['weth_reserve']:.2f}, quote reserve={p['quote_reserve']:.2f}")

print("\n=== Test 2: token_decimals cache ===")
for t in [arb_engine.WETH, arb_engine.USDC, arb_engine.USDCE]:
    d = arb_engine.token_decimals(rpc, t)
    print(f"  {t}: {d}")

print("\n=== Test 3: scan_once ===")
start = time.time()
result = arb_engine.scan_once(rpc)
t2 = time.time() - start
edges = result.get('edges', [])
print(f"scan_once: {len(edges)} edges, {len(result.get('pools', []))} pools in {t2:.2f}s")
print(f"ETH USD: {result.get('eth_usd')}")
print(f"Gas USD: {result.get('gas_usd')}")
if edges:
    print(f"First edge: size={edges[0].get('size_weth')} WETH, gross=${edges[0].get('gross_usd')}, net=${edges[0].get('net_usd')}")
if result.get('detail'):
    print(f"First report: {result['detail'][0]}")

print("\n=== Test 4: TOKENS cache size ===")
print(f"Tokens cached: {len(arb_engine.TOKEN_DECIMALS_CACHE)}")
print(f"Tokens metadata: {len(arb_engine.TOKENS)}")

print("\n=== ALL TESTS COMPLETE ===")