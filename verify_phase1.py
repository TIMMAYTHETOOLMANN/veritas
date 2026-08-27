import sys, time
sys.path.insert(0, '.')
from core.rpc import RPC
rpc = RPC('https://arb1.arbitrum.io/rpc', timeout=1, retries=1)
import arb_engine

# Test 1: Token universe refresh
print("=== Test 1: Token Universe Refresh ===")
start = time.time()
arb_engine.refresh_token_universe(rpc)
t = time.time() - start
print(f"refresh: {t:.2f}s, TOKENS={len(arb_engine.TOKENS)}, cache={len(arb_engine.TOKEN_DECIMALS_CACHE)}")

# Test 2: discover_pools
print("\n=== Test 2: discover_pools ===")
start = time.time()
pools = arb_engine.discover_pools(rpc)
t = time.time() - start
print(f"pools: {len(pools)} in {t:.2f}s")

# Test 3: scan_once edges
print("\n=== Test 3: scan_once ===")
start = time.time()
result = arb_engine.scan_once(rpc)
t = time.time() - start
edges = result.get('edges', [])
print(f"scan_once: {len(edges)} edges in {t:.2f}s")
print(f"ETH USD: {result.get('eth_usd')}")
print(f"Gas USD: {result.get('gas_usd')}")

# Test 4: Report detail
print(f"\nReport count: {len(result.get('detail', []))}")
if result.get('detail'):
    print(f"First report keys: {list(result['detail'][0].keys())}")

print("\n=== ALL TESTS PASSED ===")