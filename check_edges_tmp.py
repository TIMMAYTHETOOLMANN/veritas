#!/usr/bin/env python3
"""Quick check: run scan_once and report if any arbitrage edges found.

Uses the consolidated VERITAS stack (core/rpc.py, arb_engine.py with ZK-proof path).
"""

import sys
sys.path.insert(0, '.')

from core.rpc import RPC
import arb_engine

rpc = RPC('https://arb1.arbitrum.io/rpc', timeout=30, retries=3)
result = arb_engine.scan_once(rpc)

edges = result.get('edges', [])
detail = result.get('detail', [])

print(f"=== VERITAS Autopilot Edge Scan ===")
print(f"Timestamp: {result.get('ts')}")
print(f"ETH/USD: {result.get('eth_usd')}")
print(f"Gas USD: {result.get('gas_usd')}")
print(f"Pools scanned: {len(result.get('pools', []))}")
print(f"Edges found: {len(edges)}")
print(f"Detail items: {len(detail)}")
print()

if edges:
    print("EDGES DETECTED — Autopilot will execute on next cycle:")
    for e in edges:
        pair = e.get('pair', 'N/A')
        net = e.get('net_usd', 'N/A')
        gross = e.get('gross_usd', 'N/A')
        loan = e.get('loan_fee_usd', 'N/A')
        gas = e.get('gas_usd', 'N/A')
        print(f"  {pair}: net_out=${net} gross=${gross} loan=${loan} gas=${gas} edge={e.get('edge')}")
    print()
    print("Execution path: ZK-proof gate (ShadowPath) → executeWithProof → sweep")
    print("Fallback: anvil fork-sim if ZK unavailable")
else:
    print("No edges — hunter loop will re-scan in 180s. Normal ranging market condition.")
    print("When edges appear, flash_hunter.py auto-executes: ZK-proof gate → broadcast → sweep")
    print("Fallback: anvil fork-sim if ZK unavailable")