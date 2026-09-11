#!/usr/bin/env python3
"""Merge funnel batch JSONL checkpoints into one audit report (offline, fast)."""
import json
import os
from collections import Counter

FILES = [f"C:/tmp/funnel_c{i}.jsonl" for i in range(10)]


def main():
    all_econ = []
    for f in FILES:
        if not os.path.exists(f):
            print(f"  MISSING checkpoint: {f}")
            continue
        n = 0
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    all_econ.append(json.loads(line))
                    n += 1
        print(f"  Loaded {n} route records from {f}")

    # Deduplicate by route_idx (reruns may overlap)
    seen = {}
    for e in all_econ:
        seen[e["route_idx"]] = e
    econ = sorted(seen.values(), key=lambda e: e["route_idx"])

    gross = sum(1 for e in econ if e["is_gross_positive"])
    net = sum(1 for e in econ if e["is_net_positive"])
    full_path = sum(1 for e in econ if e.get("hops"))
    malformed = sum(1 for e in econ if (e.get("rejection_reason") or "").startswith("route_malformed"))
    survivors = [e for e in econ if e["is_net_positive"]]

    print("\n" + "=" * 70)
    print("MERGED FUNNEL SUMMARY")
    print("=" * 70)
    print(f"  Routes with economics:       {len(econ)}")
    print(f"  Gross-positive routes:       {gross}")
    print(f"  Net-positive routes:         {net}")
    print(f"  Gross->net conversion:       {(net / gross * 100) if gross else 0:.1f}%")
    print(f"  Full-path valued routes:     {full_path}")
    print(f"  Malformed routes:            {malformed}")

    if survivors:
        best = max(survivors, key=lambda e: e["net_profit_usd"])
        print("\n" + "=" * 70)
        print("BEST ROUTE")
        print("=" * 70)
        print(f"  Route index:      {best['route_idx']}")
        print(f"  Token path:       {' -> '.join(best['token_path'])}")
        print(f"  Venue path:       {' -> '.join(best['venue_path'])}")
        for addr in best.get("pool_addresses", [best.get("pool_address")] if best.get("pool_address") else []):
            if addr:
                print(f"  Pool address:     {addr}")
        print(f"  Fee tier:         {best.get('fee_tier')}")
        for h in best.get("hops", []) or []:
            print(f"  Hop {h['hop']}: {h['venue']} {h['pool_address']} "
                  f"{h['token_in'][:10]}->{h['token_out'][:10]} "
                  f"{h['amount_in']}->{h['amount_out']} (src={h.get('source', '?')} auth={h.get('authoritative', False)})")
        print(f"  Amount in:        {best['amount_in']} (${best['amount_in'] / 1e18 * 2500.0:.2f})")
        print(f"  Amount out:       {best['amount_out']} (${best['amount_out'] / 1e18 * 2500.0:.2f})")
        print(f"  Gross profit:     ${best['gross_profit_usd']:.4f}")
        print(f"  DEX fees:         ${best['dex_fees_usd']:.4f}")
        print(f"  Flash loan fee:   ${best['flash_fee_usd']:.4f}")
        print(f"  Gas cost:         ${best['gas_cost_usd']:.4f}")
        print(f"  Slippage cost:    ${best['slippage_cost_usd']:.4f}")
        print(f"  Total costs:      ${best['total_costs_usd']:.4f}")
        print(f"  Net profit:       ${best['net_profit_usd']:.4f}")
        print(f"  ROI:              {best['roi_bps']:.2f} bps")
        print(f"  Execution gate:   {best['execution_gate']}"
              f"{(' (' + best['rejection_reason'] + ')') if best['rejection_reason'] else ''}")

        rois = sorted(e["roi_bps"] for e in survivors)
        n = len(rois)
        median = rois[n // 2] if n % 2 == 1 else (rois[n // 2 - 1] + rois[n // 2]) / 2
        gate_pass = sum(1 for e in survivors if e["execution_gate"] == "PASS")
        print("\n" + "=" * 70)
        print("ROI DISTRIBUTION (net-positive routes)")
        print("=" * 70)
        print(f"  Count:            {n}")
        print(f"  Min ROI:          {rois[0]:.2f} bps")
        print(f"  Median ROI:       {median:.2f} bps")
        print(f"  Max ROI:          {rois[-1]:.2f} bps")
        print(f"  Above 5 bps:      {sum(1 for r in rois if r >= 5)} routes (execution threshold)")
        print(f"  Above 50 bps:     {sum(1 for r in rois if r >= 50)} routes")
        print(f"  Above 100 bps:    {sum(1 for r in rois if r >= 100)} routes")
        print(f"  Gate PASS:        {gate_pass} routes")
    else:
        print("\nBEST ROUTE: none (no net-positive routes)")

    reasons = Counter(
        e["rejection_reason"].split(":")[0]
        for e in econ
        if e["rejection_reason"]
    )
    print("\n" + "=" * 70)
    print("REJECTION REASON HISTOGRAM")
    print("=" * 70)
    if reasons:
        for reason, count in reasons.most_common():
            print(f"  {reason}: {count}")
    else:
        print("  (no rejections)")


if __name__ == "__main__":
    main()
