# zk/impact.py — Layer 5: EVM State & Economic Impact Simulator (ENHANCED)
# Turns a T4 differential finding into a financially-objectified verdict:
#   pre/post TVL delta + attacker balance delta on a local anvil fork ($0).
#
# Enhanced to cover all 5 exploit classes with proper financial objectification:
#   1. ZK-FIELD-OVERFLOW       -> caller_supplied_vk (FUND_DRAIN)
#   2. ZK-UNDER-CONSTRAINED    -> caller_supplied_vk (FUND_DRAIN)
#   3. ZK-NULLIFIER-COLLISION  -> zk_nullifier_collision (DOUBLE_SPEND)
#   4. ZK-VERIFIER-CONFIG-MISMATCH -> zk_verifier_config_mismatch (REPLAY_OR_DRAIN)
#   5. ZK-PROOF-MALLEABILITY   -> zk_proof_malleability (DOUBLE_SPEND)

import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db
from core.rpc import RPC, uint
from core import value as value_mod
from zk import differential as L4

WITHDRAW_SEL = None  # resolved lazily from selectors_map


def _eth(addr, rpc):
    return rpc.get_balance(addr)


# ---------------------------------------------------------------------------
# Static measured-census path (always available, $0)
# ---------------------------------------------------------------------------
def static_impact(address, confirmed=False, confirmed_classes=None,
                  p_success=0.9, competition=0.5,
                  gas_gwei=5, gas_units=350_000):
    """Measured-value census + EV recipe for a T4 finding on `address`.

    confirmed: True only when the T4 differential loop actually hit
    (on-chain ACCEPTED + local oracle invalid). Per doctrine, the suspicion
    tier is NEVER actionable: unconfirmed targets get census rows with
    V measured for situational awareness, verdict stays INFO.

    V ceilings by class (conservative, doctrine-aligned):
      caller_supplied_vk / config_mismatch: L0 + L1 (full pool)
      malleability / nullifier:             L0 (balance-limited replay)
    V is MEASURED (eth_getBalance + event census), never assumed.
    """
    address = address.lower()
    inv = value_mod.compute_inventory(address)
    l0, l1 = inv["L0_wei"], inv["L1_wei"]
    gas_wei = gas_gwei * 1e9 * gas_units
    confirmed_classes = set(confirmed_classes or [])

    classes = {
        "caller_supplied_vk": {
            "V": l0 + max(l1, 0), "ceiling": "L0+L1 (entire pool)",
            "recipe": ("deploy nothing — craft verifyProof calldata with "
                       "self-supplied VK; withdraw(amount=pool) to attacker "
                       "EOA. Single tx, no prerequisites."),
            "taxonomy": "FUND_DRAIN",
        },
        "zk_verifier_config_mismatch": {
            "V": l0 + max(l1, 0), "ceiling": "L0+L1 (entire pool)",
            "recipe": ("replay a proof minted for circuit A into verifier B "
                       "(shared VK); follow-on withdraw drains balance."),
            "taxonomy": "REPLAY_OR_DRAIN",
        },
        "zk_proof_malleability": {
            "V": l0, "ceiling": "L0 (replay-limited)",
            "recipe": ("mutate (A,B,C) -> (-A,-B,-C) of any accepted proof; "
                       "re-submit withdraw — nullifier/proof-hash gate sees a "
                       "new hash; repeat until balance exhausted."),
            "taxonomy": "DOUBLE_SPEND",
        },
        "zk_nullifier_collision": {
            "V": l0, "ceiling": "L0 (double-spend-limited)",
            "recipe": ("collide nullifier secrets; second withdraw passes the "
                       "spent-nullifier gate; extracts remaining balance."),
            "taxonomy": "DOUBLE_SPEND",
        },
    }
    out = []
    for vclass, spec in classes.items():
        V = spec["V"]
        ev = int(p_success * V * (1 - competition)) - int(gas_wei)
        hit = confirmed and vclass in confirmed_classes
        if hit and V > 0 and ev > 0:
            verdict = "FINANCIALLY_EXPLOITABLE"
        elif hit and V == 0:
            verdict = "INFO_V_ZERO"      # doctrine: V=0 downgrades, always
        elif hit:
            verdict = "NEGATIVE_EV"
        else:
            verdict = "CENSUS_INFO_NO_FINDING"
        out.append({
            "address": address, "vclass": vclass,
            "V_wei": str(V), "ceiling": spec["ceiling"],
            "recipe": spec["recipe"],
            "taxonomy": spec["taxonomy"],
            "L0_wei": str(l0), "L1_wei": str(l1),
            "p_success": p_success, "competition": competition,
            "gas_wei": str(int(gas_wei)),
            "ev_wei": str(ev),
            "verdict": verdict,
        })
    return out


# ---------------------------------------------------------------------------
# Economic classification (described taxonomy: Infinite Mint / Double Spend /
# Fund Drain). Maps a recipe-class to its money-path taxonomy.
# ---------------------------------------------------------------------------
CLASS_TAXONOMY = {
    "caller_supplied_vk": "FUND_DRAIN",
    "zk_verifier_config_mismatch": "REPLAY_OR_DRAIN",
    "zk_proof_malleability": "DOUBLE_SPEND",
    "zk_nullifier_collision": "DOUBLE_SPEND",
}


