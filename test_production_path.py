#!/usr/bin/env python3
"""
Production Path Verification Test

Verifies that the live scanner's quote path produces the same result
as the authoritative QuoterV2 reference implementation.
"""
import os
import sys

os.environ['PYTHONIOENCODING'] = 'utf-8'

from core.external_apis import AlchemyRPC
from core.rpc_resilience import ResilientRPC, RPCCache
from core.pool import PoolId, PoolMetadata
from core.quote_engine_v2 import QuoteEngine
from core.quote_engine_v3 import V3QuoterV2Adapter, encode_quote_exact_input_single, decode_quote_result


def main():
    print("=" * 70)
    print("PRODUCTION PATH VERIFICATION TEST")
    print("=" * 70)
    
    # Known WETH/USDC 0.05% pool on Arbitrum
    pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"
    weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    
    pool_id = PoolId(
        chain_id=42161,
        venue="uniswap_v3",
        factory="0x1f98431c8ad98523631ae4a59f267346ea31f984",
        pool_address=pool_addr,
    )
    pool = PoolMetadata(
        pool_id=pool_id,
        token0=weth,
        token1=usdc,
        decimals0=18,
        decimals1=6,
        fee=500,
        kind="v3",
    )
    
    # Trade size
    amount_in = 10**16  # 0.01 WETH
    
    print(f"\nPool: {pool_addr}")
    print(f"Token In: WETH ({weth})")
    print(f"Token Out: USDC ({usdc})")
    print(f"Fee: {pool.fee} bps (0.05%)")
    print(f"Amount In: {amount_in} wei (0.01 WETH)")
    
    # ---- Reference: Direct QuoterV2 call ----
    print("\n" + "-" * 70)
    print("REFERENCE: Direct QuoterV2 Call")
    print("-" * 70)
    
    rpc = AlchemyRPC()
    
    calldata = encode_quote_exact_input_single(
        token_in=weth,
        token_out=usdc,
        fee=500,
        amount_in=amount_in,
    )
    
    quoter_addr = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
    
    try:
        ref_result = rpc.eth_call(quoter_addr, calldata)
        ref_amount_out = decode_quote_result(ref_result)
        ref_block = rpc.eth_blockNumber()
        
        print(f"  Calldata: {calldata[:60]}...")
        print(f"  Raw result: {ref_result[:60]}...")
        print(f"  Amount out: {ref_amount_out}")
        print(f"  USDC out: {ref_amount_out / 10**6:.6f}" if ref_amount_out else "  FAILED")
        print(f"  Block: {ref_block}")
    except Exception as e:
        print(f"  ERROR: {e}")
        ref_amount_out = None
        ref_block = 0
    
    # ---- Production: Full scanner path ----
    print("\n" + "-" * 70)
    print("PRODUCTION: Scanner Quote Path")
    print("-" * 70)
    
    # Create resilient RPC (same as production)
    providers = [
            (os.getenv("ALCHEMY_ARBITRUM_URL", ""), 0),
            ("https://gateway.tenderly.co/public/arbitrum", 1),
            ("https://arbitrum.drpc.org", 2),
        ]
    resilient_rpc = ResilientRPC(providers, cache=RPCCache())
    
    # Create production quote engine
    engine = QuoteEngine(rpc, resilient_rpc)
    
    # Get quote through production path
    quote_result = engine.quote(weth, usdc, amount_in, pool)
    
    print(f"  Success: {quote_result.success}")
    print(f"  Amount out: {quote_result.amount_out}")
    print(f"  USDC out: {quote_result.amount_out / 10**6:.6f}" if quote_result.amount_out else "  N/A")
    print(f"  Confidence: {quote_result.confidence}")
    print(f"  Error: {quote_result.error}")
    print(f"  Quote latency: {quote_result.quote_latency_ms:.1f}ms")
    print(f"  Block: {quote_result.block_number}")
    
    # ---- Comparison ----
    print("\n" + "-" * 70)
    print("COMPARISON")
    print("-" * 70)
    
    if ref_amount_out and quote_result.amount_out:
        diff = abs(ref_amount_out - quote_result.amount_out)
        diff_bps = (diff / ref_amount_out) * 10000 if ref_amount_out > 0 else 0
        
        print(f"  Reference:  {ref_amount_out:>15} wei ({ref_amount_out / 10**6:.6f} USDC)")
        print(f"  Production: {quote_result.amount_out:>15} wei ({quote_result.amount_out / 10**6:.6f} USDC)")
        print(f"  Difference: {diff:>15} wei ({diff_bps:.2f} bps)")
        
        if diff_bps < 10:  # Within 0.1%
            print("\n  ✅ PRODUCTION MATCHES REFERENCE")
        elif diff_bps < 100:  # Within 1%
            print("\n  ⚠️  Small difference (may be block timing)")
        else:
            print("\n  ❌ SIGNIFICANT DIFFERENCE - INVESTIGATE")
    else:
        print("  Cannot compare - one or both failed")
    
    # ---- RPC Health ----
    print("\n" + "-" * 70)
    print("RPC HEALTH")
    print("-" * 70)
    for h in resilient_rpc.health_status():
        print(f"  {h['url'][:40]}...: {h['status']} ({h['successful']}/{h['requests']})")


if __name__ == "__main__":
    main()
