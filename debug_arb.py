#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flash_hunter import get_rpc, load_key
from eth_account import Account
import arb_engine
import json

def main():
    rpc, _ = get_rpc()
    acct = Account.from_key(load_key())
    print(f"Using RPC: {rpc.url}")
    print(f"Account: {acct.address}")
    
    # Get ETH/USD price from Sushi using getReserves
    SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
    pair_abi = [{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"reserve0","type":"uint112"},{"name":"reserve1","type":"uint112"},{"name":"blockTimestampLast","type":"uint32"}],"type":"function"}]
    pair_contract = rpc.eth.contract(address=SUSHI_WETH_USDC, abi=pair_abi)
    try:
        reserves = pair_contract.functions.getReserves().call()
        weth_res = reserves[0]
        usdc_res = reserves[1]
        if weth_res == 0:
            eth_usd = 2450.0
        else:
            eth_usd = (usdc_res / 1e6) / (weth_res / 1e18)  # USDC 6 decimals, WETH 18
        print(f"Sushi reserves: WETH={weth_res}, USDC={usdc_res}")
        print(f"ETH/USD price: {eth_usd}")
    except Exception as e:
        print(f"Failed to get reserves: {e}")
        eth_usd = 2450.0
    
    # Gas price
    gas_wei = int(rpc.call("eth_gasPrice", []), 16)
    gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd  # 450k gas estimate
    print(f"Gas price: {gas_wei} wei, gas cost in USD: {gas_usd}")
    
    # Now let's manually check the registry to see what pools are loaded
    from core.registry import Registry
    reg = Registry()
    print(f"Registry loaded: {len(reg.pools)} pools")
    # Print first few pools
    for i, (addr, pool) in enumerate(list(reg.pools.items())[:5]):
        print(f"  Pool {i}: {addr} tokens {pool.token0}, {pool.token1} reserves {pool.reserve0}, {pool.reserve1}")
    
    # Scan with lower size steps to see if we get any edges
    print("\nScanning with default parameters...")
    edges, report = arb_engine.scan_cross_venue(rpc, eth_usd, gas_usd,
                                                size_steps=12,
                                                max_venues_per_quote=8,
                                                use_multi_hop=True,
                                                use_parallel=True)
    print(f"Found {len(edges)} edges")
    if edges:
        for i, e in enumerate(edges[:10]):
            print(f"  Edge {i}: {e.get('venue_buy')} -> {e.get('venue_sell')} size={e.get('size_weth')} WETH net=${e.get('net_usd'):.6f} gross=${e.get('gross_usd'):.6f}")
    else:
        print("No edges found. Let's try increasing size steps and lowering profit threshold in the scan?")
        # The scan_cross_venue function does not have a profit threshold; it returns all edges that have gross profit > 0 after fees? Actually it returns edges where gross profit > 0? Let's look at arb_engine.py.
        # We'll instead compute a simple arb between two pools manually to see if there is any opportunity.
        print("\nTrying manual check between first two WETH/USDC pools...")
        # Find WETH and USDC addresses
        WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
        USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"  # Arbitrum USDC
        # Find pools that have these two tokens
        pools = []
        for addr, pool in reg.pools.items():
            if (pool.token0.lower() == WETH.lower() and pool.token1.lower() == USDC.lower()) or \
               (pool.token0.lower() == USDC.lower() and pool.token1.lower() == WETH.lower()):
                pools.append((addr, pool))
        print(f"Found {len(pools)} WETH/USDC pools")
        for addr, pool in pools[:3]:
            print(f"  Pool {addr}: reserve0={pool.reserve0}, reserve1={pool.reserve1}")
        if len(pools) >= 2:
            # Compute arbitrage between first two pools
            pool_a = pools[0][1]
            pool_b = pools[1][1]
            # Assume base=WETH, quote=USDC
            base = WETH
            quote = USDC
            # We need to compute the arbitrage using the constant product formula.
            # Let's import the helper functions from arb_engine
            from arb_engine import best_two_pool_arb, human
            # We need to convert reserves to human? The pools store reserves as uint112? Actually they are uint112 but we need to convert to human using decimals.
            # The pool stores reserves as uint112 with no decimals? Actually the reserves are stored as uint112 representing the raw token amounts (with decimals).
            # So we need to know the decimals of each token.
            # For simplicity, we'll use the human function which expects the raw reserve and the base token address to know decimals.
            # But we don't have the token contracts here. Let's approximate: WETH 18 decimals, USDC 6 decimals.
            # We'll create dummy pool objects with human values.
            # Instead, let's just use the arb_engine's pool_side function which uses the raw reserves and the token addresses to compute human.
            from arb_engine import pool_side
            # Get reserves for pool_a
            reserve0_a = pool_a.reserve0
            reserve1_a = pool_a.reserve1
            # Determine which is WETH and which is USDC
            if pool_a.token0.lower() == WETH.lower():
                weth_res_a = reserve0_a
                usdc_res_a = reserve1_a
            else:
                weth_res_a = reserve1_a
                usdc_res_a = reserve0_a
            # Same for pool_b
            if pool_b.token0.lower() == WETH.lower():
                weth_res_b = pool_b.reserve0
                usdc_res_b = pool_b.reserve1
            else:
                weth_res_b = pool_b.reserve1
                usdc_res_b = pool_b.reserve0
            print(f"Pool A: WETH={weth_res_a}, USDC={usdc_res_a}")
            print(f"Pool B: WETH={weth_res_b}, USDC={usdc_res_b}")
            # Now compute human values using the human function from arb_engine which needs the token address to get decimals.
            # We'll need to call the human function with the reserve and the token address.
            # Let's import human
            from arb_engine import human
            weth_human_a = human(weth_res_a, WETH)
            usdc_human_a = human(usdc_res_a, USDC)
            weth_human_b = human(weth_res_b, WETH)
            usdc_human_b = human(usdc_res_b, USDC)
            print(f"Pool A human: WETH={weth_human_a}, USDC={usdc_human_a}")
            print(f"Pool B human: WETH={weth_human_b}, USDC={usdc_human_b}")
            # Now we can compute the arbitrage: buy WETH with USDC in pool A, sell WETH for USDC in pool B, etc.
            # But we'll just call best_two_pool_arb with our custom pools? We'll need to create pool objects that mimic the interface.
            # Let's instead compute manually: 
            # Price in pool A = USDC/WETH = usdc_human_a / weth_human_a
            # Price in pool B = USDC/WETH = usdc_human_b / weth_human_b
            price_a = usdc_human_a / weth_human_a if weth_human_a != 0 else 0
            price_b = usdc_human_b / weth_human_b if weth_human_b != 0 else 0
            print(f"Price A (USDC per WETH): {price_a}")
            print(f"Price B (USDC per WETH): {price_b}")
            if price_a > 0 and price_b > 0:
                if price_a < price_b:
                    # Buy WETH in A (pay USDC), sell WETH in B (receive USDC)
                    # Amount of WETH we can buy with 1 USDC in A: 1/price_a WETH
                    # Then sell that WETH in B: get (1/price_a) * price_b USDC
                    profit_per_usdc = (price_b / price_a) - 1
                    print(f"Arbitrage opportunity: buy WETH in A, sell in B. Profit per USDC: {profit_per_usdc:.6%}")
                else:
                    profit_per_usdc = (price_a / price_b) - 1
                    print(f"Arbitrage opportunity: buy WETH in B, sell in A. Profit per USDC: {profit_per_usdc:.6%}")
            else:
                print("Invalid prices")
        else:
            print("Need at least two WETH/USDC pools for manual check")

if __name__ == "__main__":
    main()