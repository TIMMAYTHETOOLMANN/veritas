#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flash_hunter import get_rpc, load_key
from eth_account import Account
import arb_engine

def main():
    rpc, _ = get_rpc()
    acct = Account.from_key(load_key())
    print(f"Using RPC: {rpc.url}")
    print(f"Account: {acct.address}")
    
    # Get ETH/USD price from Sushi (as in hunter)
    SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
    # Fallback ETH/USD from deepest WETH/USDC V2 pool.
    eth_usd_call = rpc.call("eth_call", [{
        "to": SUSHI_WETH_USDC,
        "data": "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0')
    }, "latest"])
    try:
        eth_usd_raw = int(eth_usd_call, 16)
        eth_usd = eth_usd_raw / 1e6  # USDC has 6 decimals
    except Exception as e:
        print(f"Failed to get ETH/USD from Sushi: {e}")
        eth_usd = 2450.0
    print(f"ETH/USD price from Sushi: {eth_usd}")
    
    # Gas price
    gas_wei = int(rpc.call("eth_gasPrice", []), 16)
    gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd  # 450k gas estimate
    print(f"Gas price: {gas_wei} wei, gas cost in USD: {gas_usd}")
    
    # Scan
    print("Scanning...")
    edges, report = arb_engine.scan_cross_venue(rpc, eth_usd, gas_usd,
                                                size_steps=12,
                                                max_venues_per_quote=8,
                                                use_multi_hop=True,
                                                use_parallel=True)
    print(f"Found {len(edges)} edges")
    if edges:
        for i, e in enumerate(edges[:5]):
            print(f"  Edge {i}: {e.get('venue_buy')} -> {e.get('venue_sell')} size={e.get('size_weth')} WETH net=${e.get('net_usd')} gross=${e.get('gross_usd')}")
    else:
        print("No edges found.")
        print("Report:", json.dumps(report, indent=2))

if __name__ == "__main__":
    import json
    main()