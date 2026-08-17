# report.py — VERITAS executive findings rollup (pure read-only over veritas.db)
#
# Usage:
#   python report.py                     console executive report
#   python report.py --json out.json     same data as JSON file (report-into-chat workflow)
#   python report.py --target 0xADDR     single-target deep report
#   python report.py --db path.db        override DB location (default: veritas.db beside this file)
#
# Read-only: opens the DB with SQLite URI mode=ro. Never writes, never fabricates:
# empty tables render explicit placeholders.
import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "veritas.db")

STATUSES = ("SUSPECT", "HARDENED", "INFO")
EVIDENCE_LIMIT = 80  # chars


# ---- read-only connection ------------------------------------------------

def connect_ro(path):
    """Open SQLite strictly read-only. Returns (conn, readonly_guaranteed)."""
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        sys.exit(f"[report] DB not found: {p}")
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()  # force open
        return c, True
    except sqlite3.Error as e:
        # WAL corner case (missing -shm, locked): fall back to a plain handle.
        # We still issue zero writes; flag it honestly.
        print(f"[report] WARN read-only URI open failed ({e}); using plain handle (no writes issued)",
              file=sys.stderr)
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        return c, False


# ---- formatting helpers ----------------------------------------------------

def trunc(s, limit=EVIDENCE_LIMIT):
    """Truncate huge strings (evidence, results) to `limit` chars."""
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= limit else s[:limit] + f"...(+{len(s) - limit} chars)"


def eth(wei):
    """wei (str/int/float/None) -> ETH float. Malformed -> 0.0, flagged by caller data."""
    try:
        return int(str(wei).strip() or 0) / 1e18
    except (ValueError, TypeError):
        return 0.0


def wei_int(wei):
    try:
        return int(str(wei).strip() or 0)
    except (ValueError, TypeError):
        return 0


def fmt_eth(wei, width=14):
    return f"{eth(wei):>{width},.4f}"


def fmt_ts(ts):
    """unix int -> 'YYYY-MM-DD HH:MM UTC'; NULL/0/None -> '-'."""
    try:
        ts = int(ts)
        if ts <= 0:
            return "-"
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))
    except (ValueError, TypeError):
        return "-"


def tpl(t):
    """NULL/empty template_id -> 'unknown'."""
    return t if t else "unknown"


def or_dash(v):
    return v if v not in (None, "") else "-"


# ---- data queries ----------------------------------------------------------

def counts(c):
    targets = c.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
    by_status, extra = {}, {}
    for r in c.execute("SELECT status, COUNT(*) n FROM findings GROUP BY status"):
        if r["status"] in STATUSES:
            by_status[r["status"]] = r["n"]
        else:
            extra[or_dash(r["status"])] = r["n"]
    for s in STATUSES:
        by_status.setdefault(s, 0)
    # NOTE: amount_wei is TEXT because wei values exceed SQLite's 64-bit INTEGER
    # (>2^63 wei = ~9.2 ETH). Sum in Python (arbitrary precision), never in SQL.
    agg = 0
    for r in c.execute(
        "SELECT amount_wei FROM inventory WHERE layer='L0' AND asset='ETH'"
    ):
        agg += wei_int(r["amount_wei"])
    return targets, by_status, extra, agg


def l0_map(c):
    """address -> summed L0 ETH wei (asset=ETH). Python-side sum (wei > 2^63)."""
    m = {}
    for r in c.execute(
        "SELECT address, amount_wei FROM inventory WHERE layer='L0' AND asset='ETH'"
    ):
        m[r["address"]] = m.get(r["address"], 0) + wei_int(r["amount_wei"])
    return m


def last_verdicts(c):
    """address -> most recent probe verdict (highest id)."""
    m = {}
    for r in c.execute(
        "SELECT address, verdict FROM probes p WHERE id = "
        "(SELECT MAX(id) FROM probes p2 WHERE p2.address = p.address)"
    ):
        m[r["address"]] = r["verdict"]
    return m


