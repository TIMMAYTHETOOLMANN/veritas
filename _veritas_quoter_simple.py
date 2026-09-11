#!/usr/bin/env python3
"""Simple direct test of pool swap."""
from core.external_apis import AlchemyRPC

rpc = AlchemyRPC()

pool = "0xc6962004f452be9203591991d15f6b388e09e8d0"
weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

# Build swap calldata manually
# swap(address recipient, bool zeroForOne, int256 amountSpecified, uint160 sqrtPriceLimitX96, bytes data)
# Selector for swap(address,bool,int256,uint160,bytes)
from eth_utils import keccak
selector = keccak(text="swap(address,bool,int256,uint160,bytes)")[:4].hex()
print(f"Swap selector: {selector}")

# Encode parameters
recipient = "0x0000000000000000000000000000000000000001"
zeroForOne = "0" * 63 + "1"  # true
amount = format(10**16, "064x")  # 0.01 WETH
sqrt_limit = "0" * 64  # 0
data_offset = "0" * * 63 + "60"  # offset to data (96 bytes)
data_length = "0" * 64  # 0 bytes
data = ""

calldata = "0x" + selector + recipient + zeroForOne + amount + sqrt_limit + data_offset + data_length + data

print(f"Calldata: {calldata[:80]}...")
print(f"Calldata length: {len(calldata)}")

try:
    result = rpc.eth_call(pool, calldata)
    print(f"Result: {result[:80]}...")
except Exception as e:
    print(f"Error: {e}")
