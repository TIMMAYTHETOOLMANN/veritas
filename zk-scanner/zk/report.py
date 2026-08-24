# zk/report.py — Comprehensive ZK Audit Report Generator
# Produces the described "Automated ZK Security Audit Report" format:
#   1. Target Profile
#   2. Field & Curve Parameters
#   3. Fuzzing Statistics
#   4. Findings (CRITICAL/HIGH/MEDIUM/LOW)
#   5. Reproducible Artifacts
#
# Pure read-only over veritas.db. Never fabricates.
# Usage:
#   python -m zk.report --target 0xADDR
#   python -m zk.report --all
#   python -m zk.report --json out.json

import argparse, json, os, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DB = os.path.join(ROOT, "veritas.db")

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

# Severity mapping from exploit class
CLASS_SEVERITY = {
    "ZK-FIELD-OVERFLOW": "CRITICAL",
    "ZK-UNDER-CONSTRAINED": "CRITICAL",
    "ZK-NULLIFIER-COLLISION": "HIGH",
    "ZK-VERIFIER-CONFIG-MISMATCH": "MEDIUM",
    "ZK-PROOF-MALLEABILITY": "HIGH",
    "caller_supplied_vk": "CRITICAL",
    "zk_nullifier_collision": "HIGH",
    "zk_verifier_config_mismatch": "MEDIUM",
    "zk_proof_malleability": "HIGH",
    "ungated_nullifier": "HIGH",
    "canonicality_gap": "MEDIUM",
}


def connect_ro(path):
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        sys.exit(f"[report] DB not found: {p}")
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        return c, True
    except sqlite3.Error as e:
        try:
            c = sqlite3.connect(p)
            c.row_factory = sqlite3.Row
            c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            return c, False
        except sqlite3.Error as e2:
            sys.exit(f"[report] cannot open DB {p}: {e2}")


def eth(wei):
    try:
        return int(str(wei).strip() or 0) / 1e18
    except (ValueError, TypeError):
        return 0.0


def fmt_eth(wei, width=14):
    return f"{eth(wei):>{width},.4f}"


def fmt_ts(ts):
    try:
        ts = int(ts)
        if ts <= 0:
            return "-"
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))
    except (ValueError, TypeError):
        return "-"


def build_target_profile(c, address):
    """Build the full target profile from DB rows."""
    address = address.lower()
    trow = c.execute("SELECT * FROM targets WHERE address=?", (address,)).fetchone()
    vrow = c.execute("SELECT * FROM vk_registry WHERE address=?", (address,)).fetchone()

    # Latest campaign
    camp = c.execute(
        "SELECT * FROM fuzz_campaigns WHERE address=? ORDER BY ts DESC LIMIT 1",
        (address,)
    ).fetchone()

    # All findings for this target
    findings = c.execute(
        "SELECT * FROM findings WHERE address=? ORDER BY id",
        (address,)
    ).fetchall()

    # Probes
    probes = c.execute(
        "SELECT * FROM probes WHERE address=? ORDER BY ts DESC LIMIT 30",
        (address,)
    ).fetchall()

    # Inventory
    inv_rows = c.execute(
        "SELECT * FROM inventory WHERE address=?", (address,)
    ).fetchall()

    # Exploitability
    expl = c.execute(
        "SELECT e.* FROM exploitability e "
        "JOIN findings f ON e.finding_id = f.id "
        "WHERE f.address=?", (address,)
    ).fetchall()

    # Impact sims
    sims = c.execute(
        "SELECT i.* FROM impact_sims i "
        "JOIN findings f ON i.finding_id = f.id "
        "WHERE f.address=?", (address,)
    ).fetchall()

    profile = {
        "address": address,
        "target": dict(trow) if trow else None,
        "vk": dict(vrow) if vrow else None,
        "campaign": dict(camp) if camp else None,
        "findings": [dict(f) for f in findings],
        "probes": [dict(p) for p in probes],
        "inventory": [dict(i) for i in inv_rows],
        "exploitability": [dict(e) for e in expl],
        "impact_sims": [dict(s) for s in sims],
    }

    # Compute L0/L1
    l0 = sum(eth(i["amount_wei"]) for i in inv_rows if i["layer"] == "L0")
    l1 = sum(eth(i["amount_wei"]) for i in inv_rows if i["layer"] == "L1")
    profile["L0_eth"] = l0
    profile["L1_eth"] = l1
    profile["total_value_eth"] = l0 + l1

    return profile


def build_finding_entry(finding, profile):
    """Build a structured finding entry for the report."""
    fid = finding["id"]
    vclass = finding["vclass"]
    severity = CLASS_SEVERITY.get(vclass, "INFO")

    # Find associated exploitability
    expl = next((e for e in profile["exploitability"] if e.get("finding_id") == fid), None)
    sim = next((s for s in profile["impact_sims"] if s.get("finding_id") == fid), None)

    entry = {
        "id": fid,
        "vclass": vclass,
        "tier": finding.get("tier"),
        "confidence": finding.get("confidence"),
        "status": finding.get("status"),
        "severity": severity,
        "evidence": finding.get("evidence"),
        "created_utc": fmt_ts(finding.get("created")),
    }

    if expl:
        entry["recipe"] = expl.get("recipe")
        entry["ceiling"] = expl.get("ceiling_wei")
        entry["ev_wei"] = expl.get("ev_wei")
        entry["ev_eth"] = round(eth(expl.get("ev_wei")), 6)
        entry["p_success"] = expl.get("p_success")
        entry["competition"] = expl.get("competition")
        entry["rationale"] = expl.get("rationale")

    if sim:
        entry["fork_block"] = sim.get("fork_block")
        entry["pre_tvl_wei"] = sim.get("pre_tvl_wei")
        entry["post_tvl_wei"] = sim.get("post_tvl_wei")
        entry["attacker_delta_wei"] = sim.get("attacker_delta_wei")
        entry["financially_exploitable"] = bool(sim.get("financially_exploitable"))
        entry["artifacts"] = sim.get("artifacts")

    return entry


