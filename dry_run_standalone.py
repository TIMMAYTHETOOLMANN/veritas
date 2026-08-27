#!/usr/bin/env python3
"""Standalone dry-run script for VERITAS flash-loan arb hunter.

Runs a full scan and prints a summary report without broadcasting any transactions.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arb_engine import scan_once


BROADCAST_RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]


def dry_run_report(rpc_url, min_profit=0.01, top_n=5):
    """Run a full scan and print a summary report without broadcasting any transactions."""
    print(f"🔍 VERITAS DRY RUN — {datetime.now().isoformat()}")
    print(f"RPC: {rpc_url}")
    print("-" * 60)

    # Use scan_once which returns a result dict
    result = scan_once(rpc_url)

    if "error" in result:
        print(f"❌ Scan error: {result['error']}")
        print("\n✅ Dry run complete — no transactions were sent.")
        return

    edges = result.get("edges", [])
    report = result.get("detail", [])

    # Filter by profit (using net_usd from the report)
    profitable = [e for e in edges if e.get('net_usd', 0) > min_profit]
    profitable.sort(key=lambda x: x.get('net_usd', 0), reverse=True)

    print(f"✅ Total edges scanned: {len(edges)}")
    print(f"💰 Profitable edges (>{min_profit} USD): {len(profitable)}")

    if profitable:
        print(f"\n🏆 Top {top_n} opportunities:")
        table_data = []
        for i, edge in enumerate(profitable[:top_n], 1):
            route = edge.get("pair", "WETH/USD")
            profit = edge.get("net_usd", 0)
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
                    help="RPC URL")
    ap.add_argument("--min-profit", type=float, default=0.01,
                    help="Minimum profit threshold in USD")
    ap.add_argument("--top-n", type=int, default=5,
                    help="Number of top edges to display")
    args = ap.parse_args()
    dry_run_report(args.rpc, min_profit=args.min_profit, top_n=args.top_n)
