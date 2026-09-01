#!/usr/bin/env python3
"""Verbose dry run: trace scanner step-by-step to find where edges disappear."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.rpc import RPC
import arb_engine

FORK_URL = "http://127.0.0.1:8545"
rpc = RPC(FORK_URL, timeout=20, retries=3)
print("=== VERITAS VERBOSE DRY RUN ===")
print("fork_block=", rpc.eth_blockNumber())

arb_engine.discover_tokens_and_pairs(rpc)
print("tokens=", list(arb_engine.TOKENS.keys()))
print("pair_cache=", arb_engine.PAIR_CACHE)

# Manual scan with verbose output
tokens = list(arb_engine.TOKENS.keys())
if arb_engine.WETH in tokens:
    tokens.remove(arb_engine.WETH)
tokens = tokens[:8]

factories = [arb_engine.SUSHI_FACTORY, arb_engine.UNIV2_FACTORY]
min_size = 10 ** 18
sizes = [min_size]

total_quotes = 0
valid_quotes = 0
cross_venue_pairs = 0
edges_built = 0

for token_a in tokens:
    print(f"\nscanning {token_a}...")
    quotes = []
    for factory in factories:
        for size in sizes:
            fb = arb_engine._quote_edge(rpc, arb_engine.WETH, token_a, size, factory)
            rb = arb_engine._quote_edge(rpc, token_a, arb_engine.WETH, size, factory)
            
            if fb:
                total_quotes += 1
                valid_quotes += 1
                quotes.append(("forward", factory, fb))
            if rb:
                total_quotes += 1
                valid_quotes += 1
                quotes.append(("reverse", factory, rb))
    
    print(f"  quotes_found={valid_quotes}")
    
    # Build cross-venue edges
    forward = [q for q in quotes if q[0] == "forward"]
    reverse = [q for q in quotes if q[0] == "reverse"]
    
    print(f"  forward={len(forward)}, reverse={len(reverse)}")
    
    if len(forward) >= 2 and len(reverse) >= 2:
        for direction, factory_f, fb in forward:
            for direction_r, factory_r, rb in reverse:
                if factory_f == factory_r:
                    continue
                if fb.get("pair_addr") == rb.get("pair_addr"):
                    continue
                cross_venue_pairs += 1
                edge = arb_engine.build_edge(fb, rb, token_a, fb.get("reserves_a", 0))
                edges_built += 1
                print(f"  edge: {factory_f[:6]}... -> {factory_r[:6]}... net=${edge.get('net_margin', 0):.6f}")

print(f"\n=== SCAN SUMMARY ===")
print(f"total_quotes_attempted={total_quotes}")
print(f"valid_quotes={valid_quotes}")
print(f"cross_venue_pairs={cross_venue_pairs}")
print(f"edges_built={edges_built}")

# Now run actual scan_cross_venue
print("\n=== ACTUAL SCAN_CROSS_VENUE ===")
edges, report = arb_engine.scan_cross_venue(rpc, 2500.0, 0.875, size_steps=12, max_venues_per_quote=8)
print(f"edges_returned={len(edges)}")
print(f"report_returned={len(report)}")
