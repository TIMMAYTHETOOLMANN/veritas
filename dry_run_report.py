#!/usr/bin/env python3
"""Simple standalone dry-run report for VERITAS flash-loan arb hunter.

Reports pool registry scan results from the database - a read-only diagnostic.
No transactions are broadcast, no signatures are made.
"""

import argparse
import json
import os
import sys
import time
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Constants from arb_engine (with 0x prefix)
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"

BROADCAST_RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]


def dry_run_report(rpc_url, min_profit=10.0, top_n=5):
    """Run a DB-only scan and print a summary report."""
    print(f"🔍 VERITAS DRY RUN — {datetime.now().isoformat()}")
    print(f"RPC: {rpc_url}")
    print("-" * 60)

    # Connect to DB and scan pools - NO RPC calls needed
    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")

    # Get total pool count
    total_pools = conn.execute('SELECT COUNT(*) FROM pools').fetchone()[0]

    # Get V3 WETH/USDC/USDCE pools (tokens stored with 0x prefix)
    v3_pools = conn.execute(
        "SELECT pair_addr, venue, token0, token1, usd_depth FROM pools "
        "WHERE kind='v3' AND (token0 LIKE '0x82af%' OR token1 LIKE '0x82af%') "
        "AND usd_depth IS NOT NULL").fetchall()

    # Get V2 WETH/USDC/USDCE pools
    v2_pools = conn.execute(
        "SELECT pair_addr, venue, token0, token1, usd_depth FROM pools "
        "WHERE kind='v2' AND (token0 LIKE '0x82af%' OR token1 LIKE '0x82af%') "
        "AND usd_depth IS NOT NULL").fetchall()

    conn.close()

    edges = []

    # Process V3 pools - report depth information
    for pool in v3_pools:
        pair_addr, venue, t0, t1, usd_depth = pool
        # Determine which is WETH and which is quote
        if t0 == WETH[2:]:  # Compare without 0x prefix
            quote = t1
        else:
            quote = t0

        # Only report USDC or USDCE pairs (compare without 0x prefix)
        if quote not in (USDC[2:], USDCE[2:]):
            continue

        # usd_depth is already in USD - report it
        edges.append({
            "pair": f"WETH/{quote[:6]}",
            "venue": venue,
            "usd_depth": usd_depth,
            "type": "V3",
        })

    # Process V2 pools - report depth information
    for pool in v2_pools:
        pair_addr, venue, t0, t1, usd_depth = pool
        # Determine which is WETH and which is quote
        if t0 == WETH[2:]:
            quote = t1
        else:
            quote = t0

        # Only report USDC or USDCE pairs
        if quote not in (USDC[2:], USDCE[2:]):
            continue

        edges.append({
            "pair": f"WETH/{quote[:6]}",
            "venue": venue,
            "usd_depth": usd_depth,
            "type": "V2",
        })

    # Filter and sort by usd_depth (deepest first)
    profitable = [e for e in edges if e.get('usd_depth', 0) > min_profit]
    profitable.sort(key=lambda x: x.get('usd_depth', 0), reverse=True)

    print(f"✅ Total pools scanned: {total_pools}")
    print(f"📊 V3 WETH/USDC/USDCE pools: {len(v3_pools)}")
    print(f"📊 V2 WETH/USDC/USDCE pools: {len(v2_pools)}")
    print(f"💰 Edges found (depth > ${min_profit}): {len(profitable)}")

    if profitable:
        print(f"\n🏆 Top {top_n} deepest opportunities:")
        table_data = []
        for i, edge in enumerate(profitable[:top_n], 1):
            route = edge.get("pair", "WETH/USD")
            depth = edge.get("usd_depth", 0)
            etype = edge.get("type", "V3")
            table_data.append([
                i,
                route,
                f"${depth:,.0f}",
                etype
            ])
        try:
            from tabulate import tabulate
            print(tabulate(table_data, headers=["#", "Route", "Depth (USD)", "Type"]))
        except ImportError:
            for row in table_data:
                print(f"  {row[0]}. Route: {row[1]}, Depth: ${row[2]:,.0f}, Type: {row[3]}")
    else:
        print("\n😴 No edges found matching criteria.")

    print("\n✅ Dry run complete — no transactions were sent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default="https://arb1.arbitrum.io/rpc",
                    help="RPC URL (not used for DB scan)")
    ap.add_argument("--min-profit", type=float, default=10.0,
                    help="Minimum usd depth threshold (default $10)")
    ap.add_argument("--top-n", type=int, default=5,
                    help="Number of top deepest pools to display")
    args = ap.parse_args()
    dry_run_report(args.rpc, min_profit=args.min_profit, top_n=args.top_n)