#!/usr/bin/env python3
"""
zk_fuzzer.py — Unified CLI entry point for the VERITAS ZK Differential Fuzzer.

Orchestrates the full T4/T5 pipeline:
  T4: differential fuzzing (extract VK -> generate adversarial witnesses ->
      assemble proofs -> probe on-chain verifier)
  T5: economic impact (static EV + optional fork divergence)

Usage:
  python zk_fuzzer.py --target 0xADDR                    # single target
  python zk_fuzzer.py --all                              # all SUSPECT targets
  python zk_fuzzer.py --target 0xADDR --divergence      # + state divergence engine
  python zk_fuzzer.py --target 0xADDR --corpus 128 --seed 0xABCD
  python zk_fuzzer.py --report --target 0xADDR           # audit report only
  python zk_fuzzer.py --list-targets                    # list known targets
"""

import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db
from core.rpc import RPC
from core.selectors import selectors_map
from zk import differential as L4
from zk import impact as L5
from zk import divergence as L5D
from zk import pocgen
from zk import witness as L2
from zk import config as ZKCFG
from zk import report as ZKRPT

# T4 witness-class -> T5 recipe-class mapping
HIT_CLASS_MAP = {
    "ZK-FIELD-OVERFLOW": "caller_supplied_vk",
    "ZK-UNDER-CONSTRAINED": "caller_supplied_vk",
    "ZK-NULLIFIER-COLLISION": "zk_nullifier_collision",
    "ZK-VERIFIER-CONFIG-MISMATCH": "zk_verifier_config_mismatch",
    "ZK-PROOF-MALLEABILITY": "zk_proof_malleability",
}


def _campaign_v(campaign, key):
    """Max measured V (wei) across the impact rows for a target."""
    best = 0
    for r in campaign.get("impact", []) or []:
        try:
            best = max(best, int(r.get(key, 0) or 0))
        except (TypeError, ValueError):
            pass
    return best


def _full_calldata_for(address, hit):
    """Rebuild full verifyProof calldata from the persisted probe row if the
    head we carried is truncated. Falls back to the (possibly truncated) head."""
    try:
        c = db.conn()
        row = c.execute(
            "SELECT call_data FROM probes WHERE address=? AND battery='t4_differential' "
            "AND probe=? ORDER BY id DESC LIMIT 1",
            (address.lower(), hit.get("label", ""))).fetchone()
        c.close()
        if row and (row["call_data"] or "").startswith("0x"):
            return row["call_data"]
    except Exception:
        pass
    return hit.get("calldata_head") or ""


def _latest_finding_id(address):
    try:
        c = db.conn()
        r = c.execute("SELECT id FROM findings WHERE address=? AND tier='T4' "
                      "ORDER BY id DESC LIMIT 1", (address.lower(),)).fetchone()
        c.close()
        return r["id"] if r else None
    except Exception:
        return None


def run_fuzz_target(address, corpus=64, seed=0x5EED, delay=0.35, rpc_url=None,
                    impact=True, divergence=False, persist=True, verbose=True):
    """Run the full T4/T5 pipeline on a single target address.

    Returns campaign dict with hits, impact rows, and optional divergence.
    """
    address = address.lower()
    rpc = RPC(rpc_url or "https://ethereum-rpc.publicnode.com", timeout=25, retries=3)

    # ---- T4: Differential fuzzing ----
    print(f"\n{'='*72}")
    print(f"[T4] Differential campaign: {address}")
    print(f"{'='*72}")

    campaign = L4.run_campaign(address, rpc_url=rpc_url, corpus_size=corpus,
                               seed=seed, delay=delay, persist=persist, verbose=verbose)
    if "error" in campaign:
        print(f"[T4] aborted: {campaign['error']}")
        return campaign

    L4.summarize(campaign)

    if not impact:
        return campaign

    # ---- T5: Economic impact ----
    # Doctrine gate: T5 actionability requires a CONFIRMED T4 hit
    confirmed_hits = [h for h in campaign["hits"] if h.get("confirmed", False)]
    confirmed_classes = {HIT_CLASS_MAP.get(h["class"], h["class"].lower())
                         for h in confirmed_hits}

    print(f"\n{'='*72}")
    print(f"[T5] Economic impact analysis: {address}")
    print(f"{'='*72}")
    print(f"  confirmed hits: {len(confirmed_hits)}")
    print(f"  confirmed classes: {confirmed_classes}")

    rows = L5.static_impact(address,
                           confirmed=bool(confirmed_hits),
                           confirmed_classes=confirmed_classes)
    if confirmed_hits:
        L5.persist_static(rows)
    L5.report(rows, address)
    campaign["impact"] = rows

    # ---- Generate PoC for first confirmed hit ----
    if confirmed_hits:
        first = confirmed_hits[0]
        warhead_calldata = first.get("calldata_head") or ""
        full_sel = _full_calldata_for(address, first)
        poc_path = pocgen.generate_poc(
            address,
            label=f"zk_{first['class']}_{address[2:10]}",
            calldata=full_sel or warhead_calldata,
            attacker=L5D.UNLOCKED_ATTACKER,
            taxonomy=L5._taxonomy(confirmed_classes and
                                  sorted(confirmed_classes)[0] or "zk_proof_malleability"),
            pre_tvl=str(int(_campaign_v(campaign, "V_wei"))),
            post_tvl="0",
            atk_delta=str(int(_campaign_v(campaign, "V_wei"))),
        )
        print(f"[T5] PoC written: {poc_path}")
        campaign["poc"] = poc_path

        # ---- Optional State Divergence Engine ----
        if divergence:
            print(f"\n[T5:div] Launching State Divergence Engine for {address}...")
            dv = L5D.run_divergence(
                address,
                {"to": address, "data": full_sel or warhead_calldata,
                 "label": pocgen._sanitize(first["class"])},
                attacker=L5D.UNLOCKED_ATTACKER,
            )
            L5D.report(dv)
            if dv.get("financially_exploitable"):
                fid = _latest_finding_id(address)
                if fid is not None:
                    L5D.persist_divergence(fid, dv,
                                           artifacts={"poc": poc_path})
            campaign["divergence"] = dv

    return campaign


