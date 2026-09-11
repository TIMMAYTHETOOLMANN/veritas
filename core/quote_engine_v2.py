#!/usr/bin/env python3
"""
core/quote_engine_v2.py — VERITAS unified quote engine (v2).

This version uses pool slot0() for price discovery instead of QuoterV2,
which has proven unreliable on Arbitrum.

For V3 pools:
- Get sqrtPriceX96 from pool.slot0()
- Calculate expected output using concentrated liquidity math
- Apply slippage based on trade size relative to liquidity

For V2 pools:
- Use constant product formula with reserves
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.pool import PoolId, PoolMetadata
from core.rpc import RPC
from core.rpc_resilience import ResilientRPC
from core.quote_engine_v3 import V3QuoterV2Adapter, V3SpotPriceAdapter
from core.error_taxonomy import ErrorTaxonomy


@dataclass
class QuoteResult:
    """Normalized quote result consumed by the rest of VERITAS."""
    amount_in: int = 0
    amount_out: int = 0
    token_in: str = ""
    token_out: str = ""
    pool: Optional[PoolId] = None
    venue: str = ""
    fee: int = 0
    price_impact_bps: float = 0.0
    gas_estimate: int = 0
    timestamp: float = 0.0
    block_number: int = 0
    quote_latency_ms: float = 0.0
    confidence: float = 0.0
    source: str = ""            # "quoter_v2" | "spot_fallback" | "v2_constant_product"
    authoritative: bool = False  # execution-eligible only when True
    success: bool = False
    error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.success and self.amount_out > 0 and self.amount_in > 0

    def is_fresh(self, max_age_seconds: int = 30) -> bool:
        age = time.time() - self.timestamp
        return age <= max_age_seconds

    def to_dict(self) -> dict:
        return {
            "amount_in": self.amount_in,
            "amount_out": self.amount_out,
            "token_in": self.token_in,
            "token_out": self.token_out,
            "pool": str(self.pool) if self.pool else None,
            "venue": self.venue,
            "fee": self.fee,
            "price_impact_bps": self.price_impact_bps,
            "gas_estimate": self.gas_estimate,
            "timestamp": self.timestamp,
            "block_number": self.block_number,
            "quote_latency_ms": self.quote_latency_ms,
            "confidence": self.confidence,
            "source": self.source,
            "authoritative": self.authoritative,
            "success": self.success,
        }


class V2QuoteAdapter:
    """V2-style quote adapter using constant product formula."""

    def __init__(self, rpc: RPC):
        self.rpc = rpc

    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
        start = time.time()
        result = QuoteResult(
            amount_in=amount_in,
            token_in=token_in,
            token_out=token_out,
            pool=pool.pool_id,
            venue=pool.pool_id.venue,
            fee=pool.fee,
        )

        try:
            if token_in.lower() == pool.token0.lower():
                reserve_in = pool.reserve0
                reserve_out = pool.reserve1
            elif token_in.lower() == pool.token1.lower():
                reserve_in = pool.reserve1
                reserve_out = pool.reserve0
            else:
                result.error = f"token {token_in} not in pool"
                return result

            if reserve_in <= 0 or reserve_out <= 0:
                result.error = "zero reserves"
                return result

            # V2 constant product formula with pool-specific fee.
            # pool.fee is in "hundredths of a bip" (e.g. 3000 = 0.3%, 2500 = 0.25%).
            # The keep-ratio is (1_000_000 - pool.fee) / 1_000_000, so the generalized formula is:
            #   amount_out = (amount_in * (1_000_000 - pool.fee) * reserve_out) //
            #                (reserve_in * 1_000_000 + amount_in * (1_000_000 - pool.fee))
            # This reduces to the original 997/1000 when pool.fee = 3000.
            adjusted_in = amount_in * (1_000_000 - pool.fee)
            numerator = adjusted_in * reserve_out
            denominator = reserve_in * 1_000_000 + adjusted_in
            amount_out = numerator // denominator

            if amount_out <= 0:
                result.error = "zero output"
                return result

            # Price impact
            if reserve_out > 0:
                spot_price = reserve_out / reserve_in
                exec_price = amount_out / amount_in if amount_in > 0 else 0
                if spot_price > 0:
                    impact = (1 - exec_price / spot_price) * 10_000
                    result.price_impact_bps = max(0, impact)

            result.amount_out = amount_out
            result.gas_estimate = 150_000
            result.source = "v2_constant_product"
            result.authoritative = True  # exact formula on reserves, not estimation
            result.success = True
            result.confidence = 0.9

        except Exception as e:
            err = ErrorTaxonomy.classify(e)
            result.error = f"{err.code.value}: {err.message}"

        result.quote_latency_ms = (time.time() - start) * 1000
        result.timestamp = time.time()
        try:
            result.block_number = self.rpc.eth_blockNumber()
        except Exception:
            result.block_number = 0

        return result


class V3QuoteAdapter:
    """V3-style quote adapter using on-chain QuoterV2 for exact quotes."""
    
    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc
        self.quoterv2 = V3QuoterV2Adapter(rpc, resilient_rpc)
        self.spot_price = V3SpotPriceAdapter(rpc, resilient_rpc)

    def quote(self, token_in: str, token_out: str, amount_in: int, pool: PoolMetadata) -> QuoteResult:
        start = time.time()
        result = QuoteResult(
            amount_in=amount_in,
            token_in=token_in,
            token_out=token_out,
            pool=pool.pool_id,
            venue=pool.pool_id.venue,
            fee=pool.fee,
        )

        try:
            # Try QuoterV2 first (authoritative)
            amount_out = self.quoterv2.quote(token_in, token_out, amount_in, pool)
            
            if amount_out and amount_out > 0:
                result.amount_out = amount_out
                result.gas_estimate = 250_000
                result.source = "quoter_v2"
                result.authoritative = True  # on-chain exact quote: execution-eligible
                result.success = True
                result.confidence = 0.95  # High confidence - on-chain quote
            else:
                # Fallback to spot price estimation
                amount_out = self.spot_price.quote(token_in, token_out, amount_in, pool)
                if amount_out and amount_out > 0:
                    result.amount_out = amount_out
                    result.gas_estimate = 250_000
                    result.source = "spot_fallback"
                    result.authoritative = False  # estimation only: NEVER execution-eligible
                    result.success = True
                    result.confidence = 0.70  # Lower confidence - estimation
                else:
                    result.error = "quoter_v2_and_spot_price_both_failed"
                    return result

        except Exception as e:
            err = ErrorTaxonomy.classify(e)
            result.error = f"{err.code.value}: {err.message}"
            return result

        result.quote_latency_ms = (time.time() - start) * 1000
        result.timestamp = time.time()
        try:
            result.block_number = self.resilient_rpc.eth_blockNumber() if self.resilient_rpc else self.rpc.eth_blockNumber()
        except Exception:
            result.block_number = 0

        return result


class QuoteEngine:
    """Unified quote engine."""

    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc
        self.v2_adapter = V2QuoteAdapter(rpc)
        self.v3_adapter = V3QuoteAdapter(rpc, resilient_rpc)
        self._stats = {
            "quotes_attempted": 0,
            "quotes_successful": 0,
            "quotes_failed": 0,
            "total_latency_ms": 0.0,
        }

    def quote(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
        pool: PoolMetadata,
        from_addr: str = "0x0000000000000000000000000000000000000001",
    ) -> QuoteResult:
        self._stats["quotes_attempted"] += 1
        start = time.time()

        if pool.kind == "v3":
            result = self.v3_adapter.quote(token_in, token_out, amount_in, pool)
        elif pool.kind == "v2":
            result = self.v2_adapter.quote(token_in, token_out, amount_in, pool)
        else:
            result = self.v2_adapter.quote(token_in, token_out, amount_in, pool)

        latency = (time.time() - start) * 1000
        self._stats["total_latency_ms"] += latency

        if result.success:
            self._stats["quotes_successful"] += 1
        else:
            self._stats["quotes_failed"] += 1

        return result

    def stats(self) -> dict:
        attempted = self._stats["quotes_attempted"]
        avg_latency = self._stats["total_latency_ms"] / attempted if attempted > 0 else 0.0
        return {
            **self._stats,
            "average_latency_ms": round(avg_latency, 2),
            "success_rate": round(self._stats["quotes_successful"] / attempted, 4) if attempted > 0 else 0.0,
        }
