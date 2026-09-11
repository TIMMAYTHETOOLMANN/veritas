#!/usr/bin/env python3
"""Debug: compare manual encoding vs web3.py encoding."""
from web3 import Web3

w3 = Web3(Web3.HTTPProvider(f"https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol"))

quoter = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

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
fee = 500
amount_in = 10**16

# Get the encoded call data using web3.py
encoded = contract.encodeABI(
    fn_name="quoteExactInputSingle",
    args=[(weth, usdc, fee, amount_in, 0)]
)

print(f"Web3.py encoded: {encoded[:80]}...")
print(f"Full: {encoded}")

# Now try manual encoding
SELECTOR = encoded[:10]  # 0x + 8 chars
print(f"\nSelector from web3: {SELECTOR}")

# Manual encoding
t_in = weth.lower().replace("0x", "").rjust(64, "0")
t_out = usdc.lower().replace("0x", "").rjust(64, "0")
fee_hex = format(fee, "064x")
amt_hex = format(amount_in, "064x")
sqrt_limit = "0" * 64

manual_data = "0x" + SELECTOR[2:] + t_in + t_out + fee_hex + amt_hex + sqrt_limit
print(f"\nManual encoded: {manual_data[:80]}...")

print(f"\nAre they equal: {encoded == manual_data}")
