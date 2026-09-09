#!/usr/bin/env python3
"""Test resilient RPC directly."""
from core.rpc_resilience import ResilientRPC, RPCCache

providers = [
    ("https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol", 0),
    ("https://gateway.tenderly.co/public/arbitrum", 1),
    ("https://arbitrum.drpc.org", 2),
]

rpc = ResilientRPC(providers, cache=RPCCache())

print("Testing resilient RPC...")
try:
    result = rpc.eth_call(
        "0xc6962004f452be9203591991d15f6b388e09e8d0",
        "0x3850c7bd"
    )
    print(f"Success! Result: {result[:60]}...")
except Exception as e:
    print(f"Failed: {e}")

print(f"\nHealth status:")
for h in rpc.health_status():
    print(f"  {h}")
