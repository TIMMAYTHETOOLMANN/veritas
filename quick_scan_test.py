#!/usr/bin/env python3
"""
Quick test of RPC connectivity and a minimal scan.
"""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.rpc import RPC
import arb_engine

def test_rpc(endpoint):
    print(f"Testing RPC endpoint: {endpoint}")
    try:
        rpc = RPC(endpoint, timeout=5, retries=1)
        start = time.time()
        block = rpc.call("eth_blockNumber", [])
        elapsed = time.time() - start
        print(f"  Success! Block number: {block} (took {elapsed:.2f}s)")
        return rpc
    except Exception as e:
        print(f"  Failed: {e}")
        return None

def main():
    endpoints = [
        "https://arb1.arbitrum.io/rpc",
        "https://endpoints.omniatech.io/v1/arbitrum/one/public",
        "https://rpc.ankr.com/arbitrum",
    ]
    rpc = None
    for ep in endpoints:
        rpc = test_rpc(ep)
        if rpc is not None:
            break
    if rpc is None:
        print("All RPC endpoints failed.")
        return

    # Get ETH price and gas price with fallbacks to avoid extra RPC calls if possible
    eth_usd = 2450.0
    try:
        # Try to get from v3_layer if available, but we'll skip if it causes delay
        import v3_layer
        out = v3_layer.quote_v3(rpc, v3_layer.WETH, v3_layer.USDC, 10**18, 500, "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f")
        eth_usd = out / 1e6 if out else 2450.0
        print(f"ETH price from v3_layer: ${eth_usd}")
    except Exception as e:
        print(f"Could not get ETH price from v3_layer: {e}")
        print("Using fallback ETH price: $2450.0")

    try:
        gas_wei = rpc.call("eth_gasPrice", [])
        gas_usd = (int(gas_wei) * 450_000 / 1e18) * eth_usd
        print(f"Gas price: {gas_wei} wei, ${gas_usd:.6f} per tx")
    except Exception as e:
        print(f"Error getting gas price: {e}")
        gas_usd = 0.005  # fallback

    print(f"ETH price: ${eth_usd}")
    print(f"Gas cost estimate: ${gas_usd}")

    # Now run the scan with minimal parameters
    print("\nScanning for arbitrage opportunities (minimal parameters)...")
    start = time.time()
    try:
        edges, report = arb_engine.scan_cross_venue(
            rpc, 
            eth_usd, 
            gas_usd, 
            size_steps=2,          # only 2 sizes
            max_venues_per_quote=2, # only 2 venues per quote
            use_multi_hop=False, 
            use_parallel=False    # single threaded to avoid issues
        )
        elapsed = time.time() - start
        print(f"\nScan complete. Found {len(edges)} profitable edges (net > safety margin).")
        print(f"Total combinations checked: {len(report)}")
        print(f"Scan took {elapsed:.2f} seconds.")
        if edges:
            print("\nTop 5 edges by net profit:")
            for i, edge in enumerate(edges[:5]):
                print(f"{i+1}. {edge.get('pair', 'Unknown')} | {edge.get('venue_buy')} -> {edge.get('venue_sell')} | "
                      f"Size: {edge.get('size_weth')} WETH | "
                      f"Net: ${edge.get('net_usd', 0):.4f} | "
                      f"Gross: ${edge.get('gross_usd', 0):.4f} | "
                      f"Gas: ${edge.get('gas_usd', 0):.4f} | "
                      f"Loan fee: ${edge.get('loan_fee_usd', 0):.4f}")
        else:
            print("\nNo profitable edges found.")
    except Exception as e:
        elapsed = time.time() - start
        print(f"\nError during scan after {elapsed:.2f} seconds: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()