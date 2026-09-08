#!/usr/bin/env python3
"""
find_edges.py — VERITAS fast cross-venue V3/V2 edge finder.

Unlike scan_registry_cross_venue (which size-grids every venue-pair — O(n^2 *
sizes) and is too slow), this finder reads the registry, quotes each WETH-paired
token once per venue, and computes the best cross-venue round-trip spread.

READ-ONLY. Reports edges (venueA -> venueB) where round-trip gross clears the
~65bp fee wall + gas + safety margin. No signing, no broadcast.

Usage:
  python3 find_edges.py                 # scan everything
  python3 find_edges.py --min-depth 2000 --top 30
"""
import argparse
import sys
import os
import sqlite3
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint
import v3_layer as v3

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")

WETH  = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC  = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"

FROM = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
AAVE_FLASH_FEE = 0.0005      # 5 bps
SWAP_FEE_WALL = 0.0065       # ~65 bps round-trip (two 0.3% swaps + premium)
GAS_UNITS = 450_000


def load_weth_pools(min_depth=0.0):
    """WETH-paired pools from the registry, keyed by quote token."""
    conn = sqlite3.connect(DB, timeout=30)
    rows = conn.execute(
        "SELECT pair_addr, venue, kind, token0, token1, fee_tier, "
        "reserve0, reserve1, usd_depth FROM pools"
    ).fetchall()
    conn.close()

    pools = {}
    for pair, venue, kind, t0, t1, fee, r0, r1, depth in rows:
        t0l, t1l = t0.lower(), t1.lower()
        if WETH not in (t0l, t1l):
            continue
        if kind == "v2" and (depth is None or depth < min_depth):
            continue
        quote = t1l if t0l == WETH else t0l
        pools.setdefault(quote, []).append({
            "pair": pair.lower(),
            "venue": venue,
            "kind": kind,
            "fee": fee or 0,
            "depth": depth or 0.0,
            "weth_is_token0": t0l == WETH,
            "liquidity": None,   # populated lazily for V3 in quote_venue
        })
    return pools


def quote_venue(rpc, pool, token_in, token_out, amount_in):
    """Quote a single leg. Returns raw out units or None.

    V3 legs are gated on LIVE liquidity + non-frozen tick to reject stale-pool
    phantom quotes (the #1 false-edge source on Arbitrum long-tail).
    """
    if pool["kind"] == "v3":
        # Reject dead/stale V3 pools before quoting (liquidity ~0 or frozen tick)
        L = pool.get("liquidity", None)
        if L is None:
            try:
                L = v3.pool_liquidity(rpc, pool["pair"])
            except Exception:
                L = 0
        if not L or L < 10**15:   # < ~0.001e18 liquidity = dead pool
            return None
        # stale-tick guard: only reject EXTREME frozen ticks (the +70,935% phantom
        # came from tick=887271 on a 0-liquidity pool). Normal long-tail V3 pools
        # are NOT stale (verified 2026-08-26) — the earlier "stale pool" finding
        # was a symptom of misreading the ABI word, not real staleness.
        try:
            r = rpc.eth_call(pool["pair"], "0x3850c7bd")  # slot0
            if r and len(r) >= 130:
                tick = int(r[66:130], 16)
                tick = tick - 2**23 if tick > 2**23 else tick
                if abs(tick) > 800000:   # only reject truly frozen ticks
                    return None
        except Exception:
            pass
        return v3.quote_v3(rpc, token_in, token_out, amount_in, pool["fee"], FROM)
    else:
        # V2: quote via CPMM on live reserves
        from core.rpc import uint as _u
        sel_reserves = "0x0902f1ac"
        sel_token0 = "0x0dfe1681"
        try:
            t0 = _parse(rpc.eth_call(pool["pair"], sel_token0))
            r0_raw, r1_raw = _parse_reserves(rpc.eth_call(pool["pair"], sel_reserves))
            if not t0 or not r0_raw:
                return None
            if t0.lower() == token_in.lower():
                r_in, r_out = r0_raw, r1_raw
            else:
                r_in, r_out = r1_raw, r0_raw
            # 0.3% fee
            amt = amount_in * 997 // 1000
            out = (amt * r_out) // (r_in + amt)
            return out
        except Exception:
            return None


