#!/usr/bin/env python3
"""Test V3 quote directly."""
from core.external_apis import AlchemyRPC
from core.quote_engine_v2 import QuoteEngine
from core.pool import PoolId, PoolMetadata

rpc = AlchemyRPC()
engine = QuoteEngine(rpc)

# Create the pool from DB
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
    decimals0=18,
    decimals1=6,
    fee=500,
    kind="v3",
    reserve0=1,
    reserve1=3444822661463129177,
)

weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

# Test WETH -> USDC
print("Testing WETH -> USDC...")
result = engine.quote(weth, usdc, 10**16, pool)
print(f"Success: {result.success}")
print(f"Amount out: {result.amount_out}")
print(f"Error: {result.error}")

# Test USDC -> WETH
print("\nTesting USDC -> WETH...")
result = engine.quote(usdc, weth, 10**6, pool)  # 1 USDC
print(f"Success: {result.success}")
print(f"Amount out: {result.amount_out}")
print(f"Error: {result.error}")
