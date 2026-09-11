#!/usr/bin/env python3
"""Find pools with active liquidity."""
from core.external_apis import AlchemyRPC
from v3_layer import census_v3, quote_v3

rpc = AlchemyRPC()

print("Scanning for pools with liquidity...")
pools = census_v3(rpc)

live_pools = [p for p in pools if p["live"]]
print(f"Found {len(live_pools)} live pools out of {len(pools)} total")

for p in live_pools[:5]:
    print(f"\nPool: {p['pool']}")
    print(f"  Base: {p['base']}, Quote: {p['quote']}, Fee: {p['fee']}")
    print(f"  Liquidity: {p['liquidity']}")
    
    # Try to quote
    try:
        result = quote_v3(rpc, p['base'], p['quote'], 10**16, p['fee'], p['pool'])
        if result:
            print(f"  Quote result: {result}")
        else:
            print(f"  Quote: None (reverted)")
    except Exception as e:
        print(f"  Quote error: {e}")