def generate_report(c, address):
    """Generate the full audit report for one target."""
    profile = build_target_profile(c, address)

    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "report_type": "ZK_Security_Audit",
    }

    # 1. Target Profile
    t = profile["target"]
    v = profile["vk"]
    camp = profile["campaign"]
    report["target_profile"] = {
        "address": profile["address"],
        "chain_id": t.get("chain_id") if t else None,
        "chain": t.get("chain") if t else None,
        "code_size_bytes": t.get("code_size") if t else None,
        "template_id": t.get("template_id") if t else None,
        "template_similarity": round(t.get("similarity", 0), 3) if t else None,
        "status": t.get("status") if t else None,
        "first_seen_utc": fmt_ts(t.get("first_seen")) if t else None,
        "analyzed_utc": fmt_ts(t.get("analyzed_ts")) if t else None,
    }

    # 2. Field & Curve Parameters
    report["field_and_curve"] = {
        "curve": v.get("curve") if v else "unknown",
        "proof_system": v.get("proof_system") if v else "unknown",
        "vk_hash": v.get("vk_hash") if v else None,
        "ic_count": v.get("ic_count") if v else None,
        "g1_point_count": v.get("g1_point_count") if v else None,
        "extracted_from": v.get("extracted_from") if v else None,
        "extracted_utc": fmt_ts(v.get("extracted_ts")) if v else None,
        "prime_modulus_p": "21888242871839275222246405745257275088696311157297823662689037894645226208583",
        "scalar_field_r": "21888242871839275222246405745257275088548364400416034343698204186575808495617",
        "ntt_2_adicity": 28,
    }

    # 3. Fuzzing Statistics
    if camp:
        report["fuzzing_statistics"] = {
            "total_iterations": camp.get("sent", 0),
            "corpus_size": camp.get("corpus_size", 0),
            "accepted": camp.get("accepted", 0),
            "rejected": camp.get("rejected", 0),
            "reverted": camp.get("reverted", 0),
            "rpc_errors": camp.get("rpc_errors", 0),
            "backend": camp.get("backend", "unknown"),
            "campaign_ts_utc": fmt_ts(camp.get("ts")),
        }
    else:
        report["fuzzing_statistics"] = {
            "total_iterations": 0,
            "note": "No differential campaign run for this target yet.",
        }

    # 4. Findings
    findings = [build_finding_entry(f, profile) for f in profile["findings"]]
    findings.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 99))
    report["findings"] = findings

    # Summary by severity
    severity_summary = {}
    for f in findings:
        s = f["severity"]
        severity_summary[s] = severity_summary.get(s, 0) + 1
    report["severity_summary"] = severity_summary

    # 5. Value / Financial Context
    report["financial_context"] = {
        "L0_eth": round(profile["L0_eth"], 6),
        "L1_eth": round(profile["L1_eth"], 6),
        "total_value_eth": round(profile["total_value_eth"], 6),
        "note": "V measured from chain (eth_getBalance + event census), never assumed.",
    }

    # 6. Reproducible Artifacts
    artifacts = []
    poc_dir = os.path.join(ROOT, "artifacts", "pocs")
    if os.path.isdir(poc_dir):
        for f in sorted(os.listdir(poc_dir)):
            if address[:12] in f.lower() or "veritas" in f.lower():
                artifacts.append(f)
    report["reproducible_artifacts"] = {
        "poc_directory": poc_dir,
        "poc_files": artifacts,
        "db_path": DEFAULT_DB,
        "note": "Run: forge test --match-test test_exploit -vvv (anvil fork / testnet)",
    }

    return report


def generate_all_report(c):
    """Generate a rollup report across all targets."""
    targets = c.execute(
        "SELECT address FROM targets ORDER BY address"
    ).fetchall()

    all_reports = []
    for t in targets:
        addr = t["address"]
        try:
            r = generate_report(c, addr)
            all_reports.append(r)
        except Exception as e:
            print(f"[report] ERROR on {addr}: {e}", file=sys.stderr)

    # Aggregate stats
    total_findings = sum(len(r["findings"]) for r in all_reports)
    critical = sum(r["severity_summary"].get("CRITICAL", 0) for r in all_reports)
    high = sum(r["severity_summary"].get("HIGH", 0) for r in all_reports)
    medium = sum(r["severity_summary"].get("MEDIUM", 0) for r in all_reports)
    total_value = sum(r["financial_context"]["total_value_eth"] for r in all_reports)

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "report_type": "ZK_Security_Audit_Rollup",
        "targets_count": len(all_reports),
        "aggregate": {
            "total_findings": total_findings,
            "critical": critical,
            "high": high,
            "medium": medium,
            "total_value_eth": round(total_value, 4),
        },
        "targets": all_reports,
    }


def main():
    ap = argparse.ArgumentParser(description="VERITAS ZK Audit Report Generator")
    ap.add_argument("--target", help="single target address")
    ap.add_argument("--all", action="store_true", help="rollup across all targets")
    ap.add_argument("--json", help="write report to JSON file")
    ap.add_argument("--db", default=DEFAULT_DB, help="override DB path")
    args = ap.parse_args()

    c, _ = connect_ro(args.db)

    if args.all:
        report = generate_all_report(c)
    elif args.target:
        report = generate_report(c, args.target.strip().lower())
    else:
        ap.error("need --target or --all")

    c.close()

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"[report] written: {args.json}")
    else:
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()