#!/usr/bin/env python3
"""Debug the discovery failover."""
import pathlib
from core.rpc_health import RPCHealthMonitor
from core.pool import PoolRegistry
from core.market_discovery import MarketDiscovery

urls = [
    "https://gateway.tenderly.co/public/arbitrum",
    "https://arbitrum.drpc.org",
    "https://arbitrum.publicnode.com",
]

health = RPCHealthMonitor(urls)
reg = PoolRegistry()

print(f"Endpoints: {list(health.endpoints.keys())}")
print(f"Healthy URLs: {health.get_healthy_urls()}")

# Try each endpoint
tried_urls = set()
for i in range(len(health.endpoints)):
    rpc = health.get_healthy_rpc()
    print(f"\nIteration {i}: got RPC for {rpc.url}")
    if rpc.url in tried_urls:
        print(f"  Already tried {rpc.url}, skipping")
        continue
    tried_urls.add(rpc.url)
    
    d = MarketDiscovery(rpc, reg, 42161)
    discovered = d.discover_pools(
        ['0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
         '0xaf88d065e77c8cC2239327C5EDb3A432268e5831'],
        venues=["uniswap_v3"],
        fee_tiers=[500],
    )
    stats = d.stats()
    print(f"  Discovered: {len(discovered)}, Stats: {stats}")
    
    if discovered:
        print(f"  SUCCESS!")
        break
    else:
        # Record error and try next
        health.record_error(rpc.url, "rate limit")
        print(f"  Recorded error, trying next...")
