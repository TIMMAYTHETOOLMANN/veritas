#!/usr/bin/env python3
"""Debug discover_and_refresh."""
from core.rpc import RPC
from core.pool import PoolRegistry
from core.market_discovery import MarketDiscovery

rpc = RPC('https://gateway.tenderly.co/public/arbitrum', timeout=30, retries=2)
reg = PoolRegistry()
d = MarketDiscovery(rpc, reg, 42161)

tokens = [
    '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
    '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
]

print("Testing discover_and_refresh...")
discovered = d.discover_and_refresh(
    tokens,
    venues=["uniswap_v3"],
    fee_tiers=[500],
)
print(f"Discovered with reserves: {len(discovered)}")
print(f"Stats: {d.stats()}")
if discovered:
    p = discovered[0]
    print(f"Pool: {p.pool_id}")
    print(f"Reserve0: {p.reserve0}, Reserve1: {p.reserve1}, Liquidity: {p.liquidity}")
