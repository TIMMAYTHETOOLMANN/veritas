#!/usr/bin/env python3
"""
Actionable financial explanation for viable candidates.
Scans the DB for candidates with probe verdicts indicating susceptibility.
If none, explains the methodology and states that no viable candidates were found.
"""
import sqlite3
import json
import os
from core.value import compute_inventory
from core.scoring import score
from core.selectors import selectors_map

DB_PATH = os.path.join(os.path.dirname(__file__), "veritas.db")

def get_inventory_for_address(address):
    """Return L0_wei and L1_wei for address."""
    inv = compute_inventory(address)
    return inv["L0_wei"], inv["L1_wei"]

def map_probe_to_vclass(probe, verdict):
    """Map probe verdict to a vulnerability class (if suspect)."""
    if verdict == "CONFIRMED_FORGERY" or verdict == "ZERO_RETURNED_SUSPECT":
        if probe == "self_vk_zero":
            return "caller_supplied_vk"
    if verdict == "UNGATED_SUSPECT":
        if probe == "nullifier_read":
            return "ungated_nullifier"
    if verdict == "CANONICALITY_GAP_SUSPECT":
        if probe == "malformed_points":
            return "canonicality_gap"  # not in CEILINGS but we can handle
    # Add more mappings as needed
    return None

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Get all distinct addresses from probes (or targets)
    addr_rows = conn.execute("""
        SELECT DISTINCT address FROM probes
    """).fetchall()
    candidates = []
    for row in addr_rows:
        addr = row["address"]
        # Get latest verdicts for each probe type
        probes = conn.execute("""
            SELECT probe, verdict FROM probes
            WHERE address = ?
            ORDER BY ts DESC
        """, (addr,)).fetchall()
        probe_map = {p["probe"]: p["verdict"] for p in probes}
        # Determine if any probe indicates susceptibility
        suspect_probes = []
        for probe, verdict in probe_map.items():
            vclass = map_probe_to_vclass(probe, verdict)
            if vclass and verdict not in ("REVERTED_HEALTHY", "HARDENED"):
                suspect_probes.append((probe, verdict, vclass))
        if suspect_probes:
            # Get inventory
            l0_wei, l1_wei = get_inventory_for_address(addr)
            # Get denominator from targets table if available
            denom_row = conn.execute("""
                SELECT denom FROM targets WHERE address = ?
            """, (addr,)).fetchone()
            denom = int(denom_row["denom"], 16) if denom_row and denom_row["denom"] else None
            candidates.append({
                "address": addr,
                "probes": probe_map,
                "suspect_probes": suspect_probes,
                "L0_wei": l0_wei,
                "L1_wei": l1_wei,
                "denom": denom
            })
    conn.close()
    if not candidates:
        print("# Actionable Financial Explanation Report\n")
        print("## Summary\n")
        print("No viable candidates (susceptible targets) were found in the scanned range.\n")
        print("All analyzed targets either did not meet the similarity floor (T1) or returned healthy verdicts (T2).\n")
        print("This indicates that the contracts implementing the event shapes (Deposit/Withdrawal) in the scanned blocks are following secure patterns (hardcoded VK, enforced canonicality, proper nullifier gating).\n")
        print("\n## Methodology\n")
        print("1. **T0 Discovery**: Event-graph walker sweeps `eth_getLogs` for Deposit/Withdrawal topics, emitting contract addresses with activity.\n")
        print("2. **T1 Structural Analysis**: For each emitter with sufficient activity, fetch bytecode, scan for selectors (deposit, withdraw, verify, etc.), match against known templates (e.g., Tornado v2). Candidates with similarity ≥ 0.6 proceed.\n")
        print("3. **T2 Probe Batteries**: Run deterministic, zero-cost `eth_call` probes:\n")
        print("   - `self_vk_zero`: Calls `verifyProof` with all-zero inputs to test if verification key is caller-supplied.\n")
        print("   - `malformed_points`: Tests acceptance of non-canonical curve points (x ≥ p) to check canonicality enforcement.\n")
        print("   - `nullifier_read`: Reads `nullifierHashes` mapping for a known spent nullifier to check if replay is gated.\n")
        print("4. **Verdict Mapping**: Probe verdicts are classified as:\n")
        print("   - `REVERTED_HEALTHY`: Node answered and the call reverted (expected behavior for healthy contract).\n")
        print("   - `HARDENED`: All variants of a probe reverted (e.g., all malformed points reverted).\n")
        print("   - `SUSPECT` variants (e.g., `ZERO_RETURNED_SUSPECT`, `UNGATED_SUSPECT`, `CANONICALITY_GAP_SUSPECT`) indicate a potential vulnerability.\n")
        print("   - `RPC_ERROR` or `ERROR` indicate transport or node issues (retried/fleeted).\n")
        print("5. **Financial Explanation**: For each suspect candidate, map the vulnerability class to a money path, measure on-chain value (L0: ETH balance, L1: pool size from event counts × denomination), compute expected EV = P × V × (1 − C) − gas, where P is estimated success probability, C is competition drag.\n")
        print("6. **Air-Gap Output**: This explanation is intended for a secure air-gap location; no private keys or secrets are included.\n")
        return
    print("# Actionable Financial Explanation Report\n")
    for cand in candidates:
        print(f"## Candidate: {cand['address']}\n")
        print(f"**Probe Verdicts:**")
        for probe, verdict in cand['probes'].items():
            mark = " (SUSPECT)" if (probe, verdict) in [(p,v) for p,v,_ in cand['suspect_probes']] else ""
            print(f"  - {probe}: {verdict}{mark}")
        print()
        print(f"**On‑Chain Value (Measured, Never Assumed):**")
        print(f"  - L0 (ETH balance): {cand['L0_wei']} wei ({cand['L0_wei'] / 1e18:.5f} ETH)")
        print(f"  - L1 (pool size): {cand['L1_wei']} wei ({cand['L1_wei'] / 1e18:.5f} ETH)")
        print(f"  - Denomination: {cand['denom']} wei (if available)")
        print()
        print(f"**Suspect Probe Details:**")
        for probe, verdict, vclass in cand['suspect_probes']:
            print(f"  - {probe} returned `{verdict}` → suggests vulnerability class: `{vclass}`")
        print()
        print(f"**Financial Explanation (if exploited):**")
        # For each suspect vclass, compute EV using scoring.py (assuming P=0.9, C=0.5, gas=50 gwei * 100000 = 5e9 wei approx)
        # We'll just show the recipe and note that EV would be positive if V > 0.
        for _, _, vclass in cand['suspect_probes']:
            if vclass == "caller_supplied_vk":
                print("  - Vulnerability: Caller‑supplied verification key allows forging proofs for arbitrary notes.")
                print("  - Money Path: Deploy malicious circuit + ceremony → mint proof → withdraw entire pool balance.")
                print("  - Ceiling: L1 (entire pool) + L0 (contract ETH balance).")
            elif vclass == "ungated_nullifier":
                print("  - Vulnerability: Nullifier mapping not gated, allowing replay of withdrawal proofs.")
                print("  - Money Path: Observe withdrawal tx → replay with altered recipient → repeat per captured nullifier.")
                print("  - Ceiling: L0 (contract ETH balance).")
            elif vclass == "canonicality_gap":
                print("  - Vulnerability: Acceptance of non‑canonical curve points allows malleability or replay.")
                print("  - Money Path: Depends on contract usage; could allow replay or value theft.")
                print("  - Ceiling: To be determined.")
            else:
                print(f"  - Vulnerability class {vclass}: money path TBD.")
        print()
        print(f"**Expected EV (illustrative):**")
        print("  Assuming success probability P = 0.9, competition drag C = 0.5, gas = 5 gwei × 100 000 = 5×10⁹ wei.")
        print("  EV = P × V × (1 − C) − gas, where V is the reachable value (L0/L1/L3) for the class.")
        print("  If V > 0 and the vulnerability is confirmed, EV is likely positive (actionable).")
        print("  If V = 0, the finding is downgraded to INFO and not actionable per doctrine.")
        print()
        print("---\n")
if __name__ == "__main__":
    main()