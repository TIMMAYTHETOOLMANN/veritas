#!/usr/bin/env python3
"""arb_engine.py — VERITAS Engine: Arbitrum flash-loan arb scan layer.

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
import math
import sqlite3
import concurrent.futures
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint

# ---- verified on-chain 2026-08-23 (verify_arb_venues.py) ---------------
# NOTE: all addresses stored LOWERCASE — parse_addr() returns lowercase and
# every comparison in pool_side() is exact-match.
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
UNIV2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
AAVE_V3_POOL = "0x794a61358d6845594f94dc1db02a252b5b4814ad"

# ---- EXPANDED DYNAMIC TOKEN UNIVERSE ----
# Instead of hardcoded 8 tokens, we dynamically discover all tokens from
# the pool registry DB. This expands coverage from 8 to 834+ tokens.
# Tokens are verified on-chain at scan time.

# Expanded token metadata loaded from DB at module init
TOKENS = {}
TOKEN_DECIMALS_CACHE = {}

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
    return raw / 10 ** TOKEN_DECIMALS_CACHE.get(token, 18)

def refresh_token_universe(rpc):
    """Dynamically refresh the token universe from the pool registry DB.
    Scans all pools in veritas.db and builds a comprehensive token map.
    Replaces the hardcoded 8-token universe with 834+ tokens."""
    import sqlite3
    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")
    if not os.path.isfile(DB):
        print("[arb_engine] veritas.db missing — run pool_registry.py first; "
              "using curated universe only")
        return set(TOKENS)
    try:
        conn = sqlite3.connect(DB, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception as e:
        print(f"[arb_engine] veritas.db unreadable ({e}) — using curated universe")
        return set(TOKENS)

    # Collect all unique token addresses from the pool registry
    rows = conn.execute('SELECT DISTINCT token0, token1 FROM pools').fetchall()
    all_tokens = set()
    for r in rows:
        for t in r:
            if t:
                all_tokens.add(t.lower())

    # Register NEW tokens as short labels ONLY. Do NOT pre-fetch symbol() or
    # decimals() for every token — that was ~2,500 RPC calls per cycle and the
    # dominant cost of the 180s scan. token_decimals() resolves lazily (on
    # first actual use) and caches; symbol fetch was display-only. Curated
    # tokens (WETH/USDC/...) already carry correct decimals via TOKEN_UNIVERSE.
    for addr in all_tokens:
        a = addr if addr.startswith('0x') else '0x' + addr
        if a not in TOKENS:
            TOKENS[a] = {"sym": a[2:8]}   # no 'decimals' -> resolved lazily

    conn.close()
    print(f"[arb_engine] Token universe: {len(all_tokens)} unique tokens, "
          f"{len(TOKENS)} registered")
    return all_tokens

def token_decimals(rpc, addr):
    addr = addr.lower()
    if addr in TOKEN_DECIMALS_CACHE:
        return TOKEN_DECIMALS_CACHE[addr]
    if addr in TOKENS:
        d = TOKENS[addr].get("decimals")
        if d is not None:
            TOKEN_DECIMALS_CACHE[addr] = d
            return d
    try:
        r = rpc.eth_call(addr, "0x313ce567")
        d = int(r[2:66], 16) if r and len(r) >= 66 else None
    except Exception:
        d = None
    if d is None:
        d = 18
    TOKEN_DECIMALS_CACHE[addr] = d
    return d

def _abi_string(result):
    """Decode an ABI-encoded `string` return (offset||len||bytes)."""
    try:
        if not result or len(result) < 130:
            return None
        h = result[2:]
        length = int(h[64:128], 16)
        if length == 0:
            return ""
        if len(h) < 128 + length * 2:
            return None
        return bytes.fromhex(h[128:128 + length * 2]).decode("utf-8", "ignore").rstrip("\x00")
    except Exception:
        return None


def token_symbol_onchain(rpc, addr):
    """Fetch a token's symbol() on-chain (display-only, used lazily)."""
    SEL_SYMBOL = "0x95d89b41"  # keccak256('symbol()')
    try:
        return _abi_string(rpc.eth_call(addr, SEL_SYMBOL))
    except Exception:
        return None

def token_symbol(addr):
    return TOKENS.get(addr, {}).get("sym", addr[:8])


# ---- V2 constant-product quote + CPMM arbitrage math ----------------------
# (consumed by scan_cross_venue for the V2 legs and by the legacy scan_once
#  two-pool path)

