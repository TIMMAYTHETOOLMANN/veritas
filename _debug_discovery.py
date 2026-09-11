#!/usr/bin/env python3
"""Debug the discovery process."""
import traceback
from core.rpc import RPC
from core.pool import PoolRegistry, PoolId

rpc = RPC('https://arbitrum.drpc.org', timeout=30, retries=2)

# Test getPool call for WETH/USDC 0.05% on Uniswap V3
factory = '0x1f98431c8ad98523631ae4a59f267346ea31f984'
token_a = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'  # WETH
token_b = '0xaf88d065e77c8cC2239327C5EDb3A432268e5831'  # USDC
fee = 500

# Build the call data manually
selector = '1698ee82'
padded_a = token_a.lower().replace('0x', '').rjust(64, '0')
padded_b = token_b.lower().replace('0x', '').rjust(64, '0')
padded_fee = f"{fee:064x}"
data = '0x' + selector + padded_a + padded_b + padded_fee

print(f"Factory: {factory}")
print(f"Data length: {len(data)}")
print(f"Data: {data[:80]}...")

try:
    result = rpc.eth_call(factory, data)
    print(f"Result: {result}")
    if result and len(result) >= 66:
        addr = '0x' + result[2:][-40:]
        print(f"Pool address: {addr}")
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
