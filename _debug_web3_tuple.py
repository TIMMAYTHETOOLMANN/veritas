#!/usr/bin/env python3
"""Test QuoterV2 using web3.py with tuple encoding."""
from web3 import Web3

# Create a Web3 instance with Alchemy
w3 = Web3(Web3.HTTPProvider(f"https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol"))

# QuoterV2 address
quoter = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

# QuoterV2 ABI
quoter_abi = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

contract = w3.eth.contract(address=quoter, abi=quoter_abi)

weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

for fee in [500, 3000, 10000]:
    print(f"\nFee={fee}:")
    try:
        # Pass as a tuple
        result = contract.functions.quoteExactInputSingle(
            (weth, usdc, fee, 10**16, 0)  # Tuple: (tokenIn, tokenOut, fee, amountIn, sqrtPriceLimit)
        ).call()
        amount_out = result[0]
        print(f"Amount out: {amount_out}")
        if amount_out > 0:
            price = amount_out / 10**6  # USDC has 6 decimals
            print(f"ETH price (for 0.1 WETH): ${price:.2f}")
            print(f"ETH price (for 1 WETH): ${price * 10:.2f}")
    except Exception as e:
        print(f"Error: {e}")
