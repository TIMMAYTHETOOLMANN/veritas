#!/usr/bin/env python3
"""
VERITAS V3 Quoter Forensic - Part 5
Direct test without stderr capture.
"""
from core.external_apis import AlchemyRPC
from web3 import Web3

rpc = AlchemyRPC()
w3 = Web3(Web3.HTTPProvider(rpc.url))

pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"
weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

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
            {"internalType": "int256", "name": "amount0", "type": "int256"},
            {"internalType": "int256", "name": "amount1", "type": "int256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

contract = w3.eth.contract(address=pool_addr, abi=pool_abi)

amount_in = 10**16

print("Testing pool.swap()...")
try:
    result = contract.functions.swap(
        "0x0000000000000000000000000000000000000001",
        True,
        amount_in,
        0,
        b''
    ).call()
    print(f"Result: {result}")
except Exception as e:
    print(f"Error: {e}")