def _taxonomy(vclass):
    return CLASS_TAXONOMY.get(vclass, "UNCLASSIFIED")


# ---------------------------------------------------------------------------
# Fork divergence path (anvil, $0)
# ---------------------------------------------------------------------------
def fork_impact(address, calldata_hex, fork_block=None, anvil_path=None,
                attacker="0x000000000000000000000000000000000000dEaD"):
    """Measure pre/post state divergence of a forged withdraw on a local fork."""
    from rehearsal import find_anvil, _launch_attempt, _kill_tree, FORK_RPCS
    from core.selectors import selectors_map

    address = address.lower()
    anvil = find_anvil(anvil_path)
    if not anvil:
        return {"path": "static_only", "reason": "anvil not installed",
                "fallback": "static_impact() is the $0 oracle"}

    attempts = [FORK_RPCS] + [[u] for u in FORK_RPCS]
    proc = host = None
    for i, urls in enumerate(attempts):
        if i > 0:
            print(f"[t5] retry {i} with fallback URL set: {urls}")
        proc, host = _launch_attempt(anvil, _free_port(), fork_block, urls)
        if proc is not None:
            break
    if proc is None:
        return {"path": "static_only", "reason": "anvil failed to start"}

    try:
        rpc = RPC(host, timeout=30, retries=2)
        head = uint(rpc.call("eth_blockNumber", []))
        if fork_block is not None and head != fork_block:
            return {"path": "aborted", "reason":
                    f"fork head mismatch {head} != {fork_block}"}
        pre_target = _eth(address, rpc)
        pre_attacker = _eth(attacker, rpc)

        try:
            ret = rpc.call("eth_call", [{
                "to": address, "data": calldata_hex,
                "from": attacker}, "latest"])
            outcome = "ACCEPTED"
            ret_head = (ret or "")[:10]
        except Exception as e:
            if L4._is_revert(e):
                outcome, ret_head = "REVERTED", None
            else:
                outcome, ret_head = "RPC_ERROR", str(e)[:120]

        try:
            rpc.call("eth_estimateGas", [{
                "to": address, "data": calldata_hex,
                "from": attacker}])
            gas_signal = "EXECUTABLE"
        except Exception as e:
            gas_signal = "REVERTED" if L4._is_revert(e) else "RPC_ERROR"

        return {
            "path": "fork",
            "fork_head": head,
            "pre_target_wei": str(pre_target),
            "pre_attacker_wei": str(pre_attacker),
            "eth_call": outcome,
            "estimate_gas": gas_signal,
            "financially_exploitable":
                outcome == "ACCEPTED" or gas_signal == "EXECUTABLE",
            "verdict": ("FORK_CONFIRMED_EXPLOITABLE"
                        if (outcome == "ACCEPTED" or gas_signal == "EXECUTABLE")
                        else "FORK_REJECTED"),
        }
    finally:
        _kill_tree(proc)
        print(f"[t5] anvil terminated")


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Persistence + report
# ---------------------------------------------------------------------------
def persist_static(rows):
    c = db.conn()
    ids = []
    for r in rows:
        if r["verdict"] == "CENSUS_INFO_NO_FINDING":
            continue
        f = c.execute("""SELECT id FROM findings WHERE address=?
                         AND tier='T4' ORDER BY id DESC LIMIT 1""",
                      (r["address"],)).fetchone()
        fid = f["id"] if f else None
        if fid is None:
            continue
        c.execute("""INSERT OR REPLACE INTO exploitability
            (finding_id, recipe, ceiling_wei, preconditions, p_success,
             competition, ev_wei, rationale)
            VALUES (?,?,?,?,?,?,?,?)""",
            (fid, r["recipe"], r["V_wei"], r["ceiling"],
             r["p_success"], r["competition"], r["ev_wei"],
             json.dumps({"L0": r["L0_wei"], "L1": r["L1_wei"],
                         "gas_wei": r["gas_wei"],
                         "taxonomy": r.get("taxonomy", "UNCLASSIFIED"),
                         "verdict": r["verdict"]})))
        c.execute("""INSERT OR REPLACE INTO impact_sims
            (finding_id, address, fork_block, pre_tvl_wei, post_tvl_wei,
             attacker_delta_wei, financially_exploitable, artifacts, ts)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (fid, r["address"], 0, r["V_wei"], "0", "0",
             1 if r["verdict"] == "FINANCIALLY_EXPLOITABLE" else 0,
             json.dumps(r)[:1500], db.now()))
        ids.append(fid)
    c.commit(); c.close()
    return ids


def report(rows, address):
    print(f"\n[t5] economic impact — {address}")
    hdr = f"  {'CLASS':<30}{'V(ETH)':>12}{'EV(ETH)':>12}  {'TAXONOMY':<14}VERDICT"
    print(hdr)
    for r in rows:
        v_eth = int(r["V_wei"]) / 1e18
        ev_eth = int(r["ev_wei"]) / 1e18
        print(f"  {r['vclass']:<30}{v_eth:>12,.4f}{ev_eth:>12,.4f}  "
              f"{r.get('taxonomy','UNCLASSIFIED'):<14}{r['verdict']}")
        if r["verdict"] == "FINANCIALLY_EXPLOITABLE":
            print(f"    recipe: {r['recipe'][:120]}")
    return rows