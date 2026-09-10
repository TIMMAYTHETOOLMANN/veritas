#!/usr/bin/env python3
"""
core/quote_engine_v3.py — VERITAS V3 quote engine using on-chain QuoterV2.

This is the authoritative V3 quoting path. It uses the Uniswap V2 QuoterV2
contract to get exact swap quotes, not spot-price approximations.

QuoterV2 address (Arbitrum): 0x61fFE014bA17989E743c5F6cB21bF9697530B21e

Function: quoteExactInputSingle((address,address,uint24,uint256,uint160))
Selector: b3b11b7e
"""
from __future__ import annotations

import time
from typing import Optional

from core.pool import PoolId, PoolMetadata
from core.rpc import RPC
from core.rpc_resilience import ResilientRPC
from core.error_taxonomy import ErrorTaxonomy


# QuoterV2 address on Arbitrum
QUOTER_V2_ADDRESS = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"

# Correct selector for quoteExactInputSingle((address,address,uint24,uint256,uint160))
QUOTER_V2_SELECTOR = "c6a5026a"


def _pad_addr(addr: str) -> str:
    """Pad address to 32 bytes for ABI encoding."""
    return addr.lower().replace("0x", "").rjust(64, "0")


def _u256(value: int) -> str:
    """Encode uint256 as 32-byte hex."""
    return f"{int(value):064x}"


def encode_quote_exact_input_single(
    token_in: str,
    token_out: str,
    fee: int,
    amount_in: int,
    sqrt_price_limit_x96: int = 0,
) -> str:
    """
    Encode calldata for QuoterV2.quoteExactInputSingle.
    
    Function signature:
        quoteExactInputSingle((address,address,uint256,uint24,uint160))
    
    Tuple layout:
        tokenIn: address (32 bytes, padded)
        tokenOut: address (32 bytes, padded)
        amountIn: uint256 (32 bytes)
        fee: uint24 (32 bytes, padded)
        sqrtPriceLimitX96: uint160 (32 bytes, padded)
    """
    return (
        "0x"
        + QUOTER_V2_SELECTOR
        + _pad_addr(token_in)
        + _pad_addr(token_out)
        + _u256(amount_in)
        + _u256(fee)
        + _u256(sqrt_price_limit_x96)
    )


def decode_quote_result(result: str) -> Optional[int]:
    """
    Decode the amountOut from QuoterV2 response.
    
    QuoterV2 returns 4 words (128 bytes):
        word 0: amountOut (uint256)
        word 1: sqrtPriceX96After (uint160)
        word 2: initializedTicksCrossed (uint32)
        word 3: gasEstimate (uint256)
    """
    if not result or result == "0x" or len(result) < 66:
        return None
    try:
        # amountOut is word 0 (bytes 2:66 after 0x prefix)
        return int(result[2:66], 16)
    except (ValueError, IndexError):
        return None


class V3QuoterV2Adapter:
    """
    V3 quote adapter using on-chain QuoterV2.
    
    This is the authoritative V3 quoting path. It calls the QuoterV2 contract
    to get exact swap quotes including:
    - Tick crossing
    - Liquidity consumption
    - Price impact
    - Pool fees
    """
    
    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc
    
    def quote(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
        pool: PoolMetadata,
    ) -> Optional[int]:
        """
        Get exact quote from QuoterV2.
        
        Args:
            token_in: Input token address
            token_out: Output token address
            amount_in: Amount of input token (in wei)
            pool: Pool metadata (must contain fee tier)
            
        Returns:
            Amount of output token (in wei), or None on failure
        """
        try:
            # Use the pool's actual fee tier
            fee = pool.fee
            
            # Encode calldata
            calldata = encode_quote_exact_input_single(
                token_in=token_in,
                token_out=token_out,
                fee=fee,
                amount_in=amount_in,
                sqrt_price_limit_x96=0,  # No price limit
            )
            
            # Use resilient RPC if available
            rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
            
            # Call QuoterV2
            result = rpc.eth_call(QUOTER_V2_ADDRESS, calldata)
            
            # Decode result
            return decode_quote_result(result)
            
        except Exception as e:
            err = ErrorTaxonomy.classify(e)
            # Don't log here - let the caller handle telemetry
            return None


class V3SpotPriceAdapter:
    """
    V3 quote adapter using spot price estimation (fallback).
    
    This is NOT an exact swap quote. It uses slot0() to get the current
    price and calculates output as: input * price * (1 - fee).
    
    Use this only when QuoterV2 is unavailable.
    """
    
    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc
    
    def quote(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
        pool: PoolMetadata,
    ) -> Optional[int]:
        """
        Estimate output using spot price (NOT an exact quote).
        
        WARNING: This does not account for:
        - Tick crossing
        - Liquidity consumption
        - Price impact beyond current tick
        
        Use V3QuoterV2Adapter for accurate quotes.
        """
        try:
            rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
            
            # Get slot0
            slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
            if not slot0 or len(slot0) < 66:
                return None
            
            sqrt_price_x96 = int(slot0[2:66], 16)
            if sqrt_price_x96 <= 0:
                return None
            
            # Get token0 to determine direction
            token0 = rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
            token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""
            
            # Determine direction
            zero_for_one = token_in.lower() == token0_addr
            
            # Get decimals
            decimals_in = pool.decimals0 if zero_for_one else pool.decimals1
            decimals_out = pool.decimals1 if zero_for_one else pool.decimals0
            
            # Calculate spot price from sqrtPriceX96
            price = (sqrt_price_x96 / (2**96))**2
            
            # Adjust for decimals
            if zero_for_one:
                price_adjusted = price * 10**(decimals_in - decimals_out)
            else:
                price_adjusted = (1 / price) * 10**(decimals_out - decimals_in)
            
            # Apply pool fee
            fee_bps = pool.fee
            amount_out_float = (amount_in / 10**decimals_in) * price_adjusted * (1 - fee_bps / 10000)
            amount_out = int(amount_out_float * 10**decimals_out)
            
            return amount_out if amount_out > 0 else None
            
        except Exception:
            return None
