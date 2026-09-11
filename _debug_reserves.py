#!/usr/bin/env python3
"""Debug reserve fetching."""
from core.rpc import RPC
from core.pool import PoolRegistry, PoolId, PoolMetadata

rpc = RPC('https://gateway.tenderly.co/public/arbitrum', timeout=30, retries=2)

# Create a pool metadata for the known V3 pool
pool_id = PoolId(
    chain_id=42161,
    venue="uniswap_v3",
    factory="0x1f98431c8ad98523631ae4a59f267346ea31f984",
    pool_address="0xc6962004f452be9203591991d15f6b388e09e8d0",
)
pool = PoolMetadata(
    pool_id=pool_id,
    token0="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    token1="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    fee=500,
    kind="v3",
)

print(f"Pool: {pool.pool_id.pool_address}")

# Test liquidity() call
print("\nTesting liquidity() call...")
result = rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
print(f"Result: {result}")
if result and len(result) >= 66:
    liquidity = int(result[2:66], 16)
    print(f"Liquidity: {liquidity}")

# Test slot0() call (has sqrtPriceX96 and tick)
print("\nTesting slot0() call...")
result = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
print(f"Result: {result}")
if result and len(result) >= 66:
    sqrt_price_x96 = int(result[2:66], 16)
    tick = int(result[66:130], 16) if len(result) >= 130 else 0
    print(f"sqrtPriceX96: {sqrt_price_x96}, tick: {tick}")
