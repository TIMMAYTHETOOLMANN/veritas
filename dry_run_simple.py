#!/usr/bin/env python3
"""Simple standalone dry-run script for VERITAS flash-loan arb hunter.

Runs a quick scan of the pool registry and reports edges found.
No transaction broadcasting.
"""

import argparse
import json
import os
import sys
import time
import sqlite3
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Minimal constants from arb_engine
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
UNIV2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"

BROADCAST_RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]


def get_rpc(rpc_url):
    """Simple RPC caller."""
    import urllib.request
    import json
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice"}
        r = urllib.request.Request(
            rpc_url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(r, timeout=30).read()
        result = json.loads(resp)
        return int(result["result"], 16)
    except Exception as e:
        print(f"RPC error: {e}")
        return 0


def token_decimals_cached(addr):
    """Simple decimals cache."""
    cache = {}
    if addr in cache:
        return cache[addr]
    # Default to 18
    cache[addr] = 18
    return 18


def dry_run_report(rpc_url, min_profit=0.01, top_n=5):
    """Run a quick scan and print a summary report without broadcasting any transactions."""
    print(f"🔍 VERITAS DRY RUN — {datetime.now().isoformat()}")
    print(f"RPC: {rpc_url}")
    print("-" * 60)

    # Get gas price
    gas_price = get_rpc(rpc_url)
    eth_usd = 2450.0
    gas_usd = (gas_price / 1e9) * (1e9 / 1e9) * eth_usd  # simplified

    # Connect to DB and scan pools
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
                            "r0": int(r0), "r1": int(r1), "depth": depth or 0})
        elif kind == "v2" and (t0l == WETH[2:] or t1l == WETH[2:]):
            v2_pools.append({"addr": addr, "venue": venue, "kind": kind,
                            "t0": t0l, "t1": t1l, "fee": fee,
                            "r0": int(r0), "r1": int(r1), "depth": depth or 0})

    conn.close()

    # Simple edge detection: find V3 pools with WETH and USDC/USDCE
    edges = []
    for pool in v3_pools:
        if pool["t0"][2:] == WETH[2:] or pool["t1"][2:] == WETH[2:]:
            # Find quote token
            if pool["t0"][2:] == WETH[2:]:
                quote = pool["t1"]
            else:
                quote = pool["t0"]

            # Only count USDC or USDCE
            if quote not in (USDC, USDCE):
                continue

            # Calculate simple profit estimate
            r0_val = pool["r0"]
            r1_val = pool["r1"]
            dec_usdc = token_decimals_cached(quote)
            dec_weth = token_decimals_cached(WETH)

            # Simple: 1 WETH -> quote amount
            usdc_out = int(r1_val / 10 ** dec_usdc) if pool["t1"][2:] == WETH[2:] else int(r0_val / 10 ** dec_usdc)
            weth_in = int(r0_val / 10 ** dec_weth) if pool["t0"][2:] == WETH[2:] else int(r1_val / 10 ** dec_weth)

            if usdc_out > weth_in:
                profit_weth = usdc_out / 10 ** dec_usdc - weth_in / 10 ** dec_weth
                profit_usd = profit_weth * eth_usd

                if profit_usd > min_profit:
                    edges.append({
                        "pair": f"WETH/{quote[:6]}",
                        "venue": pool["venue"],
                        "profit_usd": round(profit_usd, 4),
                        "gas_usd": round(gas_usd, 4),
                        "size_weth": round(weth_in / 1e18, 6),
                        "gross_usd": round(profit_usd + gas_usd, 4),
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
                    help="RPC URL")
    ap.add_argument("--min-profit", type=float, default=0.01,
                    help="Minimum profit threshold in USD")
    ap.add_argument("--top-n", type=int, default=5,
                    help="Number of top edges to display")
    args = ap.parse_args()
    dry_run_report(args.rpc, min_profit=args.min_profit, top_n=args.top_n)