def findings_rows(c, status):
    """Findings of one status joined with targets/exploitability/L0/verdict."""
    l0, verd = l0_map(c), last_verdicts(c)
    rows = []
    q = (
        "SELECT f.id, f.address, f.vclass, f.tier, f.confidence, f.status, "
        "f.evidence, f.created, t.template_id, t.similarity, t.chain, "
        "e.recipe, e.ev_wei, e.ceiling_wei, e.p_success, e.competition, e.rationale "
        "FROM findings f "
        "LEFT JOIN targets t ON t.address = f.address "
        "LEFT JOIN exploitability e ON e.finding_id = f.id "
        "WHERE f.status = ?"
    )
    for r in c.execute(q, (status,)):
        d = dict(r)
        d["template_id"] = tpl(d.get("template_id"))
        d["l0_wei"] = l0.get(d["address"])
        d["l0_eth"] = round(eth(d["l0_wei"]), 6)
        d["last_verdict"] = verd.get(d["address"])
        d["ev_eth"] = round(eth(d.get("ev_wei")), 6) if d.get("ev_wei") is not None else None
        d["created_utc"] = fmt_ts(d.get("created"))
        rows.append(d)
    rows.sort(key=lambda x: -(x["l0_wei"] or 0))
    return rows


def lineage_clusters(c):
    """template_id -> [addresses]; plus per-cluster L0 for context."""
    l0 = l0_map(c)
    out = []
    for r in c.execute(
        "SELECT template_id, address, delta_regions FROM lineage ORDER BY template_id, address"
    ):
        out.append(dict(r))
    clusters = {}
    for row in out:
        clusters.setdefault(tpl(row["template_id"]), []).append(row["address"])
    return [
        {"template_id": t, "members": a, "member_count": len(a),
         "l0_eth": round(sum(eth(l0.get(x)) for x in a), 6)}
        for t, a in clusters.items()
    ]


def vclass_footer(c):
    return {r["vclass"] or "-": r["n"]
            for r in c.execute("SELECT vclass, COUNT(*) n FROM findings GROUP BY vclass "
                               "ORDER BY n DESC")}


def build_rollup(c, db_path):
    targets, by_status, extra, agg = counts(c)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db": os.path.abspath(db_path),
        "header": {
            "total_targets": targets,
            "findings_by_status": by_status,
            **({"findings_by_status_other": extra} if extra else {}),
            "aggregate_l0_wei": str(agg),
            "aggregate_l0_eth": round(eth(agg), 6),
        },
        "suspects": findings_rows(c, "SUSPECT"),
        "hardened": findings_rows(c, "HARDENED"),
        "lineage": lineage_clusters(c),
        "footer": {"findings_by_vclass": vclass_footer(c)},
    }


# ---- console rendering -------------------------------------------------------

W_ADDR, W_VCLASS, W_TIER, W_TPL, W_SIM = 44, 18, 5, 13, 6


def line(ch="-", n=118):
    return ch * n


def render_header(d):
    h = d["header"]
    print("VERITAS EXECUTIVE FINDINGS ROLLUP")
    print(line("="))
    print(f"generated      : {d['generated']}")
    print(f"db             : {d['db']}")
    print(f"total targets  : {h['total_targets']}")
    s = h["findings_by_status"]
    print(f"findings       : {s['SUSPECT']} SUSPECT / {s['HARDENED']} HARDENED / {s['INFO']} INFO"
          + (f" / other: {h['findings_by_status_other']}" if "findings_by_status_other" in h else ""))
    print(f"L0 custody     : {h['aggregate_l0_eth']:,.4f} ETH  (measured, sum of inventory L0)")


