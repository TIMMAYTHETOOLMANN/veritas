#!/usr/bin/env python3
"""
Working V3 Scanner - Calculates prices from slot0 sqrtPriceX96.
No QuoterV2 calls - uses direct pool data.
"""
from web3 import Web3

RPC_URL = "https://arb1.arbitrum.io/rpc"
FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"

WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDT = "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"

POOL_ABI = [
    {"inputs":[], "name":"slot0", "outputs":[{"name":"sqrtPriceX96","type":"uint160"},{"name":"tick","type":"int24"},{"name":"observationIndex","type":"uint16"},{"name":"observationCardinality","type":"uint16"},{"name":"observationCardinalityNext","type":"uint16"},{"name":"feeProtocol","type":"uint8"},{"name":"unlocked","type":"bool"}], "stateMutability":"view", "type":"function"},
    {"inputs":[], "name":"token0", "outputs":[{"name":"","type":"address"}], "stateMutability":"view", "type":"function"},
    {"inputs":[], "name":"token1", "outputs":[{"name":"","type":"address"}], "stateMutability":"view", "type":"function"},
    {"inputs":[], "name":"fee", "outputs":[{"name":"","type":"uint24"}], "stateMutability":"view", "type":"function"},
    {"inputs":[], "name":"liquidity", "outputs":[{"name":"","type":"uint128"}], "stateMutability":"view", "type":"function"}
]

FACTORY_ABI = [
    {"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}], "name":"getPool", "outputs":[{"name":"pool","type":"address"}], "stateMutability":"view", "type":"function"}
]


def get_price_from_sqrt_x96(sqrt_price_x96, token0, token1, fee):
    """
    Calculate price from sqrtPriceX96.
    Returns price as token1/token0 adjusted for decimals.
    """
    # price = (sqrtPriceX96 / 2^96)^2
    price = (sqrt_price_x96 / (2**96))**2
    
    # Determine decimals
    decimals0 = 18 if token0 == WETH else 6
    decimals1 = 18 if token1 == WETH else 6
    
    # Adjust for decimals: price_adjusted = price * 10^(decimals0 - decimals1)
    # This gives us the price in terms of token1 per token0
    decimal_adjustment = 10 ** (decimals0 - decimals1)
    price_adjusted = price * decimal_adjustment
    
    return price_adjusted


def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    print(f"Connected. Block: {w3.eth.block_number}")
    print()
    
    factory = w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)
    
    # Get WETH/USDC pools
    pools = []
    for fee in [500, 3000, 10000]:
        pool_addr = factory.functions.getPool(
            w3.to_checksum_address(WETH),
            w3.to_checksum_address(USDC),
            fee
        ).call()
        
        if pool_addr != "0x0000000000000000000000000000000000000000":
            pool = w3.eth.contract(address=w3.to_checksum_address(pool_addr), abi=POOL_ABI)
            
            slot0 = pool.functions.slot0().call()
            token0 = pool.functions.token0().call()
            token1 = pool.functions.token1().call()
            liquidity = pool.functions.liquidity().call()
            
            price = get_price_from_sqrt_x96(slot0[0], token0, token1, fee)
            
            print(f"Pool {pool_addr[:10]}... fee={fee}")
            print(f"  token0: {token0[:10]}...")
            print(f"  token1: {token1[:10]}...")
            print(f"  sqrtPriceX96: {slot0[0]}")
            print(f"  price (token1/token0): {price:.2f}")
            print(f"  liquidity: {liquidity}")
            
            # Calculate 1 WETH in USDC
            if token0.lower() == WETH.lower():
                weth_price = price
            else:
                weth_price = 1 / price if price > 0 else 0
            
            print(f"  1 WETH = {weth_price:.2f} USDC")
            print()
            
            pools.append({
                "address": pool_addr,
                "fee": fee,
                "price": price,
                "weth_price": weth_price,
                "liquidity": liquidity,
            })
    
    # Find arbitrage opportunities
    if len(pools) >= 2:
        print("Arbitrage Analysis:")
        print("-" * 60)
        for i, pool_a in enumerate(pools):
            for j, pool_b in enumerate(pools):
                if i >= j:
                    continue
                
                # If pool_a has higher WETH price, buy on pool_b and sell on pool_a
                if pool_a["weth_price"] > pool_b["weth_price"]:
                    buy_pool = pool_b
                    sell_pool = pool_a
                else:
                    buy_pool = pool_a
                    sell_pool = pool_b
                
                price_diff = abs(pool_a["weth_price"] - pool_b["weth_price"])
                profit_per_weth = price_diff * 0.999  # Subtract fees
                
                if profit_per_weth > 0.01:
                    print(f"  Buy on {buy_pool['address'][:10]} (fee={buy_pool['fee']}) @ ${buy_pool['weth_price']:.2f}")
                    print(f"  Sell on {sell_pool['address'][:10]} (fee={sell_pool['fee']}) @ ${sell_pool['weth_price']:.2f}")
                    print(f"  Profit per WETH: ${profit_per_weth:.2f}")
                else:
                    print(f"  No arb: {pool_a['address'][:10]} (${pool_a['weth_price']:.2f}) vs {pool_b['address'][:10]} (${pool_b['weth_price']:.2f})")


if __name__ == "__main__":
    main()