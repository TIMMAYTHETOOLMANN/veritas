import sys, time
sys.path.insert(0, '.')
from core.rpc import RPC
rpc = RPC('https://arb1.arbitrum.io/rpc', timeout=1, retries=1)
import arb_engine

# Refresh token universe first
print("=== Refreshing token universe ===")
start = time.time()
arb_engine.refresh_token_universe(rpc)
t = time.time() - start
print(f'refresh_token_universe: {t:.2f}s')
print(f'TOKENS: {len(arb_engine.TOKENS)}')
print(f'TOKEN_DECIMALS_CACHE: {len(arb_engine.TOKEN_DECIMALS_CACHE)}')

# Now scan
print("\n=== Running scan_once ===")
start = time.time()
result = arb_engine.scan_once(rpc)
t = time.time() - start
edges = result.get('edges', [])
print(f'scan_once: {len(edges)} edges, {len(result.get("pools", []))} pools in {t:.2f}s')
print(f'ETH USD: {result.get("eth_usd")}')
print(f'Gas USD: {result.get("gas_usd")}')

if edges:
    print(f"\n=== Found {len(edges)} edges ===")
    for i, e in enumerate(edges[:5]):
        print(f"\nEdge {i+1}:")
        print(f"  size_weth: {e.get('size_weth')} WETH")
        print(f"  gross_usd: {e.get('gross_usd')}")
        print(f"  net_usd: {e.get('net_usd')}")
        print(f"  buy: {e.get('buy_venue')} ({e.get('buy_kind')}, fee {e.get('buy_fee')})")
        print(f"  sell: {e.get('sell_venue')} ({e.get('sell_kind')}, fee {e.get('sell_fee')})")
        print(f"  quote: {e.get('quote')[:8]}...")
else:
    print("\n=== No edges found ===")
    print(f"Report count: {len(result.get('detail', []))}")
    if result.get('detail'):
        print(f"First report: {result['detail'][0]}")

print(f"\n=== Total time: {time.time() - start:.2f}s ===")