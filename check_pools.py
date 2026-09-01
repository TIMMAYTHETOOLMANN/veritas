#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))

from registry import Registry
from web3 import Web3

# Connect to Arbitrum to get current prices
w3 = Web3(Web3.HTTPProvider('https://arb1.arbitrum.io/rpc'))

reg = Registry()
print(f"Total pools in registry: {len(reg.pools)}")

WETH = Web3.to_checksum_address('0x82af49447d8a07e3bd95bd0d56f35241523fbab1')
USDC = Web3.to_checksum_address('0xaf88d065e77c8cC2239327C5EDb3A432268e5831')

weth_usdc_pools = []
for addr, pool in reg.pools.items():
    t0 = pool.token0.lower()
    t1 = pool.token1.lower()
    if (t0 == WETH.lower() and t1 == USDC.lower()) or (t0 == USDC.lower() and t1 == WETH.lower()):
        weth_usdc_pools.append((addr, pool))

print(f"Found {len(weth_usdc_pools)} WETH/USDC pools")
for i, (addr, pool) in enumerate(weth_usdc_pools[:10]):
    if pool.token0.lower() == WETH.lower():
        weth_res = pool.reserve0
        usdc_res = pool.reserve1
    else:
        weth_res = pool.reserve1
        usdc_res = pool.reserve0
    # Convert to human: WETH 18 decimals, USDC 6 decimals
    weth_human = weth_res / 1e18
    usdc_human = usdc_res / 1e6
    price = usdc_human / weth_human if weth_human > 0 else 0
    print(f"  Pool {i}: {addr}")
    print(f"    WETH reserve: {weth_res} ({weth_human:.6f} WETH)")
    print(f"    USDC reserve: {usdc_res} ({usdc_human:.2f} USDC)")
    print(f"    Price (USDC/WETH): {price:.2f}")

# Also check the Sushi pool directly via RPC
SUSHI_WETH_USDC = Web3.to_checksum_address('0x57b85fef094e10b5eecdf350af688299e9553378')
pair_abi = [{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"reserve0","type":"uint112"},{"name":"reserve1","type":"uint112"},{"name":"blockTimestampLast","type":"uint32"}],"type":"function"}]
pair_contract = w3.eth.contract(address=SUSHI_WETH_USDC, abi=pair_abi)
reserves = pair_contract.functions.getReserves().call()
weth_res = reserves[0]
usdc_res = reserves[1]
weth_human = weth_res / 1e18
usdc_human = usdc_res / 1e6
price = usdc_human / weth_human if weth_human > 0 else 0
print(f"\nSushi pool (direct RPC):")
print(f"  WETH reserve: {weth_res} ({weth_human:.6f} WETH)")
print(f"  USDC reserve: {usdc_res} ({usdc_human:.2f} USDC)")
print(f"  Price (USDC/WETH): {price:.2f}")

# Now, let's see if we can compute arbitrage between the first two pools we found
if len(weth_usdc_pools) >= 2:
    pool1_addr, pool1 = weth_usdc_pools[0]
    pool2_addr, pool2 = weth_usdc_pools[1]
    # Determine reserves for each pool
    if pool1.token0.lower() == WETH.lower():
        weth_res1 = pool1.reserve0
        usdc_res1 = pool1.reserve1
    else:
        weth_res1 = pool1.reserve1
        usdc_res1 = pool1.reserve0
    if pool2.token0.lower() == WETH.lower():
        weth_res2 = pool2.reserve0
        usdc_res2 = pool2.reserve1
    else:
        weth_res2 = pool2.reserve1
        usdc_res2 = pool2.reserve0
    weth_human1 = weth_res1 / 1e18
    usdc_human1 = usdc_res1 / 1e6
    weth_human2 = weth_res2 / 1e18
    usdc_human2 = usdc_res2 / 1e6
    price1 = usdc_human1 / weth_human1 if weth_human1 > 0 else 0
    price2 = usdc_human2 / weth_human2 if weth_human2 > 0 else 0
    print(f"\nArbitrage between pool1 ({pool1_addr}) and pool2 ({pool2_addr}):")
    print(f"  Pool1 price: {price1:.2f} USDC/WETH")
    print(f"  Pool2 price: {price2:.2f} USDC/WETH")
    if price1 > 0 and price2 > 0:
        if price1 < price2:
            profit_per_usdc = (price2 / price1) - 1
            print(f"  Arbitrage opportunity: Buy WETH in pool1, sell in pool2")
            print(f"  Profit per USDC invested: {profit_per_usdc:.6%}")
        else:
            profit_per_usdc = (price1 / price2) - 1
            print(f"  Arbitrage opportunity: Buy WETH in pool2, sell in pool1")
            print(f"  Profit per USDC invested: {profit_per_usdc:.6%}")
    else:
        print("  Invalid prices")
else:
    print("\nNeed at least two WETH/USDC pools to compute arbitrage")