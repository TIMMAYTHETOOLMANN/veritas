#!/usr/bin/env python3
"""Test the correct QuoterV2 selector."""
from core.external_apis import AlchemyRPC

rpc = AlchemyRPC()
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
SELECTOR = "b3b11b7e"  # Correct selector

weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
amount_in = 10**18

for fee in [500, 3000, 10000]:
    t_in = weth.lower().replace("0x", "").rjust(64, "0")
    t_out = usdc.lower().replace("0x", "").rjust(64, "0")
    fee_hex = format(fee, "064x")
    amt_hex = format(amount_in, "064x")
    sqrt_limit = "0" * 64
    
    data = "0x" + SELECTOR + t_in + t_out + fee_hex + amt_hex + sqrt_limit
    
    print(f"\nFee={fee}: Data={data[:60]}...")
    try:
        result = rpc.eth_call(QUOTER_V2, data)
        print(f"Result: {result[:80] if result else 'None'}...")
        if result and len(result) >= 66:
            amount_out = int(result[2:66], 16)
            print(f"Amount out: {amount_out}")
            if amount_out > 0:
                price = amount_out / 10**6  # USDC has 6 decimals
                print(f"ETH price: ${price:.2f}")
    except Exception as e:
        print(f"Error: {e}")