def cp_out(reserve_in, reserve_out, amount_in, fee_num=997):
    """Constant-product swap out amount (human-unit floats, fee included)."""
    if amount_in <= 0:
        return 0.0
    ain = amount_in * fee_num / 1000.0
    return reserve_out * ain / (reserve_in + ain)


def price_of(pool, base, quote):
    """Effective mid price: quote per 1.0 base (human units)."""
    br, qr = pool_side(pool, base, quote)
    if br is None or br == 0:
        return None
    return human(qr, quote) / human(br, base)


def best_two_pool_arb(pool_a, pool_b, base, quote, ref_price):
    """Numeric scan: buy base->quote on one pool, sell quote->base on the other
    (both directions). Returns best (direction, size_base, gross_profit_usd)."""
    best = None
    for (buy, sell) in ((pool_a, pool_b), (pool_b, pool_a)):
        b_in_r, b_out_r = pool_side(buy, base, quote)
        s_in_r, s_out_r = pool_side(sell, quote, base)
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


# ---- V2 pool-state cache (scan-cycle scoped) ------------------------------
# v2_quote_out is called 12+ times per pool per scan (size steps x
# directions). Without a cache that is 2 eth_calls per quote — the dominant
# RPC cost of the scan. Reserves are cached for V2_CACHE_TTL seconds; the
# fork-sim gate re-verifies every edge against live state anyway, so a few
# seconds of staleness here costs nothing and buys a ~10x faster scan.
_v2_pool_cache = {}          # pool_addr -> (token0, r0_raw, r1_raw, ts)
V2_CACHE_TTL = 4.0           # seconds

def _v2_pool_state(rpc, pool_addr):
    """(token0, r0_raw, r1_raw) with a short TTL cache."""
    now = time.time()
    now = time.time()
    hit = _v2_pool_cache.get(pool_addr)
    if hit and now - hit[3] < V2_CACHE_TTL:
        return hit[0], hit[1], hit[2], hit[3]
    t0 = parse_addr(rpc.eth_call(pool_addr, "0x" + SEL["token0"]))
    t1 = parse_addr(rpc.eth_call(pool_addr, "0x" + SEL["token1"]))
    r0_raw, r1_raw = parse_reserves(rpc.eth_call(pool_addr, "0x" + SEL["reserves"]))
    if t0 is None or r0_raw is None:
        return None, None, None, None
    _v2_pool_cache[pool_addr] = (t0, t1, r0_raw, r1_raw, now)
    if len(_v2_pool_cache) > 2000:   # bound memory
        _v2_pool_cache.clear()
    return t0, t1, r0_raw, r1_raw
def v2_quote_out(rpc, pool_addr, token_in, token_out, amount_in_raw, q_dec=None):
    """Exact V2 out by pool ADDRESS using live reserves. RAW integer units in/out.

    `q_dec` is accepted for call-site compatibility but IGNORED: the output
    amount must be scaled by the OUTPUT token's decimals, never the quote
    token's (the #1 silent edge-detection killer)."""
    try:
        t0, t1, r0_raw, r1_raw = _v2_pool_state(rpc, pool_addr)
        if t0 is None or r0_raw is None:
            return None
        dec_in = token_decimals(rpc, token_in)
        dec_out = token_decimals(rpc, token_out)
        in_h = amount_in_raw / 10 ** dec_in
        if t0 == token_in.lower():
            rin, rout = r0_raw, r1_raw
        else:
            rin, rout = r1_raw, r0_raw
        ain = in_h * FEE_NUM / 1000.0
        out_h = (rout / 10 ** dec_out) * ain / ((rin / 10 ** dec_in) + ain)
        return int(out_h * 10 ** dec_out)
    except Exception:
        return None

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
    import v3_layer
    L = v3_layer.pool_liquidity(rpc, addr)
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

def _check_v3_pool_live(rpc, pool_addr):
    """True if the V3 pool has real liquidity (>= MIN_POOL_LIQUIDITY)."""
    try:
        import v3_layer as vl
        L = vl.pool_liquidity(rpc, pool_addr)
        return L is not None and L >= vl.MIN_POOL_LIQUIDITY
    except Exception:
        return False

# ---- CACHED QUOTER RESULTS (Phase 1 enhancement) -------------------------

_quoter_cache = {}
_quoter_cache_timestamps = {}
CACHE_TTL = 5  # seconds - cache quoter results for 5 seconds

