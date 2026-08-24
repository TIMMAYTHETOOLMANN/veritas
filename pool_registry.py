#!/usr/bin/env python3
"""
pool_registry.py — VERITAS Phase-1 'Eyes': pool registry in veritas.db.

Three discovery prongs:
  1. CURATED HUB: verified tokens x WETH/USDC/USDC.e quotes, UniV3 (getPool
     all fee tiers) + Sushi/UniV2/Camelot (getPair). Fast, deterministic.
  2. CAMELOT CENSUS: full enumeration of the Camelot V2-style factory
     (4,310 pairs as of 2026-08-23) via allPairs(i) with per-pair metadata
     and reserves for hub-quoted pairs. Long-tail surface.
  3. FRESH LAUNCHES: eth_getLogs walk of V3 PoolCreated + Camelot
     PairCreated in a recent window; auto-registers new pools.

Registry table (SQLite, veritas.db):
  pools(pair_addr PK, venue, kind, token0, token1, fee_tier,
        reserve0, reserve1, usd_depth, first_seen, last_checked)

All read-only against the chain. $0.

Usage:
  python3 pool_registry.py curated        # prong 1
  python3 pool_registry.py camelot [--limit N]
  python3 pool_registry.py fresh [--blocks 100000]
  python3 pool_registry.py stats
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint  # noqa: E402
from core.selectors import kec256  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")

# ---- verified on-chain 2026-08-23 (verify_arb_venues.py + camelot probe) --
WETH   = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC   = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE  = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"

UNIV3_FACTORY   = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
UNIV2_FACTORY   = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY   = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
CAMELOT_FACTORY = "0x6eccab422d763ac031210895c81787e87b43a652"

V3_FEE_TIERS = (100, 500, 3000, 10000)

# curated universe — ONLY addresses verified on-chain in this repo's tools.
# (sushi/crv/gmx/arb/wbtc/link/uni verified by arb_engine census 2026-08-23)
CURATED = {
    WETH:  ("WETH",   18),
    USDC:  ("USDC",   6),
    USDCE: ("USDC.e", 6),
    "0x912ce59144191c1204e64559fe8253a0e49e6548": ("ARB",   18),
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": ("WBTC",  8),
    "0xf97f4df75117a78c1a5a0dbb814af92458539fb4": ("LINK",  18),
    "0xfa7f8980b0f1e64a2062791cc3b0871572f1f7f0": ("UNI",   18),
    "0xd4d42f0b6def4ce0383636770ef773390d85c61a": ("SUSHI", 18),
    "0x11cdb42b0eb46d95f990bedd4695a6e3fa034978": ("CRV",   18),
    "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a": ("GMX",   18),
}

MIN_USD_DEPTH = 2_000


# ---- db -------------------------------------------------------------------

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS pools (
        pair_addr TEXT PRIMARY KEY,
        venue TEXT NOT NULL,
        kind TEXT NOT NULL,
        token0 TEXT NOT NULL,
        token1 TEXT NOT NULL,
        fee_tier INTEGER DEFAULT 0,
        reserve0 REAL DEFAULT 0,
        reserve1 REAL DEFAULT 0,
        usd_depth REAL DEFAULT 0,
        first_seen TEXT,
        last_checked TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS census_progress (
        name TEXT PRIMARY KEY,
        next_idx INTEGER NOT NULL,
        updated TEXT)""")
    conn.commit()
    return conn


def _commit_retry(conn, attempts=5):
    """Commit with backoff over transient 'database is locked' errors."""
    for k in range(attempts):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and k < attempts - 1:
                time.sleep(1.5 * (k + 1))
                continue
            raise


