#!/usr/bin/env python3
"""
v3_layer.py — minimal stub for VERITAS cleanup.

This module preserves the public symbols still imported by the live engine
after the repository-optimization purge. The original full implementation was
an experimental V3 quoting/liquidity layer and is no longer present in-tree.

Degraded behavior:
- quote_v3() returns 0 instead of raising, so V3 code paths become no-ops
  while V2 paths in arb_engine continue to work.
- pool_liquidity() returns None so callers fall back.
"""

from __future__ import annotations

from typing import Optional

WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

MIN_POOL_LIQUIDITY = 0.0


def pool_liquidity(rpc, pool_addr: str) -> Optional[float]:  # type: ignore[type-arg]
    """Stub: always return None so callers degrade safely."""
    return None


def quote_v3(  # type: ignore[return]
    rpc,
    token_in: str,
    token_out: str,
    amount: int,
    fee: int,
    from_addr: str,
):
    """Stub: always return 0 so V3 paths short-circuit safely."""
    return 0