def render_table(title, rows, empty_msg):
    print(f"\n{title}")
    print(line())
    if not rows:
        print(empty_msg)
        return
    print(f"{'ADDRESS':<{W_ADDR}}{'VCLASS':<{W_VCLASS}}{'TIER':<{W_TIER}}"
          f"{'TEMPLATE':<{W_TPL}}{'SIM':<{W_SIM}}{'L0 (ETH)':>14}  {'LAST VERDICT':<18}{'CREATED'}")
    for r in rows:
        sim = f"{r['similarity']:.3f}" if isinstance(r["similarity"], (int, float)) else "-"
        print(f"{r['address']:<{W_ADDR}}{trunc(or_dash(r['vclass']), W_VCLASS - 1):<{W_VCLASS}}"
              f"{or_dash(r['tier']):<{W_TIER}}{trunc(r['template_id'], W_TPL - 1):<{W_TPL}}"
              f"{sim:<{W_SIM}}{fmt_eth(r['l0_wei']):>14}  "
              f"{trunc(or_dash(r['last_verdict']), 17):<18}{r['created_utc']}")
        if r.get("recipe") or r.get("ev_wei") is not None:
            ev = f"{eth(r['ev_wei']):,.4f} ETH" if r.get("ev_wei") is not None else "-"
            print(f"{'':>{W_ADDR}}exploit: recipe={trunc(or_dash(r['recipe']), 40)}  "
                  f"ev={ev}  ceiling={fmt_eth(r.get('ceiling_wei'))} ETH  "
                  f"p_success={r['p_success'] if r['p_success'] is not None else '-'}")
        if r.get("evidence"):
            print(f"{'':>{W_ADDR}}evidence: {trunc(r['evidence'])}")


def render_lineage(clusters):
    print("\nLINEAGE (cluster memberships)")
    print(line())
    if not clusters:
        print("0 lineage rows — no clusters recorded yet")
        return
    for cl in clusters:
        print(f"cluster {cl['template_id']:<20} members={cl['member_count']:<4} "
              f"L0={cl['l0_eth']:,.4f} ETH")
        for a in cl["members"]:
            print(f"    {a}")


def render_footer(d):
    v = d["footer"]["findings_by_vclass"]
    print("\nFOOTER — findings by vclass")
    print(line())
    if not v:
        print("0 findings recorded — ecosystem scan pending")
        return
    for k, n in v.items():
        print(f"  {k:<24} {n}")


def render_console(d):
    render_header(d)
    s = d["header"]["findings_by_status"]["SUSPECT"]
    if s == 0:
        print("\nSUSPECTS")
        print(line())
        print("0 SUSPECT findings — ecosystem scan pending")
    else:
        render_table(f"SUSPECTS ({s}) — sorted by L0 balance desc", d["suspects"], "none")
    h = d["header"]["findings_by_status"]["HARDENED"]
    if h == 0:
        print("\nHARDENED")
        print(line())
        print("0 HARDENED findings — none recorded")
    else:
        render_table(f"HARDENED ({h})", d["hardened"], "none")
    render_lineage(d["lineage"])
    render_footer(d)


# ---- single-target deep report ------------------------------------------------

