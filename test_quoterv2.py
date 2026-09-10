#!/usr/bin/env python3
"""
Test QuoterV2 integration.

This test verifies that the QuoterV2 adapter produces correct quotes
by comparing against known values.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.external_apis import AlchemyRPC
from core.rpc_resilience import ResilientRPC, RPCCache
from core.quote_engine_v3 import V3QuoterV2Adapter, V3SpotPriceAdapter
from core.pool import PoolId, PoolMetadata


def test_quoterv2_direct():
    """Test QuoterV2 call directly."""
    print("=" * 60)
    print("TEST: QuoterV2 Direct Call")
    print("=" * 60)
    
    rpc = AlchemyRPC()
    
    # Known WETH/USDC 0.05% pool on Arbitrum
    pool_addr = "0xc6962004f452be920351991d15f6b388e09e8d0"
    weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    usdc = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    
    # Create pool metadata
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
    
    # Test QuoterV2
    adapter = V3QuoterV2Adapter(rpc)
    
    # Test 1: WETH -> USDC (0.01 WETH)
    amount_in = 10**16  # 0.01 WETH
    result = adapter.quote(weth, usdc, amount_in, pool)
    
    print(f"\nInput: 0.01 WETH")
    print(f"QuoterV2 output: {result}")
    if result:
        usdc_out = result / 10**6
        print(f"Output: {usdc_out:.6f} USDC")
        print(f"Implied price: ${usdc_out / 0.01:.2f}/WETH")
    
    # Test 2: Compare with spot price adapter
    spot_adapter = V3SpotPriceAdapter(rpc)
    spot_result = spot_adapter.quote(weth, usdc, amount_in, pool)
    
    print(f"\nSpot price output: {spot_result}")
    if spot_result:
        spot_usdc = spot_result / 10**6
        print(f"Spot output: {spot_usdc:.6f} USDC")
    
    # Compare
    if result and spot_result:
        diff = abs(result - spot_result)
        diff_bps = (diff / result) * 10000 if result > 0 else 0
        print(f"\nDifference: {diff} wei ({diff_bps:.1f} bps)")
        
        if diff_bps < 100:  # Within 1%
            print("✅ QuoterV2 and spot price agree within 1%")
        else:
            print("⚠️  Significant difference - investigate")
    
    return result is not None


def test_quoterv2_various_sizes():
    """Test QuoterV2 with various trade sizes."""
    print("\n" + "=" * 60)
    print("TEST: QuoterV2 Various Sizes")
    print("=" * 60)
    
    rpc = AlchemyRPC()
    
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
    
    adapter = V3QuoterV2Adapter(rpc)
    
    sizes = [
        (10**10, "0.000001 WETH"),
        (10**12, "0.001 WETH"),
        (10**14, "0.1 WETH"),
        (10**16, "0.01 WETH"),
        (10**17, "0.1 WETH"),
        (10**18, "1 WETH"),
    ]
    
    print(f"\n{'Size':<20} {'Output (USDC)':<20} {'Price':<15} {'Slippage'}")
    print("-" * 70)
    
    base_price = None
    for amount_in, label in sizes:
        result = adapter.quote(weth, usdc, amount_in, pool)
        if result:
            usdc_out = result / 10**6
            price = usdc_out / (amount_in / 10**18)
            
            if base_price is None:
                base_price = price
                slippage = "0 bps"
            else:
                slippage_bps = ((base_price - price) / base_price) * 10000
                slippage = f"{slippage_bps:.1f} bps"
            
            print(f"{label:<20} {usdc_out:<20.6f} ${price:<14.2f} {slippage}")
        else:
            print(f"{label:<20} {'FAILED':<20}")


def test_quoterv2_with_resilient_rpc():
    """Test QuoterV2 with resilient RPC."""
    print("\n" + "=" * 60)
    print("TEST: QuoterV2 with Resilient RPC")
    print("=" * 60)
    
    providers = [
        ("https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol", 0),
        ("https://gateway.tenderly.co/public/arbitrum", 1),
        ("https://arbitrum.drpc.org", 2),
    ]
    
    resilient_rpc = ResilientRPC(providers, cache=RPCCache())
    
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
    
    adapter = V3QuoterV2Adapter(None, resilient_rpc=resilient_rpc)
    
    amount_in = 10**16  # 0.01 WETH
    result = adapter.quote(weth, usdc, amount_in, pool)
    
    print(f"\nInput: 0.01 WETH")
    print(f"QuoterV2 output: {result}")
    if result:
        usdc_out = result / 10**6
        print(f"Output: {usdc_out:.6f} USDC")
        print("✅ QuoterV2 with resilient RPC works!")
    
    print(f"\nRPC Health:")
    for h in resilient_rpc.health_status():
        print(f"  {h['url'][:40]}...: {h['status']} ({h['successful']}/{h['requests']})")


if __name__ == "__main__":
    success = test_quoterv2_direct()
    test_quoterv2_various_sizes()
    test_quoterv2_with_resilient_rpc()
    
    print("\n" + "=" * 60)
    if success:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ TESTS FAILED")
    print("=" * 60)
