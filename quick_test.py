import sys, time
sys.path.insert(0, '.')
from core.rpc import RPC
rpc = RPC('https://arb1.arbitrum.io/rpc', timeout=1, retries=1)
import arb_engine

start = time.time()
pools = arb_engine.discover_pools(rpc)
t = time.time() - start
print(f'discover_pools: {len(pools)} pools in {t:.2f}s')
if pools:
    print(f'First: {pools[0]["name"]}')

print(f'\nTOKENS: {len(arb_engine.TOKENS)}')
print(f'TOKEN_DECIMALS_CACHE: {len(arb_engine.TOKEN_DECIMALS_CACHE)}')