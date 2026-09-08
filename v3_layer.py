#!/usr/bin/env python3
"""
v3_layer.py — Uniswap V3 executable-quote layer for the VERITAS arb engine.

QuoterV2 (verified on-chain: 0x61fFE014bA17989E743c5F6cB21bF9697530B21e) gives
EXACT executable out-amounts for a V3 single-hop swap via eth_call chain
simulation. Nothing here signs — pure read-only.

CRITICAL PARSING FACT (verified live 2026-09-07):
    QuoterV2 quoteExactInputSingle((tokenIn,tokenOut,amountIn,fee,sqrtLimit))
    returns a 129-byte (4-word) result. `amountOut` is **word 0** (bytes 2:66
    after the 0x prefix). Word 1 is sqrtPriceX96After (garbage for our use).
    Verified: 1 WETH -> 2486.69 USDC, word0 = 2486697310 (÷1e6), word1 =
    quadrillion garbage.

USE: from v3_layer import quote_v3
     out = quote_v3(rpc, WETH, USDC, 10**18, 500, from_addr)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- verified on-chain ARBITRUM addresses (NOT Ethereum mainnet) ---------
WETH  = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC  = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"   # bridged USDC (native)
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"   # USDC.e (bridge)

V3_FACTORY  = "0x1f98431c8ad98523631ae4a59f267346ea31f984"  # Uniswap V3
QUOTER_V2   = "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"  # Uniswap QuoterV2

SUSHI_V3_FACTORY  = "0x1af415a1eba07a4986a52b6f2e7de7003d82231e"
PANCAKE_V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
RAMSES_V3_FACTORY  = "0xaa2cd7477c451e703f3b9ba5663334914763edf8"
CAMELOT_V3_FACTORY = "0x1a3c9b1d2f0529d97f2afc5136cc23e58f1fd35b"  # Algebra

PANCAKE_QUOTER_V2  = "0xb048bBc1Ee6b733FFfCFb9e9CeF7375518e25997"
CAMELOT_V3_QUOTER  = "0xFe24b2cDfF01B644995bc248bA8497467d688F7B"

FEE_TIERS        = (100, 500, 3000, 10000)        # 0.01% 0.05% 0.3% 1%
PANCAKE_FEE_TIERS = (100, 500, 2500, 10000)       # Pancake/Camelot use 2500

MIN_POOL_LIQUIDITY = 0.0   # relaxed; stale-filter below handles dead pools

# ---- low-level helpers ---------------------------------------------------
def pad(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def u256(v):
    return f"{int(v):064x}"


def parse_addr(res):
    if not res or len(res) < 66:
        return None
    tail = res[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def _q_selector():
    try:
        from eth_utils import keccak
        return keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4].hex()
    except Exception:
        # precomputed selector for quoteExactInputSingle
        return "c6a5026a"


def _getPool_selector():
    return "1698ee82"


# ---- pool census ---------------------------------------------------------
def v3_pool(rpc, token_a, token_b, fee, factory=V3_FACTORY):
    r = rpc.eth_call(factory, "0x" + _getPool_selector() + pad(token_a)
                     + pad(token_b) + u256(fee))
    return parse_addr(r) if r else None


def pool_liquidity(rpc, pool):
    r = rpc.eth_call(pool, "0x1a686502")  # liquidity()
    try:
        return int(r[2:66], 16) if r and len(r) >= 66 else 0
    except Exception:
        return 0


def census_v3(rpc, base=WETH, quotes=(USDC, USDCE)):
    out = []
    for q in quotes:
        for fee in FEE_TIERS:
            p = v3_pool(rpc, base, q, fee)
            if not p:
                continue
            L = pool_liquidity(rpc, p)
            out.append({"pool": p, "fee": fee, "liquidity": L,
                        "base": base, "quote": q, "live": L > 0})
    return out


# ---- executable quotes (THE core function) -------------------------------
def quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr,
             quoter=QUOTER_V2):
    """Exact out for token_in -> token_out via the (fee) pool. None on revert.

    amountOut is word 0 of the QuoterV2 result (bytes 2:66). Returns int raw
    units of token_out.
    """
    data = ("0x" + _q_selector()
            + pad(token_in) + pad(token_out) + u256(amount_in)
            + u256(fee) + u256(0))          # sqrtPriceLimitX96 = 0 (none)
    try:
        # eth_call simulates the non-view quoter. Use from_addr so the
        # simulation has a msg.sender (QuoterV2 does not need it but the
        # Node may require a valid 'from' for prologue).
        r = rpc._call({
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"from": from_addr, "to": quoter, "data": data}, "latest"],
        })
        if isinstance(r, dict):
            r = r.get("result")
        if not r or r == "0x" or len(r) < 68:
            return None
        return int(r[2:66], 16)             # amountOut (word 0)
    except Exception:
        return None


def quote_v3_best(rpc, token_in, token_out, amount_in, from_addr,
                  fees=FEE_TIERS, quoter=QUOTER_V2):
    """Best executable out across fee tiers. Returns (out, fee, pool) or None."""
    best = None
    for fee in fees:
        out = quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr, quoter)
        if out and (best is None or out > best[0]):
            pool = v3_pool(rpc, token_in, token_out, fee)
            best = (out, fee, pool)
    return best


def quote_v3_venue(rpc, token_in, token_out, amount_in, fee, from_addr, venue):
    """Quote via a named venue. Returns (out, pool) or (None, None)."""
    cfg = {
        "uniswap": (V3_FACTORY, QUOTER_V2, FEE_TIERS),
        "sushi":   (SUSHI_V3_FACTORY, QUOTER_V2, FEE_TIERS),
        "pancake": (PANCAKE_V3_FACTORY, PANCAKE_QUOTER_V2, PANCAKE_FEE_TIERS),
        "ramses":  (RAMSES_V3_FACTORY, QUOTER_V2, FEE_TIERS),
        "camelot": (CAMELOT_V3_FACTORY, CAMELOT_V3_QUOTER, PANCAKE_FEE_TIERS),
    }.get(venue)
    if not cfg or fee not in cfg[2]:
        return None, None
    factory, quoter, _ = cfg
    pool = v3_pool(rpc, token_in, token_out, fee, factory)
    if not pool:
        return None, None
    out = quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr, quoter)
    return out, pool


def quote_v3_best_multi(rpc, token_in, token_out, amount_in, from_addr,
                        venues=("uniswap", "sushi", "pancake", "ramses")):
    """Best (out, fee, pool, venue) across multiple venues + fee tiers."""
    best = None
    for venue in venues:
        cfg = {
            "uniswap": (V3_FACTORY, QUOTER_V2, FEE_TIERS),
            "sushi":   (SUSHI_V3_FACTORY, QUOTER_V2, FEE_TIERS),
            "pancake": (PANCAKE_V3_FACTORY, PANCAKE_QUOTER_V2, PANCAKE_FEE_TIERS),
            "ramses":  (RAMSES_V3_FACTORY, QUOTER_V2, FEE_TIERS),
            "camelot": (CAMELOT_V3_FACTORY, CAMELOT_V3_QUOTER, PANCAKE_FEE_TIERS),
        }.get(venue)
        if not cfg:
            continue
        factory, quoter, fee_tiers = cfg
        for fee in fee_tiers:
            pool = v3_pool(rpc, token_in, token_out, fee, factory)
            if not pool:
                continue
            out = quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr, quoter)
            if out and (best is None or out > best[0]):
                best = (out, fee, pool, venue)
    return best


def selfcheck(rpc):
    from_addr = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
    print("[v3] live pools:")
    for p in census_v3(rpc):
        if p["live"]:
            print(f"  WETH/{'USDC' if p['quote']==USDC else 'USDC.e'}"
                  f" fee={p['fee']/10000:.2f}% pool={p['pool']}"
                  f" L={p['liquidity']/1e18:.3f}e18")
    amt = 10 ** 18
    print("[v3] executable quotes (1 WETH in):")
    for qname, q in [("USDC", USDC), ("USDC.e", USDCE)]:
        b = quote_v3_best(rpc, WETH, q, amt, from_addr)
        if b:
            out, fee, pool = b
            print(f"  1 WETH -> {out/1e6:,.2f} {qname} fee={fee/10000:.2f}% ({pool})")
        else:
            print(f"  1 WETH -> NO QUOTE {qname}")


if __name__ == "__main__":
    from core.rpc import RPC
    rpc = RPC("https://gateway.tenderly.co/public/arbitrum", timeout=20, retries=2)
    selfcheck(rpc)