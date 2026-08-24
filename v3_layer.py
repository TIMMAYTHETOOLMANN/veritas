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
FEE_TIERS   = (100, 500, 3000, 10000)   # 0.01% 0.05% 0.3% 1%

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

def v3_pool(rpc, token_a, token_b, fee):
    r = rpc.eth_call(V3_FACTORY, SEL["getPool"] + pad(token_a)
                     + pad(token_b) + u256(fee))
    return parse_addr(r)


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

def quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr):
    """Exact out for token_in -> token_out via the (fee) pool. None on revert.

    Returns integer raw amount_out. Uses eth_call simulation of QuoterV2.
    """
    data = ("0x" + quoter_selector()
            + pad(token_in) + pad(token_out) + u256(amount_in)
            + u256(fee) + u256(0))          # sqrtPriceLimitX96 = 0 (none)
    try:
        r = rpc.call("eth_call", [{"from": from_addr, "to": QUOTER_V2,
                                   "data": data}, "latest"])
        if not r or r == "0x" or len(r) < 66:
            return None
        return int(r[2:66], 16)   # first return word = amountOut
    except Exception:
        return None


def quote_v3_best(rpc, token_in, token_out, amount_in, from_addr,
                  fees=FEE_TIERS):
    """Best executable out across fee tiers. Returns (out, fee, pool) or None."""
    best = None
    for fee in fees:
        out = quote_v3(rpc, token_in, token_out, amount_in, fee, from_addr)
        if out and (best is None or out > best[0]):
            pool = v3_pool(rpc, token_in, token_out, fee)
            best = (out, fee, pool)
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
