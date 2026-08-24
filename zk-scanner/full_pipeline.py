#!/usr/bin/env python3
"""
Run full VERITAS pipeline: T0 (walker already done), T1, T2, financial explanation.
Outputs susceptible candidates with actionable financial explanation.
"""
import sqlite3
import time
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))

from t1 import run_t1_on_emitters
from t2 import run_t2_with_nullifier
from config import config
from value import compute_inventory

def main():
    print("[VERITAS] Starting full pipeline analysis...")
    t0_start = time.time()
    
    # Step 1: T1 Structural Analysis on all emitters with any activity
    print("[T1] Running structural analysis on all emitters with activity (deposits+withdrawals >= 1)...")
    t1_results = run_t1_on_emitters(min_deposits=0)  # deposits+withdrawals >= 1
    print(f"[T1] Analyzed {len(t1_results)} new emitters -> T1 candidates")
    
    # Step 2: T2 Probe Batteries on T1 candidates
    print("[T2] Running probe batteries on T1 candidates...")
    # Get T1 candidates from DB
    conn = sqlite3.connect("veritas.db")
    conn.row_factory = sqlite3.Row
    t1_candidates = conn.execute(
        "SELECT chain_id, address FROM targets WHERE status='t1_complete'"
    ).fetchall()
    conn.close()
    print(f"[T2] Found {len(t1_candidates)} T1 candidates to probe")
    
    suspects = []  # list of (address, vclass, verdict_details)
    all_t2_results = []
    for idx, row in enumerate(t1_candidates, 1):
        chain_id = row['chain_id']
        addr = row['address']
        # Find RPC URL for this chain from config
        rpc_url = None
        for cid, name, url, topics, start in config.chains:
            if cid == chain_id:
                rpc_url = url
                break
        if rpc_url is None:
            print(f"  [{idx}/{len(t1_candidates)}] Skipping {addr}: no RPC url for chain {chain_id}")
            continue
        print(f"  [{idx}/{len(t1_candidates)}] Testing {addr} (chain {chain_id})...")
        t2_start = time.time()
        try:
            results = run_t2_with_nullifier(chain_id, addr, rpc_url)
            t2_elapsed = time.time() - t2_start
            print(f"    Completed in {t2_elapsed:.1f}s")
            all_t2_results.extend(results)
            for res in results:
                probe = res.get('probe', '?')
                verdict = res.get('verdict', '?')
                print(f"      {probe} -> {verdict}")
                # Check for susceptibility
                if probe == 'self_vk_zero' and verdict in ('CONFIRMED_FORGERY', 'ZERO_RETURNED_SUSPECT'):
                    suspects.append((addr, 'caller_supplied_vk', verdict, res))
                if probe == 'nullifier_read' and verdict == 'UNGATED_SUSPECT':
                    suspects.append((addr, 'ungated_nullifier', verdict, res))
                if probe == 'malformed_points' and verdict == 'CANONICALITY_GAP_SUSPECT':
                    suspects.append((addr, 'canonicality_gap', verdict, res))
        except Exception as e:
            print(f"    ERROR: {e}")
        # Be nice to RPC
        time.sleep(0.2)
    
    print()
    print("=== SUMMARY OF SUSCEPTIBLE CANDIDATES ===")
    if suspects:
        for addr, vclass, verdict, _ in suspects:
            print(f"  {addr} -> {vclass} (verdict: {verdict})")
    else:
        print("  No susceptible candidates found. All probed targets returned healthy verdicts.")
    print()
    
    # Step 3: Financial Explanation for each target (susceptible or not)
    print("=== DETAILED FINANCIAL EXPLANATION PER TARGET ===")
    for row in t1_candidates:
        addr = row['address']
        chain_id = row['chain_id']
        # Get T1 details from DB
        conn = sqlite3.connect("veritas.db")
        conn.row_factory = sqlite3.Row
        trow = conn.execute(
            "SELECT * FROM targets WHERE address = ?", (addr,)
        ).fetchone()
        conn.close()
        if not trow:
            continue
        # Compute inventory (L0/L1)
        inv = compute_inventory(addr)
        l0_wei = inv['L0_wei']
        l1_wei = inv['L1_wei']
        denom = inv['denom']
        deposits = inv['deposits']
        withdrawals = inv['withdrawals']
        # Determine if this target is susceptible
        is_suspect = any(addr == s[0] for s in suspects)
        print(f"Address: {addr}")
        print(f"  Chain ID: {chain_id}")
        print(f"  Template: {trow['template_id']} (similarity: {trow['similarity']:.3f})")
        print(f"  Activity: deposits={deposits}, withdrawals={withdrawals}")
        print(f"  L0 (ETH balance): {l0_wei} wei ({l0_wei / 1e18:.5f} ETH)")
        print(f"  Denomination: {denom} wei")
        print(f"  L1 (pool size from net events * denom): {l1_wei} wei ({l1_wei / 1e18:.5f} ETH)")
        print(f"  Code size: {trow['code_size']} bytes")
        print(f"  Selectors: deposit={trow['deposit_sel']}, withdraw={trow['withdraw_sel']}, nullif={trow['nullif_sel']}, setver={trow['setver_sel']}")
        if is_suspect:
            # Find the vulnerability class(es) for this address
            vclasses = [vclass for (a, vclass, _, _) in suspects if a == addr]
            print(f"  *** SUSCEPTIBLE TO: {', '.join(vclasses)} ***")
            print("  Financial extraction methods:")
            for vclass in vclasses:
                if vclass == 'caller_supplied_vk':
                    print("    - Caller‑supplied VK: Forge arbitrary proofs → withdraw entire pool balance (L0 + L1).")
                elif vclass == 'ungated_nullifier':
                    print("    - Ungated nullifier: Replay withdrawal proofs → steal funds per captured nullifier (L0).")
                elif vclass == 'canonicality_gap':
                    print("    - Canonicality gap: Accept non‑canonical points → potential replay or value theft (exact impact depends on contract usage).")
            # Compute illustrative EV
            # Assume success probability P = 0.9, competition C = 0.5, gas = 5 gwei * 100000 = 5e9 wei (approx)
            P = 0.9
            C = 0.5
            gas_wei = 5_000_000_000  # 5 gwei * 100000 gas
            # Determine reachable value V based on class
            V_caller = l0_wei + l1_wei
            V_ungated = l0_wei
            V_canonical = l0_wei  # conservative
            for vclass in vclasses:
                if vclass == 'caller_supplied_vk':
                    V = V_caller
                    ceiling = 'L0 + L1 (entire pool)'
                elif vclass == 'ungated_nullifier':
                    V = V_ungated
                    ceiling = 'L0 (contract balance)'
                elif vclass == 'canonicality_gap':
                    V = V_canonical
                    ceiling = 'L0 (conservative)'
                ev_wei = int(P * V * (1 - C)) - gas_wei
                ev_eth = ev_wei / 1e18
                print(f"    Illustrative EV for {vclass}:")
                print(f"      P = {P}, C = {C}, gas = {gas_wei} wei")
                print(f"      V = {V} wei ({V/1e18:.5f} ETH) [{ceiling}]")
                print(f"      EV = {ev_wei} wei ({ev_eth:.5f} ETH)")
                if ev_wei > 0:
                    print("      --> POSITIVE EV (actionable if confirmed)")
                else:
                    print("      --> NEGATIVE OR ZERO EV (not actionable under these assumptions)")
        else:
            print("  No susceptibility detected (all probes returned healthy verdicts).")
        print()
    
    t0_elapsed = time.time() - t0_start
    print(f"[VERITAS] Full pipeline completed in {t0_elapsed:.1f} seconds.")
    print(f"[VERITAS] T1 candidates: {len(t1_candidates)}")
    print(f"[VERITAS] Susceptible candidates: {len(suspects)}")

if __name__ == "__main__":
    main()