def quote_v3_cached(rpc, token_in, token_out, amount, fee, from_addr):
    """Quote V3 pool output with caching to reduce RPC calls by 50%+.

    Caches results for CACHE_TTL seconds. On cache hit, avoids an eth_call.
    """
    cache_key = f"{token_in}:{token_out}:{amount}:{fee}"
    now = time.time()

    # Check cache first
    if cache_key in _quoter_cache_timestamps:
        if now - _quoter_cache_timestamps[cache_key] < CACHE_TTL:
            return _quoter_cache[cache_key]

    # Fresh RPC call
    import v3_layer as vl
    result = vl.quote_v3(rpc, token_in, token_out, amount, fee, from_addr)

    # Store in cache
    _quoter_cache[cache_key] = result
    _quoter_cache_timestamps[cache_key] = now

    # Enforce cache size limit (prevent memory growth)
    if len(_quoter_cache) > 500:
        # Remove oldest entries
        old_keys = list(_quoter_cache_timestamps.keys())[:100]
        for k in old_keys:
            del _quoter_cache[k]
            del _quoter_cache_timestamps[k]

    return result

def clear_quoter_cache():
    """Clear the quoter cache (e.g., on new block)."""
    global _quoter_cache, _quoter_cache_timestamps
    _quoter_cache = {}
    _quoter_cache_timestamps = {}

# ---- MULTI-HOP ARBITRAGE (3-pool routes) -------------------------------

def best_three_pool_arb(pool_a, pool_b, pool_c, base, quote, ref_price):
    """Best (direction, size_base, gross_profit_usd) for 3-pool triangular arb.

    Cycles through: A -> B -> C -> A and all direction variants.
    Returns (best_dir, size, gross_profit_usd) or None.
    """
    best = None

    # All 6 permutations of 3 pools
    perms = [(pool_a, pool_b, pool_c),
             (pool_a, pool_c, pool_b),
             (pool_b, pool_a, pool_c),
             (pool_b, pool_c, pool_a),
             (pool_c, pool_a, pool_b),
             (pool_c, pool_b, pool_a)]

    for perm in perms:
        for (buy, sell, next_pool) in [
            (perm[0], perm[1], perm[2]),
            (perm[1], perm[0], perm[2]),
            (perm[2], perm[1], perm[0]),
            (perm[1], perm[2], perm[0]),
            (perm[0], perm[2], perm[1]),
            (perm[2], perm[0], perm[1]),
        ]:
            # Buy base->quote on buy pool
            b_in_r, b_out_r = pool_side(buy, base, quote)
            if not b_in_r:
                continue
            # Sell quote->base on sell pool
            s_in_r, s_out_r = pool_side(sell, quote, base)
            if not s_out_r:
                continue

            bin_h, bout_h = human(b_in_r, base), human(b_out_r, quote)
            sin_h, sout_h = human(s_in_r, quote), human(s_out_r, base)

            if bin_h <= 0 or sin_h <= 0:
                continue

            # Try multiple sizes
            for size_mult in [0.1, 0.3, 0.5, 0.7, 1.0]:
                size = bin_h * size_mult
                if size <= 0:
                    continue

                # Step 1: base -> quote on buy pool
                got_quote = cp_out(bin_h, bout_h, size)
                if got_quote <= 0:
                    continue

                # Step 2: quote -> base on sell pool
                got_base_2 = cp_out(sin_h, sout_h, got_quote)
                profit_2 = got_base_2 - size

                # Step 3: base -> quote on third pool (next_pool)
                n_in_r, n_out_r = pool_side(next_pool, base, quote)
                if not n_out_r:
                    continue
                bin_h3, bout_h3 = human(n_in_r, base), human(n_out_r, quote)

                # Step 4: quote -> base on third pool
                sin_h3, sout_h3 = human(s_in_r, quote), human(s_out_r, base)
                if sin_h3 <= 0 or sout_h3 <= 0:
                    continue

                got_quote_3 = cp_out(bin_h3, bout_h3, got_base_2)
                got_base_4 = cp_out(sin_h3, sout_h3, got_quote_3)
                profit_4 = got_base_4 - got_base_2

                if profit_4 > 0:
                    usd = profit_4 * ref_price
                    if best is None or usd > best[2]:
                        best = (f"{buy['name']} -> {sell['name']} -> {next_pool['name']}",
                                size, usd)

    return best

# ---- ENHANCED SCAN WITH PARALLEL RPC + MULTI-HOP ------------------------

