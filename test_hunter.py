#!/usr/bin/env python3
"""
Test the hunter with a very low safety margin and reduced scan parameters.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import arb_engine and override the safety margin
import arb_engine
original_safety = arb_engine.SAFETY_MARGIN_USD
arb_engine.SAFETY_MARGIN_USD = 0.001  # $0.001 net profit required
print(f"Set SAFETY_MARGIN_USD from {original_safety} to {arb_engine.SAFETY_MARGIN_USD}")

# Now import the hunter
from flash_hunter import hunt_once
from core.rpc import RPC

def main():
    # Use a public RPC endpoint for Arbitrum
    rpc_url = "https://arb1.arbitrum.io/rpc"
    rpc = RPC(rpc_url, timeout=10, retries=2)
    print("Testing RPC connection...")
    try:
        block = rpc.call("eth_blockNumber", [])
        print(f"Connected to Arbitrum, block number: {block}")
    except Exception as e:
        print(f"RPC connection failed: {e}")
        return

    # We need an account to sign transactions, but for a dry run we can just use a dummy account.
    from eth_account import Account
    acct = Account.create()
    print(f"Using account: {acct.address}")

    # We need an executor address. We can use the one that was deployed earlier or a dummy.
    executor_addr = "0x91761f714dc18cb1242a7c3b540ef2bb1e717cc2"  # from the log
    if not os.path.exists('.executor_address'):
        pass
    else:
        with open('.executor_address', 'r') as f:
            executor_addr = f.read().strip()
    print(f"Using executor address: {executor_addr}")

    # We also need an RPC for scanning (can be the same as the broadcast RPC)
    # We'll pass the URL string for rpc_scan, as the hunt_once function expects a string to create a new RPC object.
    rpc_scan_url = rpc_url

    # Now run one hunt cycle with verbose=True to see what's happening
    print("\nRunning one hunt cycle (scan -> ZK-proof gate -> broadcast)...")
    try:
        result = hunt_once(rpc, acct, executor_addr, rpc_scan_url, verbose=True)
        print(f"\nHunt result: {result}")
    except Exception as e:
        print(f"\nError during hunt: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore the original safety margin
        arb_engine.SAFETY_MARGIN_USD = original_safety
        print(f"Restored SAFETY_MARGIN_USD to {original_safety}")

if __name__ == '__main__':
    main()