#!/usr/bin/env python3
"""Debug MarketDiscovery step by step."""
import traceback
from core.rpc import RPC
from core.pool import PoolRegistry
from core.market_discovery import MarketDiscovery, _parse_pool_addr, FACTORIES, _pad_addr, _u256

rpc = RPC('https://arbitrum.drpc.org', timeout=30, retries=2)
reg = PoolRegistry()
d = MarketDiscovery(rpc, reg, 42161)

# Manually test the V3 pool query
venue = "uniswap_v3"
factory = FACTORIES[venue]
token_a = '0x82aF49447D8a07e3bd95BD0d56f35241523fBab1'
token_b = '0xaf88d065e77c8cC2239327C5EDb3A432268e5831'
fee = 500

print(f"Factory: {factory}")
print(f"Selector: {factory['selector']}")

# Build the call data
data = (
    "0x" + factory["selector"]
    + _pad_addr(token_a) + _pad_addr(token_b) + _u256(fee)
)
print(f"Data: {data[:80]}...")

try:
    result = rpc.eth_call(factory["address"], data)
    print(f"Result: {result}")
    
    pool_addr = _parse_pool_addr(result)
    print(f"Parsed pool address: {pool_addr}")
    
    if pool_addr:
        # Check if already registered
        existing = reg.get_by_address(42161, pool_addr)
        print(f"Already registered: {existing is not None}")
        
        if not existing:
            print("Would register new pool!")
        else:
            print(f"Pool already exists: {existing.pool_id}")
    else:
        print("No pool address parsed (returned 0x000...)")
        
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
