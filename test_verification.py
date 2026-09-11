#!/usr/bin/env python
"""Verification script for VERITAS engine with MEV integration."""
import sys
sys.path.insert(0, ".")

from veritas_engine import VeritasEngine

config = {
    "chain_id": 1,
    "rpc_urls": ["https://1rpc.net", "https://cloudflare-eth.com", "https://rpc.tenderly.com"],
    "max_gas_usd": 100,
    "max_slippage_bps": 50,
    "max_capital_exposure_usd": 10,
    "min_profit_usd": 0.05,
}

print("=" * 60)
print("VERITAS Engine Verification")
print("=" * 60)

engine = VeritasEngine(config)
print("\n[1/4] Initializing engine...")

if not engine.initialize():
    print("❌ Engine initialization FAILED")
    sys.exit(1)

print("✅ Engine initialized successfully")

print("\n[2/4] Running single scan cycle...")
result = engine.scan_once()

print("✅ Scan cycle completed")

print("\n[3/4] Checking results...")
stats = result.statistics
print(f"  Cycle: {stats.get('cycle')}")
print(f"  Pools seen: {stats.get('pools_seen')}")
print(f"  Routes generated: {stats.get('routes_generated')}")
print(f"  Quotes attempted: {stats.get('quotes_attempted')}")
print(f"  Quotes successful: {stats.get('quotes_successful')}")
print(f"  Quotes failed: {stats.get('quotes_failed')}")
print(f"  Positive gross edges: {stats.get('positive_gross_edges')}")
print(f"  Positive net edges: {stats.get('positive_net_edges')}")
print(f"  Size tests: {stats.get('size_tests')}")
print(f"  Simulation attempts: {stats.get('simulation_attempts')}")
print(f"  Simulation passes: {stats.get('simulation_passes')}")
print(f"  Execution candidates: {stats.get('execution_candidates')}")
print(f"  RPC failures: {stats.get('rpc_failures')}")

# Check MEV-specific stats
mev_stats = stats.get('mev_arbitrages', 0), stats.get('mev_liquidations', 0), stats.get('mev_sandwiches', 0)
print(f"  MEV arbitrages: {mev_stats[0]}")
print(f"  MEV liquidations: {mev_stats[1]}")
print(f"  MEV sandwiches: {mev_stats[2]}")

# Check best edge if available
best_route = stats.get('best_route', '')
best_gross = stats.get('best_gross_edge', 0.0)
best_net = stats.get('best_net_edge', 0.0)
print(f"  Best route: {best_route}")
print(f"  Best gross edge: {best_gross:.6f} USD")
print(f"  Best net edge: {best_net:.6f} USD")

# Check MEV opportunities in metadata
mev_opp = result.scan_metadata.get('mev_opportunities', [])
print(f"  MEV opportunities in metadata: {len(mev_opp)}")
for opp in mev_opp[:3]:  # Show top 3
    print(f"    - {opp.get('type', 'unknown')}: block={opp.get('block', '?')}, profit={opp.get('profit_usd', 0):.6f} USD")

print("\n[4/4] Engine status...")
status = engine.status()
print(f"  Cycle: {status.get('cycle')}")
print(f"  Running: {status.get('running')}")
print(f"  Pools: {status.get('pools')}")
print(f"  MEV summary: {status.get('mev', 'N/A')}")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE")
print("=" * 60)