def _parse(res):
    if not res or len(res) < 66:
        return None
    tail = res[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def _parse_reserves(res):
    if not res or res == "0x" or len(res) < 130:
        return None, None
    h = res[2:]
    return int(h[0:64], 16), int(h[64:128], 16)


def token_decimals(rpc, addr):
    try:
        r = rpc.eth_call(addr, "0x313ce567")
        return int(r[2:66], 16) if r and len(r) >= 66 else 18
    except Exception:
        return 18


def eth_usd(rpc):
    try:
        out = v3.quote_v3(rpc, WETH, USDC, 10**18, 500, FROM)
        return out / 1e6 if out else 2450.0
    except Exception:
        return 2450.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-depth", type=float, default=500.0)
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--rpc", default="https://gateway.tenderly.co/public/arbitrum")
    args = ap.parse_args()

    rpc = RPC(args.rpc, timeout=15, retries=1)
    px = eth_usd(rpc)
    gas_usd = 0.0
    try:
        gp = rpc.eth_gasPrice()
        gas_usd = (gp * GAS_UNITS / 1e18) * px if gp else 0.0
    except Exception:
        pass

    pools = load_weth_pools(args.min_depth)
    print(f"[edges] {len(pools)} WETH-paired quote tokens above depth ${args.min_depth:.0f} "
          f"(ETH ${px:.0f}, gas ${gas_usd:.3f})", flush=True)

    SIZE_WETH = 1.0
    amt = int(SIZE_WETH * 1e18)

    results = []
    for quote, plist in pools.items():
        if len(plist) < 2:
            continue
        # best buy (WETH->quote) and best sell (quote->WETH) across venues
        best_buy = None   # (quote_out_units, pool)
        best_sell = None  # (weth_out_units, pool)
        for p in plist:
            # buy leg: WETH -> quote (we get MORE quote = better buy)
            b = quote_venue(rpc, p, WETH, quote, amt)
            if b and (best_buy is None or b > best_buy[0]):
                best_buy = (b, p)
            # sell leg: quote -> WETH (same 1 WETH worth of quote back)
            # to compare apples-apples, quote `amt` quote -> WETH; higher out = better sell venue
            s = quote_venue(rpc, p, quote, WETH, amt)
            if s and (best_sell is None or s > best_sell[0]):
                best_sell = (s, p)
        if not best_buy or not best_sell:
            continue
        # round-trip: buy WETH->quote on best_buy (amt WETH -> mid quote),
        #             sell quote->WETH on best_sell (mid quote -> back WETH)
        mid = best_buy[0]                      # quote units from amt WETH
        back = quote_venue(rpc, best_sell[1], quote, WETH, mid)
        if not back:
            continue
        profit_weth = (back - amt) / 1e18
        gross_usd = profit_weth * px
        cost = (amt / 1e18) * px * AAVE_FLASH_FEE + gas_usd + 0.05
        net = gross_usd - cost
        results.append({
            "quote": quote,
            "buy_venue": f"{best_buy[1]['venue']}/{best_buy[1]['kind']}/{best_buy[1]['fee']}",
            "sell_venue": f"{best_sell[1]['venue']}/{best_sell[1]['kind']}/{best_sell[1]['fee']}",
            "gross_usd": round(gross_usd, 4),
            "net_usd": round(net, 4),
        })

    results.sort(key=lambda r: r["net_usd"], reverse=True)
    edges = [r for r in results if r["net_usd"] > 0]
    print(f"[edges] {len(results)} venue-pairs evaluated, {len(edges)} profitable", flush=True)
    for r in results[:15]:
        mark = "EDGE" if r["net_usd"] > 0 else "    "
        print(f"  {mark} net=${r['net_usd']:>7.2f} gross=${r['gross_usd']:>7.2f} "
              f"{r['quote'][:10]} buy={r['buy_venue']} sell={r['sell_venue']}", flush=True)


if __name__ == "__main__":
    main()