#!/usr/bin/env python3
"""
census_v3_venues.py — VERITAS multi-V3-venue census (topic-only PoolCreated walk).

Walks PoolCreated events across ALL V3-fork factories (not just Uniswap) by
filtering on the event topic only — this is the only getLogs shape that works
on the free public RPCs (gateway.tenderly.co works; drpc 400; publicnode 403).

This gives long-tail tokens a SECOND venue (Sushi V3 / Pancake V3 / Ramses V3 /
Camelot V3) for cross-venue arb against their Camelot-V2 liquidity.

Event topic (UniV3-fork PoolCreated):
  0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118
  topics[1]=token0, topics[2]=token1, topics[3]=fee; pool address in DATA word 0.

READ-ONLY. Checkpointed. No signing.

Usage:
  python3 census_v3_venues.py --lookback 3000000   # ~1 year, chunked
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"

RPC_URL = "https://gateway.tenderly.co/public/arbitrum"

TOPIC_POOLCREATED = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"

# Factory -> venue label (for dedupe/labeling). Unknown factories still counted under 'v3_other'
FACTORY_VENUE = {
    "0x1f98431c8ad98523631ae4a59f267346ea31f984": "univ3",
    "0x1af415a1eba07a4986a52b6f2e7de7003d82231e": "sushi_v3",
    "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865": "pancake_v3",
    "0xaa2cd7477c451e703f3b9ba5663334914763edf8": "ramses_v3",
    "0x1a3c9b1d2f0529d97f2afc5136cc23e58f1fd35b": "camelot_v3",
}


def parse_addr(topic_word):
    return "0x" + topic_word[-40:].lower()


def get_logs(rpc, topic, from_block, to_block):
    return rpc._call({
        "jsonrpc": "2.0", "method": "eth_getLogs",
        "params": [{"fromBlock": hex(from_block), "toBlock": hex(to_block),
                    "topics": [topic]}],
    })


def checkpoint(name):
    conn = sqlite3.connect(DB, timeout=30)
    row = conn.execute("SELECT next_idx FROM census_progress WHERE name=?",
                       (name,)).fetchone()
    conn.close()
    return row[0] if row else 0


def save_checkpoint(name, blk):
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    with conn:
        conn.execute(
            "INSERT INTO census_progress(name, next_idx, updated) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET next_idx=excluded.next_idx, "
            "updated=excluded.updated",
            (name, blk, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    conn.close()


def upsert_pool(pair, venue, t0l, t1l, fee):
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    with conn:
        conn.execute(
            "INSERT INTO pools(pair_addr, venue, kind, token0, token1, fee_tier, "
            "reserve0, reserve1, usd_depth, first_seen, last_checked) "
            "VALUES(?,?,?,?,?,?,0,0,-1,?,?) "
            "ON CONFLICT(pair_addr) DO UPDATE SET venue=excluded.venue, "
            "kind=excluded.kind, fee_tier=excluded.fee_tier, "
            "last_checked=excluded.last_checked",
            (pair, venue, "v3", t0l, t1l, fee,
             time.strftime("%Y-%m-%d"), time.strftime("%Y-%m-%d %H:%M:%S")),
        )
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=3000000)
    ap.add_argument("--chunk", type=int, default=100000)
    args = ap.parse_args()

    rpc = RPC(RPC_URL, timeout=20, retries=2)
    head = rpc.eth_blockNumber()
    from_block = max(0, head - args.lookback)
    cp = checkpoint("v3_venues_topic")
    if cp and cp > from_block:
        from_block = cp   # resume
    print(f"[census-v3] head={head:,} walking {from_block:,}..{head:,}", flush=True)

    registered = 0
    chunk = args.chunk
    for fb in range(from_block, head, chunk):
        tb = min(fb + chunk - 1, head)
        try:
            res = get_logs(rpc, TOPIC_POOLCREATED, fb, tb)
        except Exception as e:
            print(f"  [{fb:,}] getLogs fail: {str(e)[:70]}", flush=True)
            continue
        logs = res if isinstance(res, list) else res.get("result", [])
        for lg in logs or []:
            try:
                topics = lg.get("topics", [])
                data = lg.get("data", "0x")
                factory = (lg.get("address") or "").lower()
                t0 = parse_addr(topics[1])
                t1 = parse_addr(topics[2])
                fee = int(topics[3], 16)
                pool = parse_addr(data)  # data word 0 = pool address (low 40)
                if WETH not in (t0, t1):
                    continue
                venue = FACTORY_VENUE.get(factory, "v3_other")
                upsert_pool(pool, venue, t0, t1, fee)
                registered += 1
            except Exception:
                continue
        save_checkpoint("v3_venues_topic", tb)
        if (fb - from_block) % (chunk * 10) == 0:
            print(f"  [{tb:,}] registered={registered}", flush=True)

    print(f"[census-v3] DONE: {registered} WETH-paired V3 pools registered "
          f"across venues", flush=True)


if __name__ == "__main__":
    main()