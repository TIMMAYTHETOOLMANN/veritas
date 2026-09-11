#!/usr/bin/env python3
"""
VERITAS V3 Quoter Forensic Diagnostic - Part 2
Focus on pool state and correct slot0 parsing.
"""
from core.external_apis import AlchemyRPC
from web3 import Web3

def main():
    print("=" * 70)
    print("VERITAS V3 QUOTER FORENSIC DIAGNOSTIC - PART 2")
    print("=" * 70)
    
    rpc = AlchemyRPC()
    
    # Pool address
    pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"
    
    # Get slot0
    slot0 = rpc.eth_call(pool_addr, "0x3850c7bd")
    print(f"\nRaw slot0: {slot0}")
    
    if slot0 and len(slot0) >= 194:
        # Parse slot0 correctly
        # ABI encoding: each field is 32 bytes
        # sqrtPriceX96: bytes 2:66 (uint160, last 20 bytes)
        # tick: bytes 66:98 (int24, last 3 bytes)
        # observationIndex: bytes 98:130 (uint16, last 2 bytes)
        # observationCardinality: bytes 130:162 (uint16, last 2 bytes)
        
        sqrt_price_raw = slot0[6:66]  # 64 hex chars = 32 bytes
        tick_raw = slot0[70:98]  # 28 hex chars = 14 bytes (but we need last 6 hex chars = 3 bytes)
        
        sqrt_price_x96 = int(sqrt_price_raw, 16)
        
        # tick is int24, stored in the last 3 bytes (6 hex chars)
        tick_hex = slot0[92:98]  # last 6 hex chars of the 32-byte word
        tick = int(tick_hex, 16)
        # Convert to signed if needed
        if tick > 2**23:
            tick -= 2**24
        
        print(f"\nParsed slot0:")
        print(f"  sqrtPriceX96: {sqrt_price_x96}")
        print(f"  tick: {tick}")
        
        # Calculate price from sqrtPriceX96
        price_raw = (sqrt_price_x96 / (2**96))**2
        # Adjust for decimals (WETH=18, USDC=6)
        price = price_raw * 10**(18-6)
        print(f"  ETH price: ${price:.2f}")
    
    # Test with web3.py contract call
    print("\n" + "-" * 40)
    print("WEB3.PY CONTRACT CALL TEST")
    print("-" * 40)
    
    w3 = Web3(Web3.HTTPProvider(rpc.url))
    
    # Minimal pool ABI for slot0
    pool_abi = [
        {
            "inputs": [],
            "name": "slot0",
            "outputs": [
                {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
                {"internalType": "int24", "name": "tick", "type": "int24"},
                {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
                {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
                {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
                {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
                {"internalType": "bool", "name": "unlocked", "type": "bool"},
            ],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    
    contract = w3.eth.contract(address=pool_addr, abi=pool_abi)
    
    try:
        slot0_result = contract.functions.slot0().call()
        print(f"  web3.py slot0: {slot0_result}")
        print(f"  sqrtPriceX96: {slot0_result[0]}")
        print(f"  tick: {slot0_result[1]}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Now test QuoterV2 with web3.py
    print("\n" + "-" * 40)
    print("QUOTER V2 TEST WITH WEB3.PY")
    print("-" * 40)
    
    quoter_addr = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
    
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
    
    quoter_contract = w3.eth.contract(address=quoter_addr, abi=quoter_abi)
    
    weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    
    # Get the function selector
    func = quoter_contract.functions.quoteExactInputSingle
    print(f"  Function: {func.fn_name}")
    
    # Build transaction to see the selector
    tx = func(weth, usdc, 500, 10**16, 0).buildTransaction({
        'gas': 0,
        'from': '0x0000000000000000000000000000000000000001'
    })
    print(f"  Selector: {tx['data'][:10]}")
    
    # Try calling
    try:
        result = func(weth, usdc, 500, 10**16, 0).call()
        print(f"  SUCCESS! amountOut: {result[0]}")
    except Exception as e:
        print(f"  FAILED: {str(e)[:200]}")
        
        # Try with smaller amount
        print("\n  Trying smaller amount (10**10)...")
        try:
            result = func(weth, usdc, 500, 10**10, 0).call()
            print(f"  SUCCESS! amountOut: {result[0]}")
        except Exception as e2:
            print(f"  FAILED: {str(e2)[:200]}")

if __name__ == "__main__":
    main()
