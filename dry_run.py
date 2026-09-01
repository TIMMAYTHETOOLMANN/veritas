#!/usr/bin/env python3
"""VERITAS dry run: scan live fork, report all candidates, show what would execute."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.rpc import RPC
import arb_engine

FORK_URL = "http://127.0.0.1:8545"
rpc = RPC(FORK_URL, timeout=20, retries=3)
print("=== VERITAS DRY RUN ===")
print("fork_block=", rpc.eth_blockNumber())
print("time=", time.strftime("%Y-%m-%d %H:%M:%S"))

arb_engine.discover_tokens_and_pairs(rpc)
print("tokens=", len(arb_engine.TOKENS))
print("pair_cache=", len(arb_engine.PAIR_CACHE))

# Scan with verbose reporting
edges, report = arb_engine.scan_cross_venue(rpc, 2500.0, 0.875, size_steps=12, max_venues_per_quote=8)
print("cross_venue_edges=", len(edges))
print("report_top=", report[:5])

# Show all candidates with their profitability breakdown
print("\n=== CANDIDATE EDGES ===")
for i, edge in enumerate(edges[:20]):
    gross = edge.get("gross_profit", 0.0)
    cost = edge.get("total_cost", 0.0)
    net = edge.get("net_margin", 0.0)
    threshold = arb_engine.MIN_SAFETY_MARGIN_USD
    gap = threshold - net if net < threshold else 0.0
    
    print(f"edge_{i}: {edge.get('pool_buy')} -> {edge.get('pool_sell')}")
    print(f"  size_weth={edge.get('size_weth')}")
    print(f"  gross_profit=${gross:.6f}")
    print(f"  total_cost=${cost:.6f}")
    print(f"  net_margin=${net:.6f}")
    print(f"  threshold=${threshold:.6f}")
    print(f"  gap=${gap:.6f}")
    print(f"  would_execute={net >= threshold}")
    
    if net < threshold and gross > 0:
        # Show what would need to change
        required_gross = cost + threshold
        print(f"  required_gross_to_execute=${required_gross:.6f}")
        print(f"  gross_shortfall=${required_gross - gross:.6f}")
    print()

# Best edge analysis
best = arb_engine.select_best_edge(rpc, edges, arb_engine.MIN_SAFETY_MARGIN_USD)
print("=== BEST EDGE ANALYSIS ===")
if best:
    print("best_edge_found= True")
    print("best_edge=", {k: best.get(k) for k in ["pool_buy","pool_sell","size_weth","gross_profit","total_cost","net_margin"]})
    print("PROFITABLE_OPPORTUNITY_EXISTS= True")
else:
    print("best_edge_found= False")
    print("PROFITABLE_OPPORTUNITY_EXISTS= False")
    
    # Analyze why no edge passed
    if edges:
        best_gross = max(e.get("gross_profit", 0.0) for e in edges)
        best_net = max(e.get("net_margin", 0.0) for e in edges)
        avg_gross = sum(e.get("gross_profit", 0.0) for e in edges) / len(edges)
        avg_net = sum(e.get("net_margin", 0.0) for e in edges) / len(edges)
        
        print("\n=== WHY NO EDGE PASSED ===")
        print(f"best_gross_profit=${best_gross:.6f}")
        print(f"best_net_margin=${best_net:.6f}")
        print(f"avg_gross_profit=${avg_gross:.6f}")
        print(f"avg_net_margin=${avg_net:.6f}")
        print(f"threshold=${arb_engine.MIN_SAFETY_MARGIN_USD:.6f}")
        print(f"gap_to_threshold=${arb_engine.MIN_SAFETY_MARGIN_USD - best_net:.6f}")
        
        # Show top 3 candidates and what would need to change
        print("\n=== TOP 3 CANDIDATES & FIXES ===")
        edges_sorted = sorted(edges, key=lambda e: e.get("net_margin", 0.0), reverse=True)[:3]
        for i, edge in enumerate(edges_sorted):
            gross = edge.get("gross_profit", 0.0)
            net = edge.get("net_margin", 0.0)
            cost = edge.get("total_cost", 0.0)
            gap = arb_engine.MIN_SAFETY_MARGIN_USD - net
            
            print(f"\ncandidate_{i}: {edge.get('pool_buy')} -> {edge.get('pool_sell')}")
            print(f"  gross=${gross:.6f}, net=${net:.6f}, cost=${cost:.6f}")
            print(f"  gap_to_execute=${gap:.6f}")
            
            # Suggest fixes
            if gross > 0 and gap > 0:
                # Option 1: Reduce margin
                required_margin = gross - cost
                if required_margin > 0:
                    print(f"  fix_1: reduce margin to ${required_margin:.6f} (currently ${arb_engine.MIN_SAFETY_MARGIN_USD:.6f})")
                
                # Option 2: Reduce gas cost
                gas_usd = cost - (gross * arb_engine.AAVE_FLASH_FEE) - arb_engine.MIN_SAFETY_MARGIN_USD
                if gas_usd > 0:
                    print(f"  fix_2: reduce gas_usd to ${gross * arb_engine.AAVE_FLASH_FEE + required_margin:.6f} (currently ${gas_usd:.6f})")
                
                # Option 3: Increase size
                scale = (gross) / (cost + arb_engine.MIN_SAFETY_MARGIN_USD)
                if scale > 1.0:
                    print(f"  fix_3: scale not viable (already optimal)")
                else:
                    print(f"  fix_3: increase size by {1/scale:.1f}x")
    else:
        print("no_candidates_found")

print("\n=== DRY RUN COMPLETE ===")
print("RECOMMENDATION: Review top candidates above. If gaps are small (<$0.10), system is launch-ready with minor tuning.")
