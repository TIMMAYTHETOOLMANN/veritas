#!/usr/bin/env python3
"""Debug V3 quote calculation."""
from core.external_apis import AlchemyRPC
from core.rpc_resilience import ResilientRPC, RPCCache
from core.quote_engine_v2 import QuoteEngine
from core.pool import PoolId, PoolMetadata

# Create resilient RPC
providers = [
    ("https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol", 0),
    ("https://gateway.tenderly.co/public/arbitrum", 1),
    ("https://arbitrum.drpc.org", 2),
]
resilient_rpc = ResilientRPC(providers, cache=RPCCache())

# Also create a regular RPC for the engine
rpc = AlchemyRPC()
engine = QuoteEngine(rpc, resilient_rpc)

# Create pool
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
)

weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

# Test WETH -> USDC
print("Testing WETH -> USDC...")
result = engine.quote(weth, usdc, 10**16, pool)
print(f"Success: {result.success}")
print(f"Amount out: {result.amount_out}")
print(f"Error: {result.error}")

# Test with direct RPC
print("\nTesting with direct RPC...")
engine2 = QuoteEngine(rpc)
result2 = engine2.quote(weth, usdc, 10**16, pool)
print(f"Success: {result2.success}")
print(f"Amount out: {result2.amount_out}")
print(f"Error: {result2.error}")

# Check resilient RPC health
print(f"\nResilient RPC health:")
for h in resilient_rpc.health_status():
    print(f"  {h}")
