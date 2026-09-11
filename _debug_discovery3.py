#!/usr/bin/env python3
"""Debug: try discovery with RPC health monitor failover."""
import time
from core.rpc_health import RPCHealthMonitor
from core.pool import PoolRegistry
from core.market_discovery import MarketDiscovery

# Use the health monitor for failover
urls = [
    "https://gateway.tenderly.co/public/arbitrum",
    "https://arbitrum.drpc.org",
    "https://arbitrum.publicnode.com",
]
health = RPCHealthMonitor(urls)
reg = PoolRegistry()

# Try each endpoint
for url in urls:
    print(f"\nTrying {url}...")
    from core.rpc import RPC
    rpc = RPC(url, timeout=30, retries=1)
    d = MarketDiscovery(rpc, reg, 42161)
    
    try:
        pools = d.discover_pools(
            ['0x82aF49447D8a07e3bd95BD0d56f35241523fBab1',
             '0xaf88d065e77c8cC2239327C5EDb3A432268e5831'],
            venues=["uniswap_v3"],
            fee_tiers=[500],
        )
        print(f"Discovered: {len(pools)}")
        print(f"Stats: {d.stats()}")
        if pools:
            print(f"First pool: {pools[0].pool_id}")
            break
    except Exception as e:
        print(f"Error: {e}")
    
    time.sleep(1)  # Brief pause between endpoints
