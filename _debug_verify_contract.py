#!/usr/bin/env python3
"""Verify QuoterV2 contract exists and has code."""
from core.external_apis import AlchemyRPC

rpc = AlchemyRPC()

# Check if QuoterV2 contract exists
quoter = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
try:
    # Get code at the address
    result = rpc._call("eth_getCode", [quoter, "latest"])
    if result and result != "0x":
        print(f"Contract exists at {quoter}")
        print(f"Bytecode length: {len(result)} chars")
    else:
        print(f"No contract at {quoter}")
except Exception as e:
    print(f"Error: {e}")

# Also check a known pool address
pool = "0xc6962004f452be9203591991d15f6b388e09e8d0"
try:
    result = rpc._call("eth_getCode", [pool, "latest"])
    if result and result != "0x":
        print(f"\nPool contract exists at {pool}")
        print(f"Bytecode length: {len(result)} chars")
    else:
        print(f"\nNo contract at {pool}")
except Exception as e:
    print(f"Error: {e}")

# Check factory contract
factory = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
try:
    result = rpc._call("eth_getCode", [factory, "latest"])
    if result and result != "0x":
        print(f"\nFactory contract exists at {factory}")
        print(f"Bytecode length: {len(result)} chars")
    else:
        print(f"\nNo contract at {factory}")
except Exception as e:
    print(f"Error: {e}")
