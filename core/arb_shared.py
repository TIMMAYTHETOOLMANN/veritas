"""Shared arbitrage constants and helpers used by arb_engine and zk_prover."""
from typing import Any, Dict, Optional, Tuple

# ---- verified on-chain 2026-08-23 (verify_arb_venues.py) ---------------
# NOTE: all addresses stored LOWERCASE — parse_addr() returns lowercase and
# every comparison in pool_side() is exact-match.
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
UNIV2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
AAVE_V3_POOL = "0x794a61358d6845594f94dc1db02a252b5b4814ad"

SEL = {
    "getPair": "e6a43905",
    "getReserves": "0902f1ac",
    "token0": "0dfe1681",
    "token1": "d21220a7",
}


def pad_addr(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def parse_addr(result: Optional[str]) -> Optional[str]:
    if not result or len(result) < 66:
        return None
    tail = result[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def parse_reserves(result: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    if not result or result == "0x" or len(result) < 130:
        return None, None
    h = result[2:]
    return int(h[0:64], 16), int(h[64:128], 16)


def univ2_pair(rpc: Any, factory: str, token_a: str, token_b: str) -> Optional[str]:
    data = "0x" + SEL["getPair"] + pad_addr(token_a) + pad_addr(token_b)
    return parse_addr(rpc.eth_call(factory, data))
