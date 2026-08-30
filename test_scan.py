#!/usr/bin/env python3
"""
Test scan with reduced safety margin to see if any arbitrage opportunities exist.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# We need to import the arb_engine but we want to temporarily change SAFETY_MARGIN_USD
# Let's import the module and then modify its constant.
import arb_engine
from core.rpc import RPC

# Override the safety margin to 0 for this test
arb_engine.SAFETY_MARGIN_USD = 0.0
print(f"Setting SAFETY_MARGIN_USD to {arb_engine.SAFETY_MARGIN_USD} for test")

def main():
    rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=10, retries=2)
    print("Testing RPC connection...")
    try:
        block = rpc.call("eth_blockNumber", [])
        print(f"Connected to Arbitrum, block number: {block}")
    except Exception as e:
        print(f"RPC connection failed: {e}")
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

    # Run the scan with parameters that are likely to finish quickly
    # We'll reduce the size_steps and max_venues_per_quote to make it faster.
    print("\nScanning for arbitrage opportunities (with safety margin = 0)...")
    try:
        edges, report = arb_engine.scan_cross_venue(
            rpc, 
            eth_usd, 
            gas_usd, 
            size_steps=6,          # reduced from 12
            max_venues_per_quote=4, # reduced from 8
            use_multi_hop=False, 
            use_parallel=True
        )
        print(f"\nScan complete. Found {len(edges)} edges with net profit > safety margin (which is 0).")
        print(f"Total combinations checked: {len(report)}")
        if edges:
            print("\nTop 10 edges by net profit:")
            for i, edge in enumerate(edges[:10]):
                print(f"{i+1}. {edge.get('pair', 'Unknown')} | {edge.get('venue_buy')} -> {edge.get('venue_sell')} | "
                      f"Size: {edge.get('size_weth')} WETH | "
                      f"Net: ${edge.get('net_usd', 0):.4f} | "
                      f"Gross: ${edge.get('gross_usd', 0):.4f} | "
                      f"Gas: ${edge.get('gas_usd', 0):.4f} | "
                      f"Loan fee: ${edge.get('loan_fee_usd', 0):.4f}")
        else:
            print("\nNo edges found with net profit > 0.")
            # Let's check the report to see if any combinations had positive profit before gas and fees?
            # The report contains all combinations, including those that were not profitable.
            # We can scan the report for any row that has a positive 'net_usd' (but note: net_usd is after gas and loan fee? 
            # In the scan function, net_usd is computed as: profit * eth_usd - gas_usd - loan_fee_usd
            # So if net_usd > 0, then it's profitable after gas and loan fee.
            # We'll just check the first few rows of the report to see what the numbers look like.
            print("\nChecking first 5 rows of the report for any positive profit:")
            for i, row in enumerate(report[:5]):
                # The report rows have the same structure as edges, but without the 'edge' flag.
                net = row.get('net_usd', 0)
                print(f"  Row {i+1}: {row.get('pair', 'Unknown')} | {row.get('venue_buy')} -> {row.get('venue_sell')} | net: ${net:.4f}")
    except Exception as e:
        print(f"Error during scan: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()