def render_target(c, addr):
    addr = addr.strip()
    t = c.execute("SELECT * FROM targets WHERE address = ?", (addr,)).fetchone()
    print(f"VERITAS TARGET DEEP REPORT — {addr}")
    print(line("="))
    if t is None:
        # still show any orphan findings/probes honestly
        print("NOT in targets table (no fingerprint row).")
    else:
        print(f"chain         : {or_dash(t['chain'])}")
        print(f"code_size     : {or_dash(t['code_size'])} bytes")
        print(f"deploy_block  : {or_dash(t['deploy_block'])}")
        print(f"template_id   : {tpl(t['template_id'])}")
        sim = t["similarity"]
        print(f"similarity    : {f'{sim:.4f}' if isinstance(sim, (int, float)) else '-'}")
        print(f"first_seen    : {fmt_ts(t['first_seen'])}")

    inv = c.execute("SELECT * FROM inventory WHERE address = ? ORDER BY layer, asset", (addr,)).fetchall()
    print(f"\nINVENTORY ({len(inv)} rows)")
    print(line())
    if not inv:
        print("  no inventory rows — unmeasured/zero custody")
    for r in inv:
        print(f"  {r['layer']:<6} {or_dash(r['asset']):<10} {eth(r['amount_wei']):>16,.6f} ETH"
              f"  (wei={or_dash(r['amount_wei'])})  block={or_dash(r['block'])} "
              f"src={or_dash(r['source'])}")

    fnd = c.execute("SELECT * FROM findings WHERE address = ? ORDER BY id", (addr,)).fetchall()
    print(f"\nFINDINGS ({len(fnd)})")
    print(line())
    if not fnd:
        print("  no findings recorded for this target")
    for r in fnd:
        print(f"  #{r['id']} [{or_dash(r['status'])}] {or_dash(r['vclass'])} / tier={or_dash(r['tier'])}"
              f" / conf={or_dash(r['confidence'])} / created={fmt_ts(r['created'])}")
        if r["evidence"]:
            print(f"     evidence: {trunc(r['evidence'])}")
        e = c.execute("SELECT * FROM exploitability WHERE finding_id = ?", (r["id"],)).fetchone()
        if e:
            ps = f"{e['p_success']:.3f}" if isinstance(e["p_success"], (int, float)) else "-"
            comp = f"{e['competition']:.3f}" if isinstance(e["competition"], (int, float)) else "-"
            print(f"     exploit: recipe={trunc(or_dash(e['recipe']), 60)}")
            print(f"              ceiling={fmt_eth(e['ceiling_wei'])} ETH  ev={fmt_eth(e['ev_wei'])} ETH"
                  f"  p_success={ps}  competition={comp}")
            if e["rationale"]:
                print(f"              rationale: {trunc(e['rationale'])}")
        else:
            print("     exploit: - (no exploitability row)")

    prb = c.execute("SELECT * FROM probes WHERE address = ? ORDER BY id", (addr,)).fetchall()
    print(f"\nPROBES ({len(prb)})")
    print(line())
    if not prb:
        print("  no probes recorded for this target")
    for r in prb:
        print(f"  #{r['id']} [{or_dash(r['battery'])}] {or_dash(r['probe'])}: "
              f"{or_dash(r['verdict'])}  ({fmt_ts(r['ts'])})")
        if r["result"]:
            print(f"     result: {trunc(r['result'])}")

    ln = c.execute("SELECT * FROM lineage WHERE address = ?", (addr,)).fetchone()
    print("\nLINEAGE")
    print(line())
    if ln:
        print(f"  cluster={tpl(ln['template_id'])}  delta_regions={trunc(or_dash(ln['delta_regions']))}")
    else:
        print("  no lineage membership recorded")

    if t is None and not fnd and not prb and not inv:
        print("\n[report] target unknown — zero rows across all tables")
        return 1
    return 0


# ---- CLI -----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="VERITAS executive findings rollup (read-only)")
    ap.add_argument("--db", default=DEFAULT_DB, help="path to veritas.db (default: beside report.py)")
    ap.add_argument("--json", metavar="OUT", help="write rollup as JSON to this file")
    ap.add_argument("--target", metavar="0xADDR", help="single-target deep report")
    args = ap.parse_args()

    c, ro = connect_ro(args.db)
    try:
        if args.target:
            sys.exit(render_target(c, args.target))
        d = build_rollup(c, args.db)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, default=str)
            n = len(json.dumps(d, default=str))
            print(f"[report] wrote {n} bytes -> {os.path.abspath(args.json)} "
                  f"({d['header']['total_targets']} targets, "
                  f"{sum(d['header']['findings_by_status'].values())} findings)")
        else:
            render_console(d)
    finally:
        c.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
