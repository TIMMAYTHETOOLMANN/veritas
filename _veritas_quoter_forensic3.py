#!/usr/bin/env python3
"""
VERITAS V3 Quoter Forensic - Part 3
Try alternative quoting methods.
"""
from core.external_apis import AlchemyRPC
from web3 import Web3

def main():
    print("=" * 70)
    print("VERITAS V3 QUOTER FORENSIC - PART 3")
    print("=" * 70)
    
    rpc = AlchemyRPC()
    w3 = Web3(Web3.HTTPProvider(rpc.url))
    
    pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"
    weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    
    # Method 1: Try pool.swap() simulation
    print("\n[1] POOL SWAP SIMULATION")
    print("-" * 40)
    
    pool_abi = [
        {
            "inputs": [
                {"internalType": "address", "name": "recipient", "type": "address"},
                {"internalType": "bool", "name": "zeroForOne", "type": "bool"},
                {"internalType": "int256", "name": "amountSpecified", "type": "int256"},
                {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                {"internalType": "bytes", "name": "data", "type": "bytes"},
            ],
            "name": "swap",
            "outputs": [
                {"internalType": "int256", "name": "amount0", "type": "uint256"},
                {"internalType": "int256", "name": "amount1", "type": "uint256"},
            ],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]
    
    contract = w3.eth.contract(address=pool_addr, abi=pool_abi)
    
    # Try a small swap: 0.01 WETH -> USDC
    # zeroForOne = true means token0 (WETH) -> token1 (USDC)
    amount_in = 10**16  # 0.01 WETH
    
    try:
        result = contract.functions.swap(
            "0x0000000000000000000000000000000000000001",  # recipient
            True,  # zeroForOne (WETH -> USDC)
            amount_in,  # amountSpecified
            0,  # sqrtPriceLimitX96 (no limit)
            b''  # data
        ).call()
        print(f"  Swap result: {result}")
        print(f"  Amount0: {result[0]}")
        print(f"  Amount1: {result[1]}")
        if result[1] < 0:
            usdc_out = abs(result[1])
            print(f"  USDC out: {usdc_out}")
            print(f"  ETH price: ${usdc_out / 10**6 / (amount_in / 10**18):.2f}")
    except Exception as e:
        print(f"  Swap failed: {str(e)[:200]}")
    
    # Method 2: Try QuoterV1
    print("\n[2] QUOTER V1 TEST")
    print("-" * 40)
    
    quoter_v1 = "0xb27308f9F90D607463bb33eA14Bb41E1140D968C"
    
    quoter_v1_abi = [
        {
            "inputs": [
                {"internalType": "address", "name": "tokenIn", "type": "address"},
                {"internalType": "address", "name": "tokenOut", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
                {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "quoteExactInputSingle",
            "outputs": [
                {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            ],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]
    
    quoter_v1_contract = w3.eth.contract(address=quoter_v1, abi=quoter_v1_abi)
    
    try:
        result = quoter_v1_contract.functions.quoteExactInputSingle(
            weth, usdc, 500, amount_in, 0
        ).call()
        print(f"  QuoterV1 result: {result}")
        if result > 0:
            print(f"  ETH price: ${result / 10**6 / (amount_in / 10**18):.2f}")
    except Exception as e:
        print(f"  QuoterV1 failed: {str(e)[:200]}")
    
    # Method 3: Try with different pool (0.3% fee)
    print("\n[3] DIFFERENT POOL (0.3% FEE)")
    print("-" * 40)
    
    pool_03 = "0xc473e2aee3441bf9240be85eb122abb059a3b57c"
    
    pool_03_abi = [
        {
            "inputs": [],
            "name": "liquidity",
            "outputs": [{"internalType": "uint128", "name": "liquidity", "type": "uint128"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    
    pool_03_contract = w3.eth.contract(address=pool_03, abi=pool_03_abi)
    
    try:
        liq = pool_03_contract.functions.liquidity().call()
        print(f"  Pool 0.3% liquidity: {liq}")
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    main()