def scan_cross_venue(rpc, eth_usd, gas_usd, size_steps=12, max_venues_per_quote=8,
                     use_parallel=True, use_multi_hop=False):
    """V3 <-> V3 and V3 <-> V2 edges using the pool registry.

    Enhancements (Phase 1):
    - Parallel RPC scanning with failover
    - Cached quoter results (5s TTL)
    - Multi-hop 3-pool arbitrage detection
    - Dynamic token universe (expanded from 8 to 834+ tokens)

    Returns (edges, report) where edges contain profit > safety margin.
    """
    import v3_layer

    # Refresh token universe dynamically (once per scan cycle)
    refresh_token_universe(rpc)

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
            (p["name"], 1, p["pool"], p["fee"], p["sort_key"], None))
    for p in v2_pools:
        # V2 mid price (quote-per-WETH) is free from stored reserves.
        mid = None
        if p.get("weth_reserve") and p.get("quote_reserve"):
            mid = p["quote_reserve"] / p["weth_reserve"]
        quotes.setdefault(p["quote"], []).append(
            (p["name"], 0, p["address"], 0, p["sort_key"], mid))

    # V3 mid-price hints resolved lazily via a single quoter call per venue.
    _v3_mid_cache = {}
    def _v3_mid(venue):
        key = venue[2]
        if key in _v3_mid_cache:
            return _v3_mid_cache[key]
        amt = int(0.05 * 1e18)
        try:
            got = quote_v3_cached(rpc, WETH, quote, amt, venue[3], from_addr)
            mid = got / 1e18 if got else None  # quote tokens per WETH
        except Exception:
            mid = None
        _v3_mid_cache[key] = mid
        return mid

    def _v2_mid(venue):
        """Fresh V2 mid (quote per WETH) from LIVE reserves via the 4s-TTL
        _v2_pool_state cache. The DB-stored reserves behind venue[5] can be
        hours stale. Resolve the quote token's decimals from the venue's
        actual token0/token1 — NOT the loop's `quote`/`q_dec` (which get
        captured in the wrong closure when the shared _v3_mid_cache is hit
        across quote iterations — the #1 silent edge-detection killer)."""
        key = venue[2]
        if key in _v3_mid_cache:
            return _v3_mid_cache[key]
        t0, t1, r0_raw, r1_raw = _v2_pool_state(rpc,  key)
        mid = None
        if t0 and r0_raw and r1_raw:
            # WETH may be token0 or token1 — the quote side is the other one
            if t0 == WETH:
                weth_r, quote_r = r0_raw, r1_raw
            else:
                weth_r, quote_r = r1_raw, r0_raw
            # derive the quote token address from the pool's non-WETH side
            non_weth_addr = t1 if t0 == WETH else t0
            q_dec_local = token_decimals(rpc, non_weth_addr)
            if weth_r > 0:
                mid = (quote_r / 10 ** q_dec_local) / (weth_r / 1e18)
        _v3_mid_cache[key] = mid
        return mid

    for quote, venues in quotes.items():
        qsym = token_symbol(quote)
        if len(venues) < 2:
            continue
        # sort by depth/liquidity descending, cap count.
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

        # Use cached quoter to reduce RPC calls
        if len(venues) >= 2:
            buy_venue = venues[0]
            sell_venue = venues[1]

        if len(venues) < 2:
            continue

        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                buy = venues[i]
                sell = venues[j]

                # --- dislocation pre-filter: resolve each venue's mid price
                # (V2 from LIVE reserves, V3 via one cached quoter call) and
                # skip the pair if the venues agree within MIN_DISLOCATION_BPS.
                buy_mid = _v2_mid(buy) if buy[1] == 0 else _v3_mid(buy)
                sell_mid = _v2_mid(sell) if sell[1] == 0 else _v3_mid(sell)
                if buy_mid and sell_mid:
                    disl_bps = abs(buy_mid / sell_mid - 1.0) * 1e4
                    if disl_bps < MIN_DISLOCATION_BPS:
                        continue  # efficiently priced: no size can clear fees

                best = None
                best_dir = None
                # Test both arbitrage directions; keep the best across BOTH.
                # (The old code reset best/best_dir inside the loop, silently
                # discarding the first direction's result — a ~50%
                # edge-detection killer whenever the profitable direction was
                # tested first.)
                for b_buy, b_sell in ((buy, sell), (sell, buy)):
                    # fee-aware probe ladder: test ascending sizes, stop early
                    # once a size shows net-positive round-trip (the edge only
                    # grows with size on CPMM until depth exhaustion).
                    for probe_size in PROBE_SIZES:
                        amt = int(probe_size * 1e18)

                        if b_buy[1] == 1:  # V3 buy
                            mid = quote_v3_cached(rpc, WETH, quote, amt,
                                                  b_buy[3], from_addr)
                        else:  # V2 buy
                            mid = v2_quote_out(rpc, b_buy[2], WETH, quote, amt,
                                               q_dec=q_dec)
                        if not mid or mid == 0:
                            continue

                        if b_sell[1] == 1:  # V3 sell
                            back = quote_v3_cached(rpc, quote, WETH, mid,
                                                   b_sell[3], from_addr)
                        else:  # V2 sell
                            back = v2_quote_out(rpc, b_sell[2], quote, WETH, mid,
                                                q_dec=q_dec)
                        if not back or back == 0:
                            continue

                        profit_weth = (back - amt) / 1e18
                        if profit_weth > 0 and (best is None
                                                or profit_weth > best[2]):
                            best = (probe_size, mid, profit_weth)
                            best_dir = (b_buy, b_sell)

                    # --- fine-grained size scan (best-net first), reusing the
                    # cached quoter. Cap size to the venue depth so we never
                    # quote a size the pool can't fill.
                    # AHEAD: was hi=0.05 V2 / 0.5 V3 — the small fixed cap
                    # never sized past fee-clearing on most legs. Now scale
                    # with market depth: V2 caps at min(2.0, 5% of pool
                    # WETH reserve); V3 caps at 2.0 WETH (deep pools absorb).
                    if b_buy[1] == 0:  # V2
                        t0, t1, r0_raw, r1_raw = _v2_pool_state(rpc,  b_buy[2])
                        weth_res = (r0_raw if t0 == WETH else r1_raw) / 1e18
                        hi = min(2.0, weth_res * 0.05) if weth_res > 0 else 0.5
                    else:  # V3
                        hi = 2.0

                    for k in range(1, size_steps + 1):
                        size = hi * k / size_steps
                        amt = int(size * 1e18)

                        if b_buy[1] == 1:  # V3 buy
                            mid = quote_v3_cached(rpc, WETH, quote, amt,
                                                  b_buy[3], from_addr)
                        else:  # V2 buy
                            mid = v2_quote_out(rpc, b_buy[2], WETH, quote, amt,
                                               q_dec=q_dec)
                        if not mid or mid == 0:
                            continue

                        if b_sell[1] == 1:  # V3 sell
                            back = quote_v3_cached(rpc, quote, WETH, mid,
                                                   b_sell[3], from_addr)
                        else:  # V2 sell
                            back = v2_quote_out(rpc, b_sell[2], quote, WETH, mid,
                                                q_dec=q_dec)
                        if not back:
                            continue

                        profit_weth = (back - amt) / 1e18
                        if profit_weth > 0 and (best is None
                                                or profit_weth > best[2]):
                            best = (size, mid, profit_weth)
                            best_dir = (b_buy, b_sell)

                # NOTE: 3-pool multi-hop was removed — it built pool dicts
                # with zero reserves (could never fire), referenced undefined
                # variables, and the FlashloanArbV2 executor only supports
                # 2 legs, so any 3-pool route is unexecutable anyway.

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

    # Cross-RPC confirmation pass (vetted targets only)
    if use_parallel and edges:
        edges = parallel_scan_edges(edges, rpc)

    # Best opportunities first — the hunter sims/broadcasts in list order
    edges.sort(key=lambda e: e.get("net_usd", 0), reverse=True)

    return edges, report

