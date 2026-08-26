#!/usr/bin/env python3
"""
arb_engine.py — VERITAS Engine: Arbitrum flash-loan arb scan layer.

Layer 1 — SCAN (read-only, $0): find dislocations between constant-product
pools on Arbitrum (SushiSwap V2 + Uniswap V2 factories) for WETH/USDC and
WETH/USDC.e, compute optimal trade size and gross profit with the proven
two-pool CPMM math (ported from the Base arb_scan.py lineage).

Cost-stack gate: gross profit must clear BOTH swap fees (in the math),
the 0.05% Aave flash-loan premium on principal, gas, and a safety margin
before a candidate is called an EDGE.

This module NEVER signs or sends a transaction. The fork-sim gate
(sim_gate.py) and the hunter loop (flash_hunter.py) consume its output.

Usage:
  python3 arb_engine.py scan                # one pass, human report
  python3 arb_engine.py scan --json         # one pass, machine JSON
  python3 arb_engine.py scan --interval 30  # loop with heartbeats
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint

# ---- verified on-chain 2026-08-23 (verify_arb_venues.py) ----------------
# NOTE: all addresses stored LOWERCASE — parse_addr() returns lowercase and
# every comparison in pool_side() is exact-match.
WETH   = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC   = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE  = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
UNIV2_FACTORY  = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY  = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
AAVE_V3_POOL   = "0x794a61358d6845594f94dc1db02a252b5b4814ad"

TOKENS = {
    WETH:  {"sym": "WETH",   "decimals": 18},
    USDC:  {"sym": "USDC",   "decimals": 6},
    USDCE: {"sym": "USDC.e", "decimals": 6},
}

FEE_NUM = 997            # 0.3% V2 pools: keep 997/1000 of input
AAVE_FLASH_FEE = 0.0005  # 0.05% premium on borrowed principal
GAS_UNITS = 350_000      # measured-class executor tx cost
SAFETY_MARGIN_USD = 0.50 # edge must clear the whole stack by this much

SEL = {
    "getPair":  "e6a43905",
    "reserves": "0902f1ac",
    "token0":   "0dfe1681",
    "token1":   "d21220a7",
}


# ---- low-level helpers ---------------------------------------------------

def pad_addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def parse_addr(result):
    if not result or len(result) < 66:
        return None
    tail = result[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def parse_reserves(result):
    if not result or result == "0x" or len(result) < 130:
        return None, None
    h = result[2:]
    return int(h[0:64], 16), int(h[64:128], 16)


def univ2_pair(rpc, factory, token_a, token_b):
    data = "0x" + SEL["getPair"] + pad_addr(token_a) + pad_addr(token_b)
    return parse_addr(rpc.eth_call(factory, data))


def load_pool(rpc, name, address):
    """token ordering + reserves for a constant-product pair."""
    if address is None:
        return None
    t0 = parse_addr(rpc.eth_call(address, "0x" + SEL["token0"]))
    t1 = parse_addr(rpc.eth_call(address, "0x" + SEL["token1"]))
    r0, r1 = parse_reserves(rpc.eth_call(address, "0x" + SEL["reserves"]))
    if t0 is None or t1 is None or r0 is None:
        return None
    return {"name": name, "address": address,
            "token0": t0, "token1": t1, "r0": r0, "r1": r1}


def pool_side(pool, base, quote):
    """(base_reserve_raw, quote_reserve_raw) for base/quote, or (None, None)."""
    if pool["token0"] == base and pool["token1"] == quote:
        return pool["r0"], pool["r1"]
    if pool["token0"] == quote and pool["token1"] == base:
        return pool["r1"], pool["r0"]
    return None, None


def human(raw, token):
    return raw / 10 ** TOKENS[token]["decimals"]


def price_of(pool, base, quote):
    br, qr = pool_side(pool, base, quote)
    if not br:
        return None
    return human(qr, quote) / human(br, base)


# ---- arb math (constant-product, numeric size scan) ----------------------

def cp_out(reserve_in, reserve_out, amount_in, fee_num=FEE_NUM):
    if amount_in <= 0:
        return 0.0
    ain = amount_in * fee_num / 1000.0
    return reserve_out * ain / (reserve_in + ain)


def best_two_pool_arb(pool_a, pool_b, base, quote, ref_price):
    """Best (direction, size_base, gross_profit_usd) buying base on one pool
    and selling it on the other, both directions considered."""
    best = None
    for (buy, sell) in ((pool_a, pool_b), (pool_b, pool_a)):
        b_in_r, b_out_r = pool_side(buy, base, quote)    # base -> quote side
        s_in_r, s_out_r = pool_side(sell, quote, base)   # quote -> base side
        if not b_in_r or not s_out_r:
            continue
        bin_h, bout_h = human(b_in_r, base), human(b_out_r, quote)
        sin_h, sout_h = human(s_in_r, quote), human(s_out_r, base)
        if bin_h <= 0 or sin_h <= 0:
            continue
        hi = min(bin_h, sout_h) * 0.30
        for i in range(120):
            size = hi * (i / 119.0) if i else hi * 1e-4
            if size <= 0:
                continue
            got_quote = cp_out(bin_h, bout_h, size)
            got_base = cp_out(sin_h, sout_h, got_quote)
            profit = got_base - size
            if profit > 0:
                usd = profit * ref_price
                if best is None or usd > best[2]:
                    best = (f"{buy['name']} -> {sell['name']}", size, usd)
    return best


# ---- token metadata (expanding beyond hard-coded universe) ----------------

_decimals_cache = {}


def token_decimals(rpc, addr):
    addr = addr.lower()
    if addr in _decimals_cache:
        return _decimals_cache[addr]
    if addr in TOKENS:
        d = TOKENS[addr]["decimals"]
        _decimals_cache[addr] = d
        return d
    try:
        r = rpc.eth_call(addr, "0x313ce567")
        d = int(r[2:66], 16) if r and len(r) >= 66 else None
    except Exception:
        d = None
    if d is None:
        d = 18
    _decimals_cache[addr] = d
    return d


def token_symbol(addr):
    return TOKENS.get(addr, {}).get("sym", addr[:8])


# ---- cross-venue scan (V2 x V3 via QuoterV2 executable quotes) -----------

def _v3_sort_key(liquidity, fee):
    """Normalize V3 raw liquidity to a comparable USD-depth scale.

    V3 liquidity L has units of sqrt(token0)*sqrt(token1) and ranges
    10^12–10^24.  V2 usd_depth is actual USD (10^2–10^6).  Without
    normalization the sort silently drops every V2 pool.

    We use log10(L) − 10 so that the deepest V3 pools (~10^18–10^24)
    score 8–14 while the shallowest (~10^12–10^13) score 2–3 —
    overlapping the V2 range (2–6).  Fee-tier adjustment: higher fee
    pools deploy less capital for the same L, so we discount them.
    """
    import math
    if liquidity <= 0:
        return 0.0
    base = math.log10(liquidity) - 10.0
    # discount by fee tier: 1% fee pools need ~10× more L for same depth
    fee_discount = {100: 0.0, 500: 0.3, 3000: 0.7, 10000: 1.2}
    return base - fee_discount.get(fee, 0.7)


def pool_liquidity_cached(rpc, conn, addr):
    """Fetch V3 pool liquidity, caching in DB with a 30s freshness window.
    Avoids 91 eth_calls on every scan cycle (the old bottleneck)."""
    import time
    addr = addr.lower()
    row = conn.execute(
        "SELECT last_checked FROM pools WHERE pair_addr=?", (addr,)).fetchone()
    now = time.time()
    if row and row[0]:
        try:
            last = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
            if now - last < 30:
                r = conn.execute(
                    "SELECT usd_depth FROM pools WHERE pair_addr=?", (addr,)).fetchone()
                return r[0] if r and r[0] else None
        except Exception:
            pass
    L = pool_liquidity(rpc, addr)
    if L is None:
        L = 0
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    # Store as REAL (SQLite can't handle 10^24 ints; usd_depth is REAL)
    conn.execute("UPDATE pools SET usd_depth=?, last_checked=? WHERE pair_addr=?",
                 (float(L), ts, addr))
    conn.commit()
    return L


def _load_registry_pools(rpc):
    """Read registered pools from veritas.db. Returns (v3_census, v2_pools)."""
    import sqlite3, math
    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    rows = conn.execute(
        "SELECT pair_addr, venue, kind, token0, token1, fee_tier, "
        "reserve0, reserve1, usd_depth FROM pools").fetchall()

    v3 = []
    v2 = []
    for addr, venue, kind, t0, t1, fee, r0, r1, depth in rows:
        t0l, t1l = t0.lower(), t1.lower()
        if kind == "v3":
            # Use cached liquidity from DB if available; only fetch live if stale
            L = pool_liquidity_cached(rpc, conn, addr)
            # only keep WETH-paired V3 pools
            if t0l != WETH and t1l != WETH:
                continue
            quote = t1l if t0l == WETH else t0l
            v3.append({"pool": addr, "fee": fee, "quote": quote,
                       "liquidity": L,
                       "sort_key": _v3_sort_key(L, fee),
                       "name": f"{venue} V3 {fee/10000:.2f}%"})
        elif kind == "v2":
            # only keep WETH-paired V2 pools with depth
            if t0l == WETH:
                base, quote, br, qr = t0l, t1l, r0, r1
            elif t1l == WETH:
                base, quote, br, qr = t1l, t0l, r1, r0
            else:
                continue
            if not br or not qr:
                continue
            # sanity-filter absurd depth (decimal read errors)
            depth = depth or 0
            if depth > 1_000_000_000 or depth < 0:
                continue
            dec0 = token_decimals(rpc, t0l)
            dec1 = token_decimals(rpc, t1l)
            p = {
                "name": f"{venue} WETH/{quote[:8]}",
                "address": addr,
                "token0": t0l, "token1": t1l,
                "r0": int(br * 10 ** dec0),
                "r1": int(qr * 10 ** dec1),
                "quote": quote,
                "weth_reserve": br,
                "quote_reserve": qr,
                "usd_depth": depth,
                "sort_key": math.log10(depth + 1) if depth > 0 else 0.0,
            }
            v2.append(p)
    conn.close()
    return v3, v2


# ---- V3 pool liveness check (not stale) -----------------------------------
# V3 pools don't hold "reserves" like V2 pairs — liquidity is concentrated in
# tick ranges.  The old _check_v3_pool_stale() compared balanceOf() against
# the quoter price, which is mathematically wrong for V3 and marked EVERY
# pool as stale.  Instead we verify the pool has non-trivial liquidity
# (>= MIN_POOL_LIQUIDITY from v3_layer) — if L > 0 the pool is live and
# tradeable.  This is a read of the liquidity() getter, not a reserve check.
def _is_v3_pool_live(rpc, pool_addr):
    """True if the V3 pool has real liquidity (>= MIN_POOL_LIQUIDITY)."""
    try:
        import v3_layer as vl
        L = vl.pool_liquidity(rpc, pool_addr)
        return L is not None and L >= vl.MIN_POOL_LIQUIDITY
    except Exception:
        return False


def scan_cross_venue(rpc, eth_usd, gas_usd, size_steps=12, max_venues_per_quote=8):
    """V3 <-> V3 and V3 <-> V2 edges using the pool registry."""
    import v3_layer

    v3_census_raw, v2_pools = _load_registry_pools(rpc)
    v3_census = [p for p in v3_census_raw
                 if p["liquidity"] >= v3_layer.MIN_POOL_LIQUIDITY]

    from_addr = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
    edges = []
    report = []

    # group venues by quote token; keep deepest V2 + most liquid V3
    quotes = {}
    for p in v3_census:
        quotes.setdefault(p["quote"], []).append(
            (p["name"], 1, p["pool"], p["fee"], p["sort_key"]))
    for p in v2_pools:
        quotes.setdefault(p["quote"], []).append(
            (p["name"], 0, p["address"], 0, p["sort_key"]))

    for quote, venues in quotes.items():
        qsym = token_symbol(quote)
        if len(venues) < 2:
            continue
        # sort by depth/liquidity descending, cap count.
        # Guarantee at least 1 V2 venue per quote so cross-venue V3<->V2
        # edges are not silently dropped (the old bug).
        venues.sort(key=lambda x: x[4], reverse=True)
        v2_venues = [v for v in venues if v[1] == 0]
        v3_venues = [v for v in venues if v[1] == 1]
        if v2_venues:
            keep = [v2_venues[0]]  # deepest V2 always kept
            keep += [v for v in v3_venues if v not in keep]
            keep += [v for v in v2_venues if v not in keep]
            venues = keep[:max_venues_per_quote]
        else:
            venues = venues[:max_venues_per_quote]
        q_dec = token_decimals(rpc, quote)

        # V3 venues already filtered by liquidity in _load_registry_pools;
        # no need for a redundant live liveness check here.
        if len(venues) < 2:
            continue
        
        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                buy = venues[i]
                sell = venues[j]
                best = None
                best_dir = None
                # Test both arbitrage directions
                for b_buy, b_sell in ((buy, sell), (sell, buy)):
                    # quote a small size to detect price divergence
                    probe_size = 0.01
                    amt = int(probe_size * 1e18)
                    if b_buy[1] == 1:
                        mid = v3_layer.quote_v3(rpc, WETH, quote, amt,
                                                b_buy[3], from_addr)
                    else:
                        mid = v2_quote_out(rpc, b_buy[2], WETH, quote, amt,
                                           q_dec=q_dec)
                    if not mid or mid == 0:
                        continue
                    if b_sell[1] == 1:
                        back = v3_layer.quote_v3(rpc, quote, WETH, mid,
                                                 b_sell[3], from_addr)
                    else:
                        back = v2_quote_out(rpc, b_sell[2], quote, WETH, mid,
                                            q_dec=q_dec)
                    if not back or back == 0:
                        continue
                    probe_profit = (back - amt) / 1e18
                    if probe_profit <= 0:
                        continue
                    # --- size scan only if probe was positive ---
                    # Use small scan sizes: V2 pools often have shallow WETH
                    # reserves, and 0.01 WETH probe already detected the edge
                    if b_buy[1] == 0:  # V2
                        hi = 0.05
                    else:  # V3
                        hi = 0.5
                    for k in range(1, size_steps + 1):
                        size = hi * k / size_steps
                        amt = int(size * 1e18)
                        if b_buy[1] == 1:
                            mid = v3_layer.quote_v3(rpc, WETH, quote, amt,
                                                    b_buy[3], from_addr)
                        else:
                            mid = v2_quote_out(rpc, b_buy[2], WETH, quote, amt,
                                               q_dec=q_dec)
                        if not mid or mid == 0:
                            continue
                        if b_sell[1] == 1:
                            back = v3_layer.quote_v3(rpc, quote, WETH, mid,
                                                     b_sell[3], from_addr)
                        else:
                            back = v2_quote_out(rpc, b_sell[2], quote, WETH, mid,
                                                q_dec=q_dec)
                        if not back:
                            continue
                        profit_weth = (back - amt) / 1e18
                        if profit_weth > 0 and (best is None
                                                or profit_weth > best[2]):
                            best = (size, mid, profit_weth)
                            best_dir = (b_buy, b_sell)
                if best and best_dir:
                    size, mid, profit = best
                    b_buy, b_sell = best_dir
                    row = {"pair": f"WETH/{qsym}",
                           "venue_buy": b_buy[0],
                           "venue_sell": b_sell[0]}
                    loan_fee_usd = size * eth_usd * AAVE_FLASH_FEE
                    net = profit * eth_usd - gas_usd - loan_fee_usd
                    row.update({
                        "size_weth": round(size, 6),
                        "gross_usd": round(profit * eth_usd, 4),
                        "loan_fee_usd": round(loan_fee_usd, 4),
                        "gas_usd": round(gas_usd, 4),
                        "net_usd": round(net, 4),
                        "buy_kind": b_buy[1], "buy_venue": b_buy[2], "buy_fee": b_buy[3],
                        "sell_kind": b_sell[1], "sell_venue": b_sell[2], "sell_fee": b_sell[3],
                        "quote": quote,
                    })
                    if net > SAFETY_MARGIN_USD:
                        row["edge"] = True
                        edges.append(row)
                    report.append(row)
                else:
                    report.append({"pair": f"WETH/{qsym}",
                                    "venue_buy": buy[0],
                                    "venue_sell": sell[0]})

    return edges, report


def pool_liquidity(rpc, pool):
    r = rpc.eth_call(pool, "0x1a686502")
    return uint(r) or 0


def v2_quote_out(rpc, pair, token_in, token_out, amount_in, q_dec=None):
    """Exact V2 out via live reserves. Returns raw int or None."""
    tok0_sel = SEL["token0"]
    if not tok0_sel.startswith("0x"):
        tok0_sel = "0x" + tok0_sel
    res_sel = SEL["reserves"]
    if not res_sel.startswith("0x"):
        res_sel = "0x" + res_sel
    t0 = parse_addr(rpc.eth_call(pair, tok0_sel))
    r0, r1 = parse_reserves(rpc.eth_call(pair, res_sel))
    if t0 is None or r0 is None:
        return None
    dec_in = token_decimals(rpc, token_in)
    dec_out = token_decimals(rpc, token_out)
    in_h = amount_in / 10 ** dec_in
    if t0.lower() == token_in.lower():
        out_h = cp_out(r0 / 10 ** dec_in, r1 / 10 ** dec_out, in_h)
    else:
        out_h = cp_out(r1 / 10 ** dec_in, r0 / 10 ** dec_out, in_h)
    return int(out_h * 10 ** dec_out)

# Token universe for pair census. Every address is verified on-chain at scan
# time (code + decimals); anything misremembered or dead simply drops out.
TOKEN_UNIVERSE = {
    WETH:  {"sym": "WETH",   "decimals": 18},
    USDC:  {"sym": "USDC",   "decimals": 6},
    USDCE: {"sym": "USDC.e", "decimals": 6},
    "0x912ce59144191c1204e64559fe8253a0e49e6548": {"sym": "ARB",  "decimals": 18},
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": {"sym": "WBTC", "decimals": 8},
    "0xf97f4df75117a78c1a5a0dbb814af92458539fb4": {"sym": "LINK", "decimals": 18},
    "0xfa7f8980b0f1e64a2062791cc3b0871572f1f7f0": {"sym": "UNI",  "decimals": 18},
    "0xd4d42f0b6def4ce0383636770ef773390d85c61a": {"sym": "SUSHI","decimals": 18},
    "0x11cdb42b0eb46d95f990bedd4695a6e3fa034978": {"sym": "CRV",  "decimals": 18},
    "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a": {"sym": "GMX",  "decimals": 18},
}

# master token table: universe lookup
TOKENS = TOKEN_UNIVERSE

MIN_POOL_USD = 2_000  # a pool must hold >= this (USD side) to count


def discover_pools(rpc):
    """Census: every (factory x quote) pair for the token universe, keeping
    only pools with real liquidity. Returns list of pool dicts with the
    quote token attached."""
    pools = []
    quotes = [t for t in TOKEN_UNIVERSE if t != WETH]
    for fname, factory in [("UniV2", UNIV2_FACTORY), ("Sushi", SUSHI_FACTORY)]:
        for quote in quotes:
            qname = TOKEN_UNIVERSE[quote]["sym"]
            pair = univ2_pair(rpc, factory, WETH, quote)
            if not pair:
                continue
            p = load_pool(rpc, f"{fname} WETH/{qname}", pair)
            if not p:
                continue
            br, qr = pool_side(p, WETH, quote)
            if not br or not qr:
                continue
            p["quote"] = quote
            p["quote_reserve"] = human(qr, quote)
            p["weth_reserve"] = human(br, WETH)
            # USD depth: stable quotes direct; else WETH leg * est ETH price
            usd = (p["quote_reserve"] if quote in (USDC, USDCE)
                   else p["weth_reserve"] * 2400.0)
            if usd < MIN_POOL_USD:
                continue
            pools.append(p)
    return pools


def scan_once(rpc):
    """Full pass. Returns dict with pools, eth_usd, gas, and actionable edges."""
    gas_price_wei = uint(rpc.call("eth_gasPrice", [])) or 0
    pools = discover_pools(rpc)

    # ETH/USD from the deepest stable-quoted pool
    eth_usd, best_depth = None, 0
    for p in pools:
        if p["quote"] in (USDC, USDCE) and p["weth_reserve"] > best_depth:
            eth_usd = p["quote_reserve"] / p["weth_reserve"]
            best_depth = p["weth_reserve"]
    if not eth_usd:
        return {"error": "no stable-quoted pool for ETH pricing"}

    gas_usd = (gas_price_wei * GAS_UNITS / 1e18) * eth_usd

    edges = []
    report = []
    quotes = {p["quote"] for p in pools}
    for quote in quotes:
        qname = TOKEN_UNIVERSE[quote]["sym"]
        pair_pools = [p for p in pools if p["quote"] == quote]
        if len(pair_pools) < 2:
            continue
        prices = sorted(price_of(p, WETH, quote) for p in pair_pools)
        ref = prices[len(prices) // 2]  # quote-per-WETH (display only)
        for i in range(len(pair_pools)):
            for j in range(len(pair_pools)):
                if i >= j:
                    continue
                a, b = pair_pools[i], pair_pools[j]
                disl_bps = abs(price_of(a, WETH, quote)
                               / price_of(b, WETH, quote) - 1) * 1e4
                # profit is in WETH units -> USD at eth_usd
                best = best_two_pool_arb(a, b, WETH, quote, eth_usd)
                row = {"pair": f"WETH/{qname}", "venue_a": a["name"],
                       "venue_b": b["name"], "dislocation_bps": round(disl_bps, 2)}
                if best:
                    direction, size, gross = best
                    loan_fee_usd = size * eth_usd * AAVE_FLASH_FEE
                    net = gross - gas_usd - loan_fee_usd
                    row.update({
                        "direction": direction,
                        "size_weth": round(size, 6),
                        "gross_usd": round(gross, 4),
                        "loan_fee_usd": round(loan_fee_usd, 4),
                        "gas_usd": round(gas_usd, 4),
                        "net_usd": round(net, 4),
                    })
                    if net > SAFETY_MARGIN_USD:
                        row["edge"] = True
                        edges.append(row)
                report.append(row)

    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chain_head": uint(rpc.call("eth_blockNumber", [])),
        "eth_usd": round(eth_usd, 2),
        "gas_gwei": round(gas_price_wei / 1e9, 4),
        "gas_usd": round(gas_usd, 4),
        "pools": [{"name": p["name"], "address": p["address"],
                   "weth_reserve": round(p["weth_reserve"], 4),
                   "quote_reserve": round(p["quote_reserve"], 2)}
                  for p in pools],
        "pairs_scanned": len(report),
        "edges": edges,
        "detail": report,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["scan"])
    ap.add_argument("--rpc", default="https://arb1.arbitrum.io/rpc")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rpc = RPC(args.rpc, timeout=60, retries=3)
    cycle = 0
    while True:
        cycle += 1
        try:
            result = scan_once(rpc)
            if args.json:
                print(json.dumps(result))
            else:
                print(f"[{result.get('ts')}] head={result.get('chain_head')} "
                      f"ETH=${result.get('eth_usd')} gas={result.get('gas_gwei')} gwei "
                      f"(${result.get('gas_usd')})")
                for p in result.get("pools", []):
                    print(f"  pool {p['name']:<22} WETH reserve {p['weth_reserve']:>12,.2f}")
                for r in result.get("detail", []):
                    line = (f"  {r['pair']:<12} {r['venue_a']:<20} vs "
                            f"{r['venue_b']:<20} {r['dislocation_bps']:>8.2f} bps")
                    if r.get("size_weth") is not None:
                        line += (f" | size {r['size_weth']} WETH "
                                 f"gross ${r['gross_usd']} net ${r['net_usd']}")
                        if r.get("edge"):
                            line += "  *** EDGE ***"
                    print(line)
                print(f"  => {len(result.get('edges', []))} actionable edges")
        except Exception as e:
            print(f"[scan error] {e}", flush=True)
        if args.interval <= 0:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
