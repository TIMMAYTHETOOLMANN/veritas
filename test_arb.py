import sys
sys.path.insert(0, '.')
from core.rpc import RPC
import v3_layer
import arb_engine

rpc = RPC('https://arb1.arbitrum.io/rpc', timeout=30, retries=3)
from_addr = '0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f'

WETH = '0x82af49447d8a07e3bd95bd0d56f35241523fbab1'
USDC = '0xaf88d065e77c8cc2239327c5edb3a432268e5831'
USDCE = '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8'

# Pancake V3 WETH/USDC.e 500 pool - CHEAP WETH
# Other V3 venues - EXPENSIVE WETH
pancake_pool = '0x0e8fcea6153205e3a3a184b0613578c3d1729eab'
uniswap_pool = '0xc31e54c7a869b9fcbecc14363cf510d1c41fa443'
sushi_pool = '0x15e444da5b343c5a0931f5d3e85d158d1efc3d40'
ramses_pool = '0x1251ef3b87157b86f189bdea80b54673b0b59698'

amt = int(0.1 * 1e18)  # 0.1 WETH

# Test: Buy WETH on Pancake (cheap), sell on Uniswap (expensive)
print('=== BUY on Pancake, SELL on Uniswap ===')
usdce_for_weth_pancake = v3_layer.quote_v3(rpc, WETH, USDCE, amt, 500, from_addr, v3_layer.PANCAKE_QUOTER_V2)
print(f'Pancake: 0.1 WETH -> {usdce_for_weth_pancake/1e6:.4f} USDC.e')

weth_from_usdce_uniswap = v3_layer.quote_v3(rpc, USDCE, WETH, usdce_for_weth_pancake, 500, from_addr)
print(f'Uniswap: {usdce_for_weth_pancake/1e6:.4f} USDC.e -> {weth_from_usdce_uniswap/1e18:.6f} WETH')

profit = (weth_from_usdce_uniswap - amt) / 1e18
print(f'Profit: {profit:.6f} WETH = ${profit * 2500:.2f}')

print()
# Test: Buy on Uniswap, sell on Pancake
print('=== BUY on Uniswap, SELL on Pancake ===')
usdce_for_weth_uniswap = v3_layer.quote_v3(rpc, WETH, USDCE, amt, 500, from_addr)
print(f'Uniswap: 0.1 WETH -> {usdce_for_weth_uniswap/1e6:.4f} USDC.e')

weth_from_usdce_pancake = v3_layer.quote_v3(rpc, USDCE, WETH, usdce_for_weth_uniswap, 500, from_addr, v3_layer.PANCAKE_QUOTER_V2)
print(f'Pancake: {usdce_for_weth_uniswap/1e6:.4f} USDC.e -> {weth_from_usdce_pancake/1e18:.6f} WETH')

profit2 = (weth_from_usdce_pancake - amt) / 1e18
print(f'Profit: {profit2:.6f} WETH = ${profit2 * 2500:.2f}')