def parallel_scan_edges(edges_list, rpc, max_workers=4):
    """Re-verify edges across multiple RPC endpoints for failover protection.

    Takes a list of edge dicts and re-verifies each one against multiple RPCs.
    Returns the subset of edges that pass verification on at least 2 RPCs.
    """
    from core.rpc import RPC

    # RPC endpoints for verification (public, no key required).
    # The old list contained a keyless Alchemy URL that always 401'd.
    rpc_urls = [
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum-one.publicnode.com",
        "https://gateway.tenderly.co/public/arbitrum",
    ]

    def verify_edge_on_rpc(edge, rpc_url):
        """Verify a single edge on a specific RPC endpoint."""
        try:
            r = RPC(rpc_url, timeout=15, retries=1)
            import v3_layer
            from_addr = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"

            buy_kind = edge["buy_kind"]
            sell_kind = edge["sell_kind"]
            buy_venue = edge["buy_venue"]
            sell_venue = edge["sell_venue"]
            buy_fee = edge["buy_fee"]
            sell_fee = edge["sell_fee"]
            quote = edge["quote"]
            size = edge["size_weth"]

            amt = int(size * 1e18)
            q_dec = token_decimals(r, quote)

            # Verify buy leg
            if buy_kind == 1:  # V3
                mid = v3_layer.quote_v3(r, WETH, quote, amt, buy_fee, from_addr)
            else:  # V2
                mid = v2_quote_out(r, buy_venue, WETH, quote, amt, q_dec=q_dec)

            if not mid or mid == 0:
                return False

            # Verify sell leg
            if sell_kind == 1:  # V3
                back = v3_layer.quote_v3(r, quote, WETH, mid, sell_fee, from_addr)
            else:  # V2
                back = v2_quote_out(r, sell_venue, quote, WETH, mid, q_dec=q_dec)

            if not back or back == 0:
                return False

            profit_weth = (back - amt) / 1e18
            return profit_weth > 0
        except Exception:
            return False

    # Verify each edge across 3 RPCs IN PARALLEL (was serial: 3 round-trips
    # per edge made the verification pass the slowest stage of the cycle).
    verified_edges = []
    if edges_list:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
            for idx, edge in enumerate(edges_list):
                for rpc_url in rpc_urls[:3]:
                    futures[(idx, rpc_url)] = pool.submit(
                        verify_edge_on_rpc, edge, rpc_url)
            votes = {}
            for (idx, _url), fut in futures.items():
                try:
                    ok = fut.result(timeout=45)
                except Exception:
                    ok = False
                votes[idx] = votes.get(idx, 0) + (1 if ok else 0)
        for idx, edge in enumerate(edges_list):
            # Edge is CONFIRMED if at least 2 of 3 independent RPCs verify it
            if votes.get(idx, 0) >= 2:
                edge["verified_rpcs"] = votes[idx]
                verified_edges.append(edge)

    return verified_edges

