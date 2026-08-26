#!/usr/bin/env python3
"""
v3_layer.py — Uniswap V3 executable-quote layer for the VERITAS arb engine.

QuoterV2 (verified on-chain 2026-08-23: 0x61fFE014bA17989E743c5F6cB21bF9697530B21e)
gives EXACT executable out-amounts for a V3 single-hop swap, including the
pool's fee tier and current tick/liquidity state. This is ground truth —
no concentrated-liquidity math reimplemented locally.

Everything here is READ-ONLY (eth_call against the quoter, which is
non-view by design but simulated via eth_call — costs nothing, signs
nothing).

Usage (sanity): python3 v3_layer.py --selfcheck
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint  # noqa: E402

# ---- verified on-chain 2026-08-23 ----------------------------------------
WETH  = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC  = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"

V3_FACTORY  = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
QUOTER_V2   = "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"

# Additional V3 venues (deployed on Arbitrum)
SUSHI_V3_FACTORY  = "0x1af415a1eba07a4986a52b6f2e7de7003d82231e"
PANCAKE_V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
RAMSES_V3_FACTORY  = "0xaa2cd7477c451e703f3b9ba5663334914763edf8"
CAMELOT_V3_FACTORY = "0x1a3c9b1d2f0529d97f2afc5136cc23e58f1fd35b"  # Algebra

PANCAKE_QUOTER_V2  = "0xb048bBc1Ee6b733FFfCFb9e9CeF7375518e25997"
CAMELOT_V3_QUOTER  = "0xFe24b2cDfF01B644995bc248bA8497467d688F7B"

FEE_TIERS   = (100, 500, 3000, 10000)   # 0.01% 0.05% 0.3% 1%
PANCAKE_FEE_TIERS = (100, 500, 2500, 10000)  # Pancake/Camelot V3 use 2500 instead of 3000

SEL = {
    "getPool":     "0x1698ee82",  # getPool(address,address,uint24)
    "liquidity":   "0x1a686502",
    "token0":      "0x0dfe1681",
    "slot0":       "0x3850c7bd",  # slot0() -> (sqrtPriceX96, tick, ...)
    "balanceOf":   "0x70a08231",
}

MIN_POOL_LIQUIDITY = 10 ** 15  # raw L units; filters dead pools


def pad(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def u256(v):
    return f"{int(v):064x}"


def parse_addr(res):
    if not res or len(res) < 66:
        return None
    tail = res[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def kec(sig):
    from eth_utils import keccak
    return keccak(text=sig)[:4].hex()


def quoter_selector():
    return kec("quoteExactInputSingle((address,address,uint256,uint24,uint160))")


# ---- pool census ----------------------------------------------------------

def v3_pool(rpc, token_a, token_b, fee, factory=V3_FACTORY):
    r = rpc.eth_call(factory, SEL["getPool"] + pad(token_a)
                     + pad(token_b) + u256(fee))
    return parse_addr(r) if r else None


def pool_liquidity(rpc, pool):
    r = rpc.eth_call(pool, SEL["liquidity"])
    return uint(r) or 0


def census_v3(rpc, base=WETH, quotes=(USDC, USDCE)):
    """All live V3 pools for base x quotes x fee tiers, with liquidity."""
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


# ---- executable quotes ----------------------------------------------------

def quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr, quoter=QUOTER_V2):
    """Exact out for token_in -> token_out via the (fee) pool. None on revert.

    Returns integer raw amount_out. Uses eth_call simulation of QuoterV2.
    Note: QuoterV2 returns (amountOut, sqrtPriceX96After, ...) tuple where
    amountOut is in the SECOND 32-byte word (index 1), not the first.
    """
    data = ("0x" + quoter_selector()
            + pad(token_in) + pad(token_out) + u256(amount_in)
            + u256(fee) + u256(0))          # sqrtPriceLimitX96 = 0 (none)
    try:
        r = rpc.call("eth_call", [{"from": from_addr, "to": quoter,
                                   "data": data}, "latest"])
        if not r or r == "0x" or len(r) < 130:
            return None
        # amountOut is in the SECOND word (bytes 66-130 after 0x prefix)
        return int(r[66:130], 16)
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


# ---- multi-venue quoting --------------------------------------------------

def quote_v3_venue(rpc, token_in, token_out, amount_in, fee, from_addr, venue):
    """Quote via a specific V3 venue (uniswap, sushi, pancake, ramses, camelot).
    
    Returns (amount_out, pool_address) or (None, None).
    """
    venue_config = {
        "uniswap": {
            "factory": V3_FACTORY,
            "quoter": QUOTER_V2,
            "fee_tiers": FEE_TIERS,
        },
        "sushi": {
            "factory": SUSHI_V3_FACTORY,
            "quoter": QUOTER_V2,  # Sushi V3 uses same quoter interface
            "fee_tiers": FEE_TIERS,
        },
        "pancake": {
            "factory": PANCAKE_V3_FACTORY,
            "quoter": PANCAKE_QUOTER_V2,
            "fee_tiers": PANCAKE_FEE_TIERS,
        },
        "ramses": {
            "factory": RAMSES_V3_FACTORY,
            "quoter": QUOTER_V2,  # Ramses is Uniswap V3 fork
            "fee_tiers": FEE_TIERS,
        },
        # "camelot": {
        #     "factory": CAMELOT_V3_FACTORY,
        #     "quoter": CAMELOT_V3_QUOTER,
        #     "fee_tiers": PANCAKE_FEE_TIERS,  # Algebra uses same fee tiers
        # },
    }
    
    cfg = venue_config.get(venue)
    if not cfg:
        return None, None
    
    if fee not in cfg["fee_tiers"]:
        return None, None
    
    pool = v3_pool(rpc, token_in, token_out, fee, cfg["factory"])
    if not pool:
        return None, None
    
    out = quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr, cfg["quoter"])
    return out, pool


def quote_v3_best_multi(rpc, token_in, token_out, amount_in, from_addr,
                        venues=("uniswap", "sushi", "pancake", "ramses")):
    """Best executable out across multiple V3 venues and their fee tiers.
    
    Returns (amount_out, fee, pool, venue) or None.
    """
    best = None
    for venue in venues:
        cfg = {
            "uniswap": (V3_FACTORY, QUOTER_V2, FEE_TIERS),
            "sushi": (SUSHI_V3_FACTORY, QUOTER_V2, FEE_TIERS),
            "pancake": (PANCAKE_V3_FACTORY, PANCAKE_QUOTER_V2, PANCAKE_FEE_TIERS),
            "ramses": (RAMSES_V3_FACTORY, QUOTER_V2, FEE_TIERS),
            # "camelot": (CAMELOT_V3_FACTORY, CAMELOT_V3_QUOTER, PANCAKE_FEE_TIERS),
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


# ---- sqrt-price helpers (mid price, display only) -------------------------

def slot0(rpc, pool):
    r = rpc.eth_call(pool, SEL["slot0"])
    if not r or len(r) < 130:
        return None, None
    h = r[2:]
    return int(h[0:64], 16), int(h[64:128], 16)   # sqrtPriceX96, tick


def v3_mid_price(rpc, pool, dec_base=18, dec_quote=6):
    """quote per 1 base (human), from sqrtPriceX96. token0/token1 aware."""
    sp, tick = slot0(rpc, pool)
    if not sp:
        return None
    t0 = parse_addr(rpc.eth_call(pool, SEL["token0"]))
    # price = (sp/2^96)^2 is token1/token0 raw-decimals
    raw = (sp / 2 ** 96) ** 2
    return raw if t0 and t0.lower().startswith("0x82af") else 1 / raw


# ---- selfcheck ------------------------------------------------------------

def selfcheck(rpc):
    addr = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
    print("[v3] census (live pools only):")
    for p in census_v3(rpc):
        if p["live"]:
            print(f"  WETH/{'USDC' if p['quote']==USDC else 'USDC.e'}"
                  f" fee={p['fee']/10000:.2f}% pool={p['pool']}"
                  f" L={p['liquidity']/1e18:.3f}e18")
    print("[v3] executable quotes (1 WETH in):")
    amt = 10 ** 18
    for qname, q in [("USDC", USDC), ("USDC.e", USDCE)]:
        b = quote_v3_best(rpc, WETH, q, amt, addr)
        if b:
            out, fee, pool = b
            print(f"  1 WETH -> {out/1e6:,.2f} {qname} via fee={fee/10000:.2f}%"
                  f" ({pool}) eff_px={out/1e6:,.1f}")
        else:
            print(f"  1 WETH -> NO QUOTE {qname}")
    print("[v3] reverse quotes (4000 USDC in):")
    for qname, q in [("USDC", USDC), ("USDC.e", USDCE)]:
        b = quote_v3_best(rpc, q, WETH, 4000 * 10 ** 6, addr)
        if b:
            out, fee, pool = b
            print(f"  4000 {qname} -> {out/1e18:.6f} WETH via fee={fee/10000:.2f}%"
                  f" eff_px={4000/(out/1e18):,.1f}")
        else:
            print(f"  4000 {qname} -> NO QUOTE")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default="https://arb1.arbitrum.io/rpc")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    rpc = RPC(args.rpc, timeout=30, retries=3)
    if args.selfcheck:
        selfcheck(rpc)


if __name__ == "__main__":
    main()
