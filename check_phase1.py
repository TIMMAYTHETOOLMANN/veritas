import sys, time
sys.path.insert(0, '.')
from core.rpc import RPC
rpc = RPC('https://arb1.arbitrum.io/rpc', timeout=1, retries=1)
import arb_engine

# Quick verification
arb_engine.refresh_token_universe(rpc)
print(f'TOKENS={len(arb_engine.TOKENS)}')
print(f'cache={len(arb_engine.TOKEN_DECIMALS_CACHE)}')

# Test token decimals
weth_d = arb_engine.token_decimals(rpc, '0x82af49447d8a07e3bd95bd0d56f35241523fbab1')
usdc_d = arb_engine.token_decimals(rpc, '0xaf88d065e77c8cc2239327c5edb3a432268e5831')
print(f'WETH decimals: {weth_d}')
print(f'USDC decimals: {usdc_d}')

# Test discover_pools
pools = arb_engine.discover_pools(rpc)
print(f'Pools: {len(pools)}')

# Test scan_once
result = arb_engine.scan_once(rpc)
edges = result.get('edges', [])
print(f'Edges: {len(edges)}')
print(f'ETH: {result.get("eth_usd")}')
print(f'Gas: {result.get("gas_usd")}')

print('ALL CHECKS PASSED')