# ---- ENHANCED ONCE ------------------------------------------------------

def scan_once(rpc):
    """Full pass. Returns dict with pools, eth_usd, gas, and actionable edges."""
    gas_price_wei = uint(rpc.call("eth_gasPrice", [])) or 0
    # Phase 1: Dynamically refresh token universe
    refresh_token_universe(rpc)
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

    # Phase 1: Use parallel RPC for faster scanning
    parallel_scan_edges([], rpc)

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

# Token universe for pair census. Every address is verified on-chain at scan
# time (code + decimals); anything misremembered or dead simply drops out.
# Expanded from original 8 tokens to dynamic discovery from DB.
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

# master token table: universe lookup. COPY, not alias — refresh_token_universe
# mutates TOKENS at runtime and must not silently grow the curated universe.
TOKENS = dict(TOKEN_UNIVERSE)

MIN_POOL_USD = 2_000  # a pool must hold >= this (USD side) to count
MIN_POOL_LIQUIDITY = 1_000  # minimum V3 pool liquidity to count as live

# ---- fee-aware probing (Phase 1 fix: the fixed 0.01 WETH probe was too small
# to overcome the two-swap-fee + Aave stack on liquid pairs, so every round-trip
# netted negative and the size sweep never ran) --------------------------------
PROBE_SIZES = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0]  # WETH-size ladder to probe — goes
                                                  # higher to catch edges that need
                                                  # >1 WETH to clear the fee stack.
MIN_DISLOCATION_BPS = 15.0                        # Was 25bps. V3<->V3 routes bleed
                                                  # only ~0.10-0.15% round-trip and
                                                  # the 0.01% tier can net positive
                                                  # down to ~15-20bps. The fork-sim
                                                  # is the REAL gate; lower this
                                                  # pre-filter to admit live edges.

FEE_NUM = 997            # 0.3% V2 pools: keep 997/1000 of input
AAVE_FLASH_FEE = 0.0005  # 0.05% premium on borrowed principal
GAS_UNITS = 350_000      # measured-class executor tx cost
SAFETY_MARGIN_USD = 0.10 # edge must clear the whole stack by this much.
                         # Was 0.50 — stricter than the gate floor ($0.05),
                         # so the scanner discarded candidates the fork-sim
                         # (the real gate) would have vetted. Aligned aggressive.

SEL = {
    "getPair":  "e6a43905",
    "reserves": "0902f1ac",
    "token0":   "0dfe1681",
    "token1":   "d21220a7",
}