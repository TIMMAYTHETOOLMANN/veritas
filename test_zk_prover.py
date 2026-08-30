#!/usr/bin/env python3
"""
Test the ZK prover with a dummy edge.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zk_prover import ZKProver
from core.rpc import RPC

def main():
    # Use a public RPC endpoint for Arbitrum
    rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=10, retries=2)
    print("Testing RPC connection...")
    try:
        block = rpc.call("eth_blockNumber", [])
        print(f"Connected to Arbitrum, block number: {block}")
    except Exception as e:
        print(f"RPC connection failed: {e}")
        return

    # Initialize the prover
    prover = ZKProver(rpc)

    # Create a dummy edge (this is just for testing the proof generation)
    # We need to pick two pools that exist in the registry.
    # Let's query the registry for a couple of pools.
    import sqlite3
    db_path = os.path.join(os.getcwd(), 'veritas.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT address, token0, token1, reserve0, reserve1 FROM pools WHERE kind='v2' AND reserve0>0 AND reserve1>0 LIMIT 2")
    pools = cursor.fetchall()
    conn.close()
    if len(pools) < 2:
        print("Not enough pools with liquidity in the registry.")
        return

    pool_a = pools[0]
    pool_b = pools[1]
    print(f"Pool A: {pool_a[0]} (token0={pool_a[1]}, token1={pool_a[2]}, reserve0={pool_a[3]}, reserve1={pool_a[4]})")
    print(f"Pool B: {pool_b[0]} (token0={pool_b[1]}, token1={pool_b[2]}, reserve0={pool_b[3]}, reserve1={pool_b[4]})")

    # Create a dummy edge
    edge = {
        "buy_venue": pool_a[0],
        "sell_venue": pool_b[0],
        "size_weth": 0.1,  # 0.1 WETH
        "buy_fee": 30,     # 0.3%
        "sell_fee": 30,    # 0.3%
        "quote": "0xAff880e1d0fEc3440FB32f9d4CC9c8a3c475Caa65e",  # USDC on Arbitrum
    }

    # We also need to know the token decimals for the quote token (USDC is 6)
    # But the prover will fetch the pool state and compute the amounts.

    # Generate a proof
    print("\nGenerating ZK proof...")
    try:
        proof = prover.generate_proof(edge, eth_usd=2450.0, gas_usd=0.005)
        if proof:
            print("Proof generated successfully!")
            print(f"Profit (USD): {proof['profit_usd']:.4f}")
            print(f"Net profit (USD): {proof['net_profit_usd']:.4f}")
            print(f"Nullifier: {proof['nullifier'].hex() if isinstance(proof['nullifier'], bytes) else proof['nullifier']}")
        else:
            print("Proof generation failed.")
    except Exception as e:
        print(f"Error during proof generation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()