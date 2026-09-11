#!/usr/bin/env python3
"""
VERITAS V3 Quoter Forensic Diagnostic

This script performs a systematic investigation of the V3 quotation path
to identify why all quote attempts fail with "no_quote".
"""
import sys
import json
from core.external_apis import AlchemyRPC

def main():
    print("=" * 70)
    print("VERITAS V3 QUOTER FORENSIC DIAGNOSTIC")
    print("=" * 70)
    
    rpc = AlchemyRPC()
    
    # ---- Section 1: Quoter Contract Verification ----
    print("\n[1] QUOTER CONTRACT VERIFICATION")
    print("-" * 40)
    
    quoter_addresses = {
        "quoter_v2": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
        "quoter_v1": "0xb27308f9F90D607463bb33eA14Bb41E1140D968C",
        "swaprouter": "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
    }
    
    for name, addr in quoter_addresses.items():
        code = rpc._call("eth_getCode", [addr, "latest"])
        code_len = len(code) if code else 0
        has_code = code_len > 2  # "0x" means no code
        print(f"  {name}: {addr}")
        print(f"    Code present: {has_code}")
        print(f"    Code size: {code_len} chars")
    
    quoter_addr = quoter_addresses["quoter_v2"]
    
    # ---- Section 2: Pool State Verification ----
    print("\n[2] POOL STATE VERIFICATION")
    print("-" * 40)
    
    # Known WETH/USDC 0.05% pool on Arbitrum
    pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"
    
    # Query pool state
    token0 = rpc.eth_call(pool_addr, "0x0dfe1681")
    token1 = rpc.eth_call(pool_addr, "0xd21220a7")
    fee = rpc.eth_call(pool_addr, "0xddca3f43")
    liquidity = rpc.eth_call(pool_addr, "0x1a686502")
    slot0 = rpc.eth_call(pool_addr, "0x3850c7bd")
    
    # Decode
    token0_addr = "0x" + token0[2:][-40:] if token0 and len(token0) >= 66 else "unknown"
    token1_addr = "0x" + token1[2:][-40:] if token1 and len(token1) >= 66 else "unknown"
    fee_val = int(fee[2:66], 16) if fee and len(fee) >= 66 else 0
    liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0
    
    print(f"  Pool: {pool_addr}")
    print(f"  token0: {token0_addr}")
    print(f"  token1: {token1_addr}")
    print(f"  fee: {fee_val}")
    print(f"  liquidity: {liquidity_val}")
    
    if slot0 and len(slot0) >= 130:
        sqrt_price_x96 = int(slot0[2:66], 16)
        tick = int(slot0[66:90], 16) if len(slot0) >= 90 else 0
        print(f"  sqrtPriceX96: {sqrt_price_x96}")
        print(f"  tick: {tick}")
    
    # ---- Section 3: Selector Verification ----
    print("\n[3] SELECTOR VERIFICATION")
    print("-" * 40)
    
    # Calculate selectors from different signatures
    from eth_utils import keccak
    
    signatures = [
        "quoteExactInputSingle((address,address,uint24,uint256,uint160))",
        "quoteExactInputSingle((address,address,uint256,uint24,uint160))",
        "quoteExactInputSingle(address,address,uint24,uint256,uint160)",
        "quoteExactInputSingle(address,address,uint256,uint24,uint160)",
    ]
    
    for sig in signatures:
        selector = keccak(text=sig)[:4].hex()
        print(f"  {sig}")
        print(f"    selector: {selector}")
    
    # ---- Section 4: Raw Quote Smoke Test ----
    print("\n[4] RAW QUOTE SMOKE TEST")
    print("-" * 40)
    
    weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    amount_in = 10**16  # 0.01 WETH
    
    # Test each selector
    selectors = {
        "c6a5026a": "quoteExactInputSingle((address,address,uint24,uint256,uint160))",
        "b3b11b7e": "quoteExactInputSingle((address,address,uint24,uint256,uint160)) [correct]",
        "f7729d43": "quoteExactInputSingle(address,address,uint24,uint256,uint160)",
    }
    
    for selector, desc in selectors.items():
        # Build calldata
        t_in = weth.lower().replace("0x", "").rjust(64, "0")
        t_out = usdc.lower().replace("0x", "").rjust(64, "0")
        fee_hex = format(fee_val, "064x")
        amt_hex = format(amount_in, "064x")
        sqrt_limit = "0" * 64
        
        data = "0x" + selector + t_in + t_out + fee_hex + amt_hex + sqrt_limit
        
        print(f"\n  Selector: {selector} ({desc})")
        print(f"  Calldata: {data[:80]}...")
        print(f"  Calldata length: {len(data)} chars")
        
        try:
            result = rpc.eth_call(quoter_addr, data)
            print(f"  Result: {result[:80] if result else 'None'}...")
            
            if result and result != "0x" and len(result) >= 66:
                amount_out = int(result[2:66], 16)
                print(f"  Amount out: {amount_out}")
                if amount_out > 0:
                    price = amount_out / 10**6
                    print(f"  SUCCESS! Price: ${price:.2f} per 0.01 WETH")
        except Exception as e:
            error_str = str(e)
            if "execution reverted" in error_str:
                print(f"  REVERTED: execution reverted")
                # Try to extract revert data
                if "data" in error_str:
                    print(f"  Revert data: {error_str[:200]}")
            else:
                print(f"  ERROR: {error_str[:100]}")
    
    # ---- Section 5: web3.py Contract Call Test ----
    print("\n[5] WEB3.PY CONTRACT CALL TEST")
    print("-" * 40)
    
    from web3 import Web3
    
    w3 = Web3(Web3.HTTPProvider(rpc.url))
    
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
    
    contract = w3.eth.contract(address=quoter_addr, abi=quoter_abi)
    
    # Get the function selector from the contract
    func = contract.functions.quoteExactInputSingle
    encoded = func(weth, usdc, fee_val, amount_in, 0).buildTransaction({'gas': 0})['data']
    print(f"  web3.py encoded selector: {encoded[:10]}")
    
    # Try calling
    try:
        result = func(weth, usdc, fee_val, amount_in, 0).call()
        print(f"  SUCCESS! Result: {result}")
    except Exception as e:
        print(f"  FAILED: {str(e)[:200]}")
    
    # ---- Section 6: Summary ----
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"  Chain: Arbitrum One (42161)")
    print(f"  Block: {rpc.eth_blockNumber()}")
    print(f"  Quoter: {quoter_addr}")
    print(f"  Pool: {pool_addr}")
    print(f"  Fee: {fee_val}")
    print(f"  Liquidity: {liquidity_val}")

if __name__ == "__main__":
    main()