def list_targets():
    """List all known targets from the database."""
    db.init()
    c = db.conn()
    rows = c.execute(
        "SELECT address, chain_id, template_id, similarity, status, code_size "
        "FROM targets ORDER BY address"
    ).fetchall()
    c.close()

    if not rows:
        print("No targets in database. Run walker.py first.")
        return

    print(f"\n{'ADDRESS':<44}{'CHAIN':<8}{'TEMPLATE':<16}{'SIM':<8}{'STATUS':<12}{'SIZE(B)':>8}")
    print("-" * 100)
    for r in rows:
        print(f"{r['address']:<44}{r['chain_id']:<8}{str(r['template_id']):<16}"
              f"{r['similarity']:<8}{str(r['status']):<12}{r['code_size']:>8}")
    print(f"\nTotal: {len(rows)} targets")


def main():
    ap = argparse.ArgumentParser(
        description="VERITAS ZK Differential Fuzzer — T4/T5 pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --target 0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936
  %(prog)s --all --corpus 128
  %(prog)s --target 0xADDR --divergence
  %(prog)s --report --target 0xADDR --json report.json
  %(prog)s --list-targets
        """
    )
    ap.add_argument("--target", help="single contract address to fuzz")
    ap.add_argument("--all", action="store_true",
                    help="run every target in veritas.db")
    ap.add_argument("--corpus", type=int, default=64,
                    help="corpus size per campaign (default: 64)")
    ap.add_argument("--seed", type=lambda x: int(x, 0), default=0x5EED,
                    help="random seed (default: 0x5EED)")
    ap.add_argument("--delay", type=float, default=0.35,
                    help="seconds between eth_calls (default: 0.35)")
    ap.add_argument("--rpc", default=None, help="RPC URL override")
    ap.add_argument("--no-impact", action="store_true",
                    help="skip T5 economic impact")
    ap.add_argument("--divergence", action="store_true",
                    help="L5b: run State Divergence Engine on confirmed hits")
    ap.add_argument("--persist", action="store_true", default=True,
                    help="persist results to veritas.db (default)")
    ap.add_argument("--no-persist", action="store_true",
                    help="skip DB persistence")
    ap.add_argument("--report", action="store_true",
                    help="generate audit report instead of running fuzz")
    ap.add_argument("--json", help="write report to JSON file")
    ap.add_argument("--list-targets", action="store_true",
                    help="list known targets and exit")
    args = ap.parse_args()

    db.init()

    # ---- List targets ----
    if args.list_targets:
        list_targets()
        return

    # ---- Report mode ----
    if args.report:
        if args.target:
            c = db.conn()
            report = ZKRPT.generate_report(c, args.target.strip().lower())
            c.close()
        elif args.all:
            c = db.conn()
            report = ZKRPT.generate_all_report(c)
            c.close()
        else:
            ap.error("--report requires --target or --all")

        if args.json:
            with open(args.json, "w") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"[report] written: {args.json}")
        else:
            print(json.dumps(report, indent=2, default=str))
        return

    # ---- Fuzz mode ----
    if args.target:
        targets = [args.target.strip().lower()]
    elif args.all:
        c = db.conn()
        rows = c.execute(
            "SELECT address FROM targets WHERE status != 'HARDENED' "
            "ORDER BY address"
        ).fetchall()
        c.close()
        targets = [r["address"] for r in rows]
        if not targets:
            print("[fuzz] No targets to fuzz. Run walker.py first.")
            return
    else:
        ap.error("need --target or --all")

    print(f"[fuzz] {len(targets)} target(s) | corpus={args.corpus} "
          f"seed={hex(args.seed)} delay={args.delay}s")

    results = []
    for i, addr in enumerate(targets, 1):
        print(f"\n{'#'*72}")
        print(f"# [{i}/{len(targets)}] {addr}")
        print(f"{'#'*72}")
        try:
            camp = run_fuzz_target(
                addr,
                corpus=args.corpus,
                seed=args.seed,
                delay=args.delay,
                rpc_url=args.rpc,
                impact=not args.no_impact,
                divergence=args.divergence,
                persist=not args.no_persist,
            )
            results.append(camp)
        except Exception as e:
            print(f"[fuzz] ERROR on {addr}: {e}")
            import traceback
            traceback.print_exc()
            results.append({"address": addr, "error": str(e)})
        # Be nice to RPC between targets
        if i < len(targets):
            time.sleep(1.0)

    # ---- Summary ----
    print(f"\n{'='*72}")
    print(f"[fuzz] COMPLETE — {len(results)} campaign(s) run")
    print(f"{'='*72}")
    for r in results:
        a = r.get("address", "?")
        if "error" in r:
            print(f"  {a}: ERROR — {r['error']}")
            continue
        hits = len(r.get("hits", []))
        confirmed = sum(1 for h in r.get("hits", []) if h.get("confirmed"))
        accepted = r.get("accepted", 0)
        print(f"  {a}: sent={r.get('sent',0)} accepted={accepted} "
              f"hits={hits} confirmed={confirmed} "
              f"poc={'yes' if r.get('poc') else 'no'}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)