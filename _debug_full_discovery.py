#!/usr/bin/env python3
"""Debug full discovery with all venues."""
from core.rpc import RPC
from core.pool import PoolRegistry
from core.market_discovery import MarketDiscovery

# Use Tenderly (which works)
rpc = RPC('https://gateway.tenderly.co/public/arbitrum', timeout=30, retries=2)
reg = PoolRegistry()
d = MarketDiscovery(rpc, reg, 42161)

tokens = [
    '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',  # WETH
    '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',  # USDC
    '0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8',  # USDC.e
    '0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f',  # WBTC
    '0x912CE59144191C1204E64559FE8253a0e49E6548',  # ARB
]

print("Starting full discovery with all venues...")
discovered = d.discover_pools(
    tokens,
    venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],
)
stats = d.stats()
print(f"Discovered: {len(discovered)}")
print(f"Stats: {stats}")
if discovered:
    for p in discovered[:5]:
        print(f"  {p.pool_id.venue}: {p.token0[:6]}/{p.token1[:6]} fee={p.fee} -> {p.pool_id.pool_address[:20]}...")