def pad(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def parse_addr(res):
    if not res or len(res) < 66:
        return None
    tail = res[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def upsert_pool(conn, pair, venue, kind, t0, t1, fee_tier=0,
                r0=None, r1=None, depth=None):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with conn:  # auto-commit/rollback per upsert; short lock window
        conn.execute("""INSERT INTO pools
            (pair_addr, venue, kind, token0, token1, fee_tier,
             reserve0, reserve1, usd_depth, first_seen, last_checked)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(pair_addr) DO UPDATE SET
              venue=excluded.venue, kind=excluded.kind,
              reserve0=COALESCE(excluded.reserve0, pools.reserve0),
              reserve1=COALESCE(excluded.reserve1, pools.reserve1),
              usd_depth=COALESCE(excluded.usd_depth, pools.usd_depth),
              last_checked=excluded.last_checked""",
                     (pair.lower(), venue, kind, t0.lower(), t1.lower(),
                      fee_tier, r0, r1, depth, now, now))
# ---- prong 1: curated hub --------------------------------------------------

def scan_curated(rpc, conn):
    hubs = [WETH, USDC, USDCE]
    found = 0
    for token in list(CURATED.keys()):
        for hub in hubs:
            if token == hub:
                continue
            for fee in V3_FEE_TIERS:
                r = rpc.eth_call(UNIV3_FACTORY,
                                 "0x1698ee82" + pad(token) + pad(hub) + f"{fee:064x}")
                pool = parse_addr(r)
                if pool:
                    upsert_pool(conn, pool, "univ3", "v3", token, hub, fee)
                    found += 1
            for venue, factory in (("univ2", UNIV2_FACTORY),
                                   ("sushi", SUSHI_FACTORY),
                                   ("camelot", CAMELOT_FACTORY)):
                r = rpc.eth_call(factory, "0xe6a43905" + pad(token) + pad(hub))
                pool = parse_addr(r)
                if pool:
                    upsert_pool(conn, pool, venue, "v2", token, hub, 0)
                    found += 1
    # final checkpoint + commit
    _commit_retry(conn)
    return found


# ---- prong 2: Camelot full census ------------------------------------------

_dec_cache = {}


def token_decimals(rpc, addr, conn=None):
    key = addr.lower()
    if key in _dec_cache:
        return _dec_cache[key]
    try:
        r = rpc.eth_call(addr, "0x313ce567")
        d = int(r[2:66], 16) if r and len(r) >= 66 else None
    except Exception:
        d = None
    if d is None:
        d = 18  # assume 18 for unknown tokens; reserves still comparable
    _dec_cache[key] = d
    return d


def _save_progress(conn, name, next_idx):
    with conn:
        conn.execute("""INSERT INTO census_progress (name, next_idx, updated)
                        VALUES (?,?,?)
                        ON CONFLICT(name) DO UPDATE SET
                          next_idx=excluded.next_idx,
                          updated=excluded.updated""",
                     (name, next_idx, time.strftime("%Y-%m-%d %H:%M:%S")))


def scan_camelot(rpc, conn, limit=0, min_depth=MIN_USD_DEPTH, eth_usd=2440.0,
                 start=0, max_pairs=0):
    n = uint(rpc.eth_call(CAMELOT_FACTORY, "0x574f2ba3")) or 0
    # auto-resume from checkpoint unless caller pinned an explicit start
    if start == 0:
        row = conn.execute(
            "SELECT next_idx FROM census_progress WHERE name='camelot_census'"
        ).fetchone()
        if row and row[0] > 0:
            start = row[0]
            print(f"[camelot] resuming from checkpoint pair {start:,}")
    end = n if not max_pairs else min(n, start + max_pairs)
    print(f"[camelot] factory reports {n:,} pairs (processing {start:,}..{end:,})")
    hub_quoted = kept = 0
    for i in range(start, end):
        r = rpc.eth_call(CAMELOT_FACTORY, "0x1e3dd18b" + f"{i:064x}")
        pair = parse_addr(r)
        if not pair:
            continue
        t0 = parse_addr(rpc.eth_call(pair, "0x0dfe1681"))
        t1 = parse_addr(rpc.eth_call(pair, "0xd21220a7"))
        if not t0 or not t1:
            continue
        t0l, t1l = t0.lower(), t1.lower()
        hubs = {WETH, USDC, USDCE}
        if t0l not in hubs and t1l not in hubs:
            # long-tail x long-tail: register metadata only (no depth)
            upsert_pool(conn, pair, "camelot", "v2", t0l, t1l, 0)
            continue
        hub_quoted += 1
        res = rpc.eth_call(pair, "0x0902f1ac")
        if not res or len(res) < 130:
            continue
        r0 = int(res[2:66], 16)
        r1 = int(res[2 + 64:2 + 128], 16)
        d0 = r0 / 10 ** token_decimals(rpc, t0l)
        d1 = r1 / 10 ** token_decimals(rpc, t1l)
        depth = 0.0
        if t1l == WETH:
            depth = 2 * d1 * eth_usd
        elif t1l in (USDC, USDCE):
            depth = 2 * d1
        elif t0l == WETH:
            depth = 2 * d0 * eth_usd
        elif t0l in (USDC, USDCE):
            depth = 2 * d0
        if depth >= min_depth:
            upsert_pool(conn, pair, "camelot", "v2", t0l, t1l, 0,
                        r0=d0, r1=d1, depth=depth)
            kept += 1
        if i % 50 == 0:
            _save_progress(conn, "camelot_census", i)
            print(f"[camelot] progress {i:,}/{n:,} "
                  f"(hub-quoted={hub_quoted} kept={kept})", flush=True)
        if limit and kept >= limit:
            break
    _save_progress(conn, "camelot_census", end)
    _commit_retry(conn)
    return hub_quoted, kept


# ---- prong 2b: enrich registered V2 pools with reserves/depth -------------

def enrich(conn, rpc, eth_usd=2440.0):
    """Backfill reserves + USD depth for registered V2 pools that lack it."""
    rows = conn.execute(
        "SELECT pair_addr, token0, token1 FROM pools "
        "WHERE kind='v2' AND (usd_depth IS NULL OR usd_depth=0)").fetchall()
    hubs = {WETH, USDC, USDCE}
    done = skipped = 0
    for pair, t0, t1 in rows:
        if t0 not in hubs and t1 not in hubs:
            skipped += 1  # long-tail x long-tail: no stable depth definable
            continue
        try:
            res = rpc.eth_call(pair, "0x0902f1ac")
        except Exception:
            continue
        if not res or len(res) < 130:
            continue
        r0 = int(res[2:66], 16)
        r1 = int(res[2 + 64:2 + 128], 16)
        d0 = r0 / 10 ** token_decimals(rpc, t0)
        d1 = r1 / 10 ** token_decimals(rpc, t1)
        if t1 == WETH:
            depth = 2 * d1 * eth_usd
        elif t1 in (USDC, USDCE):
            depth = 2 * d1
        elif t0 == WETH:
            depth = 2 * d0 * eth_usd
        else:
            depth = 2 * d0
        with conn:
            conn.execute("UPDATE pools SET reserve0=?, reserve1=?, usd_depth=?, "
                         "last_checked=? WHERE pair_addr=?",
                         (d0, d1, depth, time.strftime("%Y-%m-%d %H:%M:%S"), pair))
        done += 1
        if done % 50 == 0:
            print(f"[enrich] {done} pools with depth", flush=True)
    return done, skipped


# ---- prong 3: fresh launches ----------------------------------------------

TOPIC_V3_POOLCREATED = "0x" + kec256(
    b"PoolCreated(address,address,uint24,int24,address)").hex()
TOPIC_V3_PAIRCREATED = "0x" + kec256(
    b"PairCreated(address,address,address,uint256)").hex()


def scan_fresh(rpc, conn, blocks=100_000):
    head = uint(rpc.call("eth_blockNumber", []))
    frm = head - blocks
    found = 0
    for name, factory, topic, kind, venue in (
            ("univ3", UNIV3_FACTORY, TOPIC_V3_POOLCREATED, "v3", "univ3"),
            ("v2/camelot", CAMELOT_FACTORY, TOPIC_V3_PAIRCREATED, "v2", "camelot"),
            ("univ2", UNIV2_FACTORY, TOPIC_V3_PAIRCREATED, "v2", "univ2"),
            ("sushi", SUSHI_FACTORY, TOPIC_V3_PAIRCREATED, "v2", "sushi")):
        try:
            logs = rpc.call("eth_getLogs", [{
                "address": factory,
                "fromBlock": hex(frm),
                "toBlock": hex(head),
                "topics": [topic],
            }])
        except Exception as e:
            print(f"[fresh] {name} walk failed: {str(e)[:100]}")
            continue
        for lg in logs or []:
            topics = lg.get("topics", [])
            data = lg.get("data", "0x")
            t0 = "0x" + topics[1][26:] if len(topics) > 1 else None
            t1 = "0x" + topics[2][26:] if len(topics) > 2 else None
            if not t0 or not t1:
                continue
            d = data[2:]
            if kind == "v3":
                # UniV3 PoolCreated: token0/token1/fee are INDEXED topics;
                # data = word0 tickSpacing (int24), word1 pool address.
                pool = "0x" + d[64 + 24:128] if len(d) >= 128 else None
                fee = int(topics[3], 16) if len(topics) > 3 else 0
            else:
                # PairCreated: pair address in data word 0
                pool = "0x" + d[24:64] if len(d) >= 64 else None
                fee = 0
            if not pool:
                continue
            upsert_pool(conn, pool, venue, kind, t0, t1, fee)
            found += 1
            print(f"[fresh] {venue} new pool {pool} "
                  f"{'0x'+t0[2:10]}../{'0x'+t1[2:10]}..")
    return found


def stats(conn):
    for row in conn.execute("""SELECT venue, kind, COUNT(*),
                                      SUM(CASE WHEN usd_depth>0 THEN 1 ELSE 0 END)
                               FROM pools GROUP BY venue, kind"""):
        print(f"  {row[0]:<8} {row[1]:<3} total={row[2]:>5,} with-depth={row[3]:>5,}")
    tot = conn.execute("SELECT COUNT(*) FROM pools").fetchone()[0]
    print(f"  TOTAL {tot:,} pools registered")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["curated", "camelot", "fresh",
                                      "stats", "enrich"])
    ap.add_argument("--rpc", default="https://arb1.arbitrum.io/rpc")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-pairs", type=int, default=0,
                    help="process at most this many pairs this run (chunked mode)")
    ap.add_argument("--blocks", type=int, default=100_000)
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args()
    conn = db()
    if args.mode == "stats":
        stats(conn)
        return
    rpc = RPC(args.rpc, timeout=30, retries=3)
    if args.mode == "curated":
        n = scan_curated(rpc, conn)
        print(f"[curated] {n} pools registered")
    elif args.mode == "camelot":
        hq, kept = scan_camelot(rpc, conn, limit=args.limit, start=args.start,
                                max_pairs=args.max_pairs)
        print(f"[camelot] {hq} hub-quoted scanned, {kept} kept "
              f"(>= ${MIN_USD_DEPTH:,} depth)")
    elif args.mode == "fresh":
        n = scan_fresh(rpc, conn, blocks=args.blocks)
        print(f"[fresh] {n} new pools registered in last {args.blocks:,} blocks")
    elif args.mode == "enrich":
        done, skipped = enrich(conn, rpc)
        print(f"[enrich] {done} pools got reserves+depth, {skipped} skipped "
              "(long-tail x long-tail)")


if __name__ == "__main__":
    main()
