#!/usr/bin/env python3
"""Test individual RPC endpoints."""
from core.rpc import RPC

urls = [
    "https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol",
    "https://gateway.tenderly.co/public/arbitrum",
    "https://arbitrum.drpc.org",
]

pool = "0xc6962004f452be9203591991d15f6b388e09e8d0"

for url in urls:
    print(f"\nTesting {url[:50]}...")
    rpc = RPC(url, timeout=30, retries=1)
    try:
        result = rpc.eth_call(pool, "0x3850c7bd")
        if result and len(result) >= 66:
            sqrt_price_x96 = int(result[2:66], 16)
            print(f"  SUCCESS! sqrtPriceX96: {sqrt_price_x96}")
        else:
            print(f"  Failed: short result")
    except Exception as e:
        print(f"  Error: {str(e)[:100]}")
