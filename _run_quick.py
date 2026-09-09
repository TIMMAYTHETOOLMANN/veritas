#!/usr/bin/env python3
"""Quick scan with reduced discovery."""
from core.market_discovery import MarketDiscovery
from core.external_apis import AlchemyRPC
from core.pool import PoolRegistry
import time

# Test discovery directly
rpc = AlchemyRPC()
reg = PoolRegistry()

discovery = MarketDiscovery(rpc, reg, 42161)

# Discover with minimal scope
tokens = [
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
]

start = time.time()
discovered = discovery.discover_pools(tokens, venues=["uniswap_v3"], fee_tiers=[500])
print(f"Discovery: {len(discovered)} pools in {time.time() - start:.1f}s")
print(f"Stats: {discovery.stats()}")
