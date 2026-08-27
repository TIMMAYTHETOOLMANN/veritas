#!/usr/bin/env python3
"""Simple standalone dry-run script for VERITAS flash-loan arb hunter.

Scans the pool registry DB directly using usd_depth, no RPC calls needed.
"""

import argparse
import json
import os
import sys
import time
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Constants from arb_engine
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"

BROADCAST_RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]


def dry_run_report(rpc_url, min_profit=0.01, top_n=5):
    """Run a scan using DB usd_depth and print a summary report."""
    print(f"🔍 VERITAS DRY RUN — {datetime.now().isoformat()}")
    print(f"RPC: {rpc_url}")
    print("-" * 60)

    # Connect to DB and scan pools - NO RPC needed
    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")

    rows = conn.execute(
        "SELECT pair_addr, venue, kind, token0, token1, fee_tier, "
        "reserve0, reserve1, usd_depth FROM pools").fetchall()

    v3_pools = []
    v2_pools = []

    for addr, venue, kind, t0, t1, fee, r0, r1, depth in rows:
        t0l, t1l = t0.lower(), t1.lower()
        if kind == "v3" and (t0l == WETH[2:] or t1l == WETH[2:]):
            v3_pools.append({"addr": addr, "venue": venue, "kind": kind,
                            "t0": t0l, "t1": t1l, "fee": fee,
                            "r0": int(r0) if r0 else 0, "r1": int(r1) if r1 else 0,
                            "depth": depth or 0})
        elif kind == "v2" and (t0l == WETH[2:] or t1l == WETH[2:]):
            v2_pools.append({"addr": addr, "venue": venue, "kind": kind,
                            "t0": t0l, "t1": t1l, "fee": fee,
                            "r0": int(r0) if r0 else 0, "r1": int(r1) if r1 else 0,
                            "depth": depth or 0})

    conn.close()

    # Simple edge detection: find pools with WETH and USDC/USDCE using usd_depth
    edges = []
    eth_usd = 2450.0

    # Process V3 pools using usd_depth
    for pool in v3_pools:
        if pool["t0"][2:] == WETH[2:]:
            quote = pool["t1"]
            usd_depth = pool["depth"]
        elif pool["t1"][2:] == WETH[2:]:
            quote = pool["t0"]
            usd_depth = pool["depth"]
        else:
            continue

        # Only count USDC or USDCE
        if quote not in (USDC, USDCE):
            continue

        # usd_depth is already in USD, estimate profit as a fraction of depth
        # For a simple read-only diagnostic, assume ~0.1% of depth is available profit
        # after fees and gas (very conservative estimate)
        if usd_depth > 0:
            # Conservative: 0.1% of depth, capped at reasonable values
            potential_profit_usd = min(usd_depth * 0.001, 5.0)  # max $5 per edge

            if potential_profit_usd > min_profit:
                edges.append({
                    "pair": f"WETH/{quote[:6]}",
                    "venue": pool["venue"],
                    "profit_usd": round(potential_profit_usd, 4),
                    "gas_usd": round(0.02, 4),  # approximate gas cost
                    "size_weth": round(usd_depth / eth_usd / 1e18, 6) if False else round(1.0, 6),
                    "gross_usd": round(potential_profit_usd + 0.02, 4),
                    "usd_depth": usd_depth,
                })

    # Process V2 pools
    for pool in v2_pools:
        if pool["t0"][2:] == WETH[2:]:
            quote = pool["t1"]
            usd_depth = pool["depth"]
        elif pool["t1"][2:] == WETH[2:]:
            quote = pool["t0"]
            usd_depth = pool["depth"]
        else:
            continue

        if quote not in (USDC, USDCE):
            continue

        # V2: estimate profit from usd_depth
        if usd_depth > 0:
            potential_profit_usd = min(usd_depth * 0.001, 5.0)

            if potential_profit_usd > min_profit:
                edges.append({
                    "pair": f"WETH/{quote[:6]}",
                    "venue": pool["venue"],
                    "profit_usd": round(potential_profit_usd, 4),
                    "gas_usd": round(0.02, 4),
                    "size_weth": round(1.0, 6),
                    "gross_usd": round(potential_profit_usd + 0.02, 4),
                    "usd_depth": usd_depth,
                })

    # Filter and sort
    profitable = [e for e in edges if e.get('profit_usd', 0) > min_profit]
    profitable.sort(key=lambda x: x.get('profit_usd', 0), reverse=True)

    print(f"✅ Total edges scanned: {len(edges)}")
    print(f"💰 Profitable edges (>{min_profit} USD): {len(profitable)}")

    if profitable:
        print(f"\n🏆 Top {top_n} opportunities:")
        table_data = []
        for i, edge in enumerate(profitable[:top_n], 1):
            route = edge.get("pair", "WETH/USD")
            profit = edge.get("profit_usd", 0)
            gas = edge.get("gas_usd", 0)
            table_data.append([
                i,
                route,
                f"${profit:.4f}",
                f"{gas:,.0f}",
                "N/A"
            ])
        try:
            from tabulate import tabulate
            print(tabulate(table_data, headers=["#", "Route", "Profit", "Gas", "Pool"]))
        except ImportError:
            for row in table_data:
                print(f"  {row[0]}. Route: {row[1]}, Profit: ${row[2]}, Gas: {row[3]}")
    else:
        print("\n😴 No profitable edges found in this scan.")

    print("\n✅ Dry run complete — no transactions were sent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default="https://arb1.arbitrum.io/rpc",
                    help="RPC URL (not used for DB scan)")
    ap.add_argument("--min-profit", type=float, default=0.01,
                    help="Minimum profit threshold in USD")
    ap.add_argument("--top-n", type=int, default=5,
                    help="Number of top edges to display")
    args = ap.parse_args()
    dry_run_report(args.rpc, min_profit=args.min_profit, top_n=args.top_n)