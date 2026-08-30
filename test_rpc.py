#!/usr/bin/env python3
"""
Test basic RPC calls and pool state for one WETH-paired pool.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.rpc import RPC

def main():
    # Try a few RPC endpoints
    endpoints = [
        "https://arb1.arbitrum.io/rpc",
        "https://endpoints.omniatech.io/v1/arbitrum/one/public",
        "https://rpc.ankr.com/arbitrum",
    ]
    rpc = None
    for endpoint in endpoints:
        print(f"Trying RPC endpoint: {endpoint}")
        try:
            rpc_test = RPC(endpoint, timeout=10, retries=2)
            block = rpc_test.call("eth_blockNumber", [])
            print(f"  Success! Block number: {block}")
            rpc = rpc_test
            break
        except Exception as e:
            print(f"  Failed: {e}")
    if rpc is None:
        print("All RPC endpoints failed.")
        return

    # Get the WETH address on Arbitrum
    WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    USDC = "0xAff880e1d0fEc3440FB32f9d4CC9c8a3c475Caa65e"  # USDC on Arbitrum

    # Get the reserves for the Uniswap V2 WETH/USDC pool (if it exists in the registry)
    # We know from the registry that there is a V2 pool for WETH/USDC? Let's check.
    import sqlite3
    db_path = os.path.join(os.getcwd(), 'veritas.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT address, reserve0, reserve1 FROM pools
        WHERE (token0 = ? AND token1 = ?) OR (token0 = ? AND token1 = ?)
        AND kind = 'v2'
    """, (WETH, USDC, USDC, WETH))
    row = cursor.fetchone()
    conn.close()
    if row:
        pool_addr, reserve0, reserve1 = row
        print(f"Found V2 pool: {pool_addr}")
        print(f"Reserve0: {reserve0}, Reserve1: {reserve1}")
        # Now we need to know which token is which.
        # We can get token0 and token1 from the pool.
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT token0, token1 FROM pools WHERE address = ?
        """, (pool_addr,))
        token0, token1 = cursor.fetchone()
        conn.close()
        print(f"Token0: {token0}")
        print(f"Token1: {token1}")
        # Now we can compute the mid price if we have both reserves.
        if reserve0 is not None and reserve1 is not None:
            # We need to know the decimals for each token.
            # For simplicity, we know WETH is 18, USDC is 6.
            # But let's get them from the registry or assume.
            # We'll assume: if token0 is WETH, then reserve0 is WETH, else reserve1 is WETH.
            if token0.lower() == WETH.lower():
                weth_reserve = reserve0
                usdc_reserve = reserve1
            else:
                weth_reserve = reserve1
                usdc_reserve = reserve0
            print(f"WETH reserve: {weth_reserve}")
            print(f"USDC reserve: {usdc_reserve}")
            # Price of USDC in WETH: usdc_reserve / weth_reserve (adjusted for decimals)
            # Actually, the reserve values are in wei (smallest unit) for each token.
            # So we need to convert to human units.
            weth_decimals = 18
            usdc_decimals = 6
            weth_human = weth_reserve / (10 ** weth_decimals)
            usdc_human = usdc_reserve / (10 ** usdc_decimals)
            print(f"WETH human: {weth_human}")
            print(f"USDC human: {usdc_human}")
            if weth_human > 0:
                price_usdc_in_weth = usdc_human / weth_human
                price_weth_in_usdc = weth_human / usdc_human if usdc_human > 0 else 0
                print(f"Price of 1 USDC in WETH: {price_usdc_in_weth}")
                print(f"Price of 1 WETH in USDC: {price_weth_in_usdc}")
    else:
        print("No V2 WETH/USDC pool found in the registry.")
        # Let's just pick any V2 pool and try to get its state.
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT address, token0, token1 FROM pools WHERE kind = 'v2' LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
        if row:
            pool_addr, token0, token1 = row
            print(f"Using first V2 pool: {pool_addr} ({token0} / {token1})")
            # Now get the reserves from the blockchain.
            try:
                # We need to call the pool's reserves function.
                # The ABI for reserves is: function reserves() returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
                # We'll use the eth_call.
                import eth_abi
                # But we don't have eth_abi installed? We can try to use the one from web3 if available, but we are in a sandbox.
                # Instead, we can use the RPC to call the function with the correct data.
                # The function selector for reserves() is 0x0902f1ac
                data = "0x0902f1ac"
                result = rpc.eth_call(pool_addr, data)
                if result and result.startswith("0x") and len(result) >= 130:
                    # Parse the result: reserve0 (32 bytes), reserve1 (32 bytes), blockTimestampLast (32 bytes)
                    reserve0_hex = result[2:66]
                    reserve1_hex = result[66:130]
                    reserve0 = int(reserve0_hex, 16)
                    reserve1 = int(reserve1_hex, 16)
                    print(f"Reserve0 (raw): {reserve0}")
                    print(f"Reserve1 (raw): {reserve1}")
                    # Now we need to know the token addresses and their decimals.
                    # We already have token0 and token1 from the registry.
                    # We need to get the decimals for each token.
                    # We can try to call the decimals() function on each token contract.
                    # But for now, let's assume we know them from the registry or we can skip.
                    # We'll just print the raw reserves.
                else:
                    print(f"Unexpected result from reserves call: {result}")
            except Exception as e:
                print(f"Error calling reserves on pool: {e}")
        else:
            print("No pools found in registry.")

    # Now let's try to get the price from a V3 pool using the quoter if we can.
    # We'll skip for now because it's more complex.

    print("\nDone.")

if __name__ == '__main__':
    main()