#!/usr/bin/env python3
"""Debug discovery with Alchemy RPC."""
from core.external_apis import AlchemyRPC
from core.pool import PoolRegistry
from core.market_discovery import MarketDiscovery

# Use Alchemy directly
rpc = AlchemyRPC()
reg = PoolRegistry()
d = MarketDiscovery(rpc, reg, 42161)

tokens = [
    '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
    '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
]

print("Testing discover_and_refresh with Alchemy...")
discovered = d.discover_and_refresh(
    tokens,
    venues=["uniswap_v3"],
    fee_tiers=[500],
)
print(f"Discovered: {len(discovered)}")
print(f"Stats: {d.stats()}")
if discovered:
    p = discovered[0]
    print(f"Pool: {p.pool_id}")
    print(f"Reserve0: {p.reserve0}, Reserve1: {p.reserve1}, Liquidity: {p.liquidity}")
