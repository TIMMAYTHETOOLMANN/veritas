#!/usr/bin/env python3
"""
Ultra-quick test of the arbitrage scan with minimal RPC calls.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.rpc import RPC
import arb_engine

def main():
    # Use a public RPC endpoint for Arbitrum with a short timeout
    rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=5, retries=1)
    print("Testing RPC connection with 5s timeout...")
    try:
        block = rpc.call("eth_blockNumber", [])
        print(f"Connected to Arbitrum, block number: {block}")
    except Exception as e:
        print(f"RPC connection failed: {e}")
        print("Trying a different endpoint...")
        # Try a different RPC endpoint
        rpc = RPC("https://endpoints.omniatech.io/v1/arbitrum/one/public", timeout=5, retries=1)
        try:
            block = rpc.call("eth_blockNumber", [])
            print(f"Connected via Omniatech, block number: {block}")
        except Exception as e2:
            print(f"Both RPC endpoints failed: {e2}")
            return

    # Get ETH price and gas price
    try:
        eth_usd = 2450.0  # fallback
        try:
            import v3_layer
            out = v3_layer.quote_v3(rpc, v3_layer.WETH, v3_layer.USDC, 10**18, 500, "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f")
            eth_usd = out / 1e6 if out else 2450.0
            print(f"ETH price from v3_layer: ${eth_usd}")
        except Exception as e:
            print(f"Could not get ETH price from v3_layer: {e}")
            print("Using fallback ETH price: $2450.0")
    except Exception as e:
        print(f"Error getting ETH price: {e}")
        eth_usd = 2450.0

    try:
        gas_wei = rpc.call("eth_gasPrice", [])
        gas_usd = (int(gas_wei) * 450_000 / 1e18) * eth_usd
        print(f"Gas price: {gas_wei} wei, ${gas_usd:.6f} per tx")
    except Exception as e:
        print(f"Error getting gas price: {e}")
        gas_usd = 0.005  # fallback

    print(f"ETH price: ${eth_usd}")
    print(f"Gas cost estimate: ${gas_usd}")

    # Now run the scan with ABSOLUTELY MINIMAL parameters
    print("\nScanning for arbitrage opportunities (ultra-quick test)...")
    try:
        # We will only look at one quote token (USDC) and only two venues (one V2, one V3)
        # We'll override the scan_cross_venue function to use a very small subset.
        # But instead, we can just set the parameters to be very small and hope it finishes quickly.
        edges, report = arb_engine.scan_cross_venue(
            rpc, 
            eth_usd, 
            gas_usd, 
            size_steps=2,          # only 2 sizes
            max_venues_per_quote=2, # only 2 venues per quote
            use_multi_hop=False, 
            use_parallel=False    # single threaded to avoid issues
        )
        print(f"\nScan complete. Found {len(edges)} profitable edges (net > safety margin).")
        print(f"Total combinations checked: {len(report)}")
        if edges:
            print("\nEdges found:")
            for i, edge in enumerate(edges):
                print(f"{i+1}. {edge.get('pair', 'Unknown')} | {edge.get('venue_buy')} -> {edge.get('venue_sell')} | "
                      f"Size: {edge.get('size_weth')} WETH | "
                      f"Net: ${edge.get('net_usd', 0):.4f}")
        else:
            print("\nNo profitable edges found.")
    except Exception as e:
        print(f"Error during scan: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()