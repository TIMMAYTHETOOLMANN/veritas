#!/usr/bin/env python3
"""
census_fast.py — VERITAS parallel Camelot census accelerator.

Enumerates the Camelot V2 factory (allPairs(i)) with a bounded thread pool to
register all WETH/USDC/USDC.e-hub-quoted pairs + their USD depth far faster
than the serial pool_registry.py loop. Checkpointed to census_progress table
(name='camelot_census_fast') so it resumes where it left off.

READ-ONLY. No signing, no broadcast.

Usage:
  python3 census_fast.py                 # run to completion (auto-resume)
  python3 census_fast.py --workers 16 --batch 500
"""
import argparse
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")

WETH  = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC  = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
HUBS  = {WETH, USDC, USDCE}

CAMELOT_FACTORY = "0x6eccab422d763ac031210895c81787e87b43a652"

RPC_URLS = [
    "https://gateway.tenderly.co/public/arbitrum",
    "https://arbitrum.drpc.org",
    "https://arbitrum.publicnode.com",
]

ETH_USD = 2489.0
MIN_DEPTH = 500.0
MAX_SANE_POOL_USD = 500_000_000.0


def parse_addr(res):
    if not res or len(res) < 66:
        return None
    tail = res[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def mk_rpc(idx):
    url = RPC_URLS[idx % len(RPC_URLS)]
    return RPC(url, timeout=15, retries=1)


class Store:
    """Thread-safe batch upsert into veritas.db."""

    def __init__(self):
        self.buf = []

    def add(self, pair, t0l, t1l, depth):
        self.buf.append((pair, t0l, t1l, depth))

    def flush(self):
        if not self.buf:
            return
        conn = sqlite3.connect(DB, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        with conn:
            for pair, t0l, t1l, depth in self.buf:
                conn.execute(
                    "INSERT INTO pools(pair_addr, venue, kind, token0, token1, "
                    "fee_tier, reserve0, reserve1, usd_depth, first_seen, last_checked) "
                    "VALUES(?,?,?,?,?,0,0,0,?,?,?) "
                    "ON CONFLICT(pair_addr) DO UPDATE SET "
                    "usd_depth=excluded.usd_depth, last_checked=excluded.last_checked",
                    (pair, "camelot", "v2", t0l, t1l, depth,
                     time.strftime("%Y-%m-%d"), time.strftime("%Y-%m-%d %H:%M:%S")),
                )
        conn.close()
        self.buf = []


def probe_one(idx, worker):
    """Probe Camelot allPairs(idx): return (pair, t0l, t1l, depth) or None."""
    rpc = mk_rpc(worker)
    try:
        r = rpc.eth_call(CAMELOT_FACTORY, "0x1e3dd18b" + f"{idx:064x}")
        pair = parse_addr(r)
        if not pair:
            return None
        t0 = parse_addr(rpc.eth_call(pair, "0x0dfe1681"))
        t1 = parse_addr(rpc.eth_call(pair, "0xd21220a7"))
        if not t0 or not t1:
            return None
        t0l, t1l = t0.lower(), t1.lower()
        if t0l not in HUBS and t1l not in HUBS:
            return (pair, t0l, t1l, 0.0)   # long-tail x long-tail: no depth
        # hub-quoted: fetch reserves + depth
        res = rpc.eth_call(pair, "0x0902f1ac")
        if not res or len(res) < 130:
            return (pair, t0l, t1l, 0.0)
        r0 = int(res[2:66], 16)
        r1 = int(res[2 + 64:2 + 128], 16)
        d0 = r0 / 10 ** _dec(rpc, t0l)
        d1 = r1 / 10 ** _dec(rpc, t1l)
        depth = 0.0
        if t1l == WETH:
            depth = 2 * d1 * ETH_USD
        elif t0l == WETH:
            depth = 2 * d0 * ETH_USD
        elif t1l in (USDC, USDCE):
            depth = 2 * d1
        elif t0l in (USDC, USDCE):
            depth = 2 * d0
        if depth > MAX_SANE_POOL_USD:
            depth = 0.0
        return (pair, t0l, t1l, depth)
    except Exception:
        return None


_dec_cache = {}
def _dec(rpc, addr):
    if addr in _dec_cache:
        return _dec_cache[addr]
    try:
        r = rpc.eth_call(addr, "0x313ce567")
        d = int(r[2:66], 16) if r and len(r) >= 66 else 18
    except Exception:
        d = 18
    _dec_cache[addr] = d
    return d


def load_checkpoint():
    conn = sqlite3.connect(DB, timeout=30)
    row = conn.execute(
        "SELECT next_idx FROM census_progress WHERE name='camelot_census_fast'"
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] > 0 else 0


def save_checkpoint(idx):
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    with conn:
        conn.execute(
            "INSERT INTO census_progress(name, next_idx, updated) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET next_idx=excluded.next_idx, "
            "updated=excluded.updated",
            ("camelot_census_fast", idx, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--batch", type=int, default=400)
    ap.add_argument("--max-pairs", type=int, default=0)
    args = ap.parse_args()

    rpc0 = mk_rpc(0)
    n = uint(rpc0.eth_call(CAMELOT_FACTORY, "0x574f2ba3")) or 0
    print(f"[census-fast] Camelot factory reports {n:,} pairs", flush=True)

    start = load_checkpoint()
    end = n if not args.max_pairs else min(n, args.max_pairs)
    print(f"[census-fast] resuming from {start:,} -> {end:,} (workers={args.workers})", flush=True)

    store = Store()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for batch_start in range(start, end, args.batch):
            batch_end = min(batch_start + args.batch, end)
            futs = {}
            for i in range(batch_start, batch_end):
                futs[ex.submit(probe_one, i, i % args.workers)] = i
            for fut in as_completed(futs):
                r = fut.result()
                if r:
                    pair, t0l, t1l, depth = r
                    if depth >= MIN_DEPTH or depth == 0.0:
                        store.add(pair, t0l, t1l, depth)
            store.flush()
            save_checkpoint(batch_end)
            rate = (batch_end - start) / (time.time() - t0)
            print(f"[census-fast] {batch_end:,}/{end:,} "
                  f"({rate:.0f} pairs/s)", flush=True)

    store.flush()
    save_checkpoint(end)
    dt = time.time() - t0
    print(f"[census-fast] DONE {end:,} pairs in {dt:.1f}s", flush=True)


if __name__ == "__main__":
    main()