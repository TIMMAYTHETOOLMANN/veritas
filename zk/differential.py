# zk/differential.py — Layer 4: The Differential Loop
# Chains L1 (extract) -> L2 (witness) -> L3 (core_engine) -> on-chain verifier.
#
# Differential principle: the local py_ecc pairing oracle is ground truth for
# Groth16 soundness. A structured (non-forged) proof is mathematically INVALID
# against the target VK — the local oracle says False. If the on-chain verifier
# ACCEPTS it anyway (non-revert, truthy return), the on-chain enforcement
# deviates from the math: caller-supplied VK, stubbed verifier, or missing
# proof binding. That gap is the exploit surface.
#
# All on-chain interaction is eth_call ($0). Fail-closed verdicts per probes.py
# doctrine: transport failure is RPC_ERROR, never healthy.
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db
from core.rpc import RPC
from core.selectors import selectors_map
from zk import extract as L1
from zk import witness as L2
from zk import core_engine as L3

RPC_URL = "https://ethereum-rpc.publicnode.com"
GAS_CEILING = 350_000  # verifyProof + withdraw headroom estimate


def _is_revert(e):
    """Node ANSWERED and named a revert (mirrors core/probes.py)."""
    if "revert" in str(e).lower():
        return True
    try:
        body = e.read()
        return b"revert" in body[:512].lower()
    except Exception:
        return False


def _truthy(ret):
    """eth_call return decoded as boolean-ish acceptance."""
    if ret in ("0x", "", None):
        return False
    body = ret[2:]
    if set(body) == {"0"}:
        return False
    # abi-encoded bool true is 32 bytes of zeros + 1; any nonzero word counts
    return True


def encode_verify_calldata(sel, proof, pub_inputs):
    """snarkjs verifyProof(uint[2],uint[2][2],uint[2],uint[2]) calldata.

    proof: [ax, ay, bx_c0, bx_c1, by_c0, by_c1, cx, cy] ints (L3 layout).
    pub_inputs: ints, right-aligned 32-byte words appended after _pC.
    """
    words = ["%064x" % (v % (2**256)) for v in proof] + \
            ["%064x" % (v % (2**256)) for v in pub_inputs]
    return sel + "".join(words)


def on_chain_check(rpc, address, calldata):
    """One eth_call. Returns verdict dict — never raises."""
    try:
        ret = rpc.eth_call(address, calldata)
    except Exception as e:
        if _is_revert(e):
            return {"outcome": "REVERTED", "ret": None}
        return {"outcome": "RPC_ERROR", "ret": None, "error": str(e)[:120]}
    return {"outcome": "ACCEPTED" if _truthy(ret) else "RETURNED_FALSE",
            "ret": (ret or "")[:10]}


# ----------------------------------------------------------------------------
# Campaign
# ----------------------------------------------------------------------------

def run_campaign(address, rpc_url=None, corpus_size=64, seed=0x5EED,
                 delay=0.35, persist=True, verbose=True):
    """Full differential campaign against one deployed verifier.

    Returns campaign result dict. Persists fuzz_campaigns + findings + probes.
    """
    address = address.lower()
    rpc = RPC(rpc_url or RPC_URL, timeout=25, retries=3)
    sel = selectors_map()["verify"]

    # ---- L1: extract VK (persisted to vk_registry) --------------------------
    code = rpc.get_code(address)
    if not code or code in ("0x", "0x0"):
        return {"address": address, "error": "no bytecode", "sent": 0}
    vk = L1.extract_vk_from_bytecode(code)
    if persist:
        # extract_vk_for_address re-fetches code; reuse the parse we already
        # did to avoid a second RPC round-trip: inline the persist here.
        c = db.conn()
        if vk:
            c.execute("""INSERT OR REPLACE INTO vk_registry
                (address, chain_id, curve, proof_system, vk_hash, alpha, alpha_pair,
                 beta2, gamma2, delta2, ic_count, ic_points, g1_point_count,
                 extracted_from, extracted_ts)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (address, 1, vk["curve"], vk["proof_system"], vk["vk_hash"],
                 vk["alpha"], vk.get("alpha_pair"), vk["beta2"], vk["gamma2"],
                 vk["delta2"], vk["ic_count"], vk["ic_points"],
                 vk["g1_point_count"], "bytecode_push32", db.now()))
        c.commit(); c.close()

    # ---- L2: corpus ---------------------------------------------------------
    n_pub = max(1, (vk["ic_count"] - 1)) if vk else 2  # tornado convention
    spec = {"n_inputs": n_pub, "unconstrained": [], "vk_hash": vk["vk_hash"] if vk else ""}
    corpus = L2.generate_corpus(spec, seed=seed, n=corpus_size)

    # Malleability family needs a base structured proof; derive from corpus[0].
    base_proof = L3.assemble_proof(corpus[0]["witness"], spec["vk_hash"])

    local_oracle = bool(vk and vk.get("alpha_pair"))  # pairing check possible?
    sent = accepted = rejected = reverted = rpc_errors = 0
    hits = []  # differential violations

    def probe_one(label, vclass, proof, pubs, expect_local_invalid=True):
        nonlocal sent, accepted, rejected, reverted, rpc_errors
        calldata = encode_verify_calldata(sel, proof, pubs)
        on = on_chain_check(rpc, address, calldata)
        sent += 1
        if on["outcome"] == "RPC_ERROR":
            rpc_errors += 1
            return {"label": label, "class": vclass, "on_chain": on}
        if on["outcome"] == "ACCEPTED":
            accepted += 1
            loc = None
            if local_oracle and expect_local_invalid:
                loc = L3.groth16_verify(vk, proof, pubs)  # ground truth
            hits.append({"label": label, "class": vclass, "on_chain": on,
                         "local_oracle_valid": loc,
                         "witness_head": [str(x) for x in pubs[:4]],
                         "calldata_head": calldata[:78]})
        else:
            if on["outcome"] == "REVERTED":
                reverted += 1
            else:
                rejected += 1
        if verbose:
            print(f"    [{label}] {vclass}: {on['outcome']}")
        return None

    for i, w in enumerate(corpus):
        if rpc_errors >= 3:
            if verbose:
                print("    [abort] 3+ RPC errors — fail-closed, stopping campaign")
            break
        proof = L3.assemble_proof(w["witness"], spec["vk_hash"])
        pubs = w["witness"][:n_pub]
        probe_one(f"w{i:03d}:{w['class']}", w["class"], proof, pubs)
        time.sleep(delay)

    # Malleability family: base + negations. Distinct proofs must NOT both
    # verify under a binding verifier; base-accepted + neg-accepted is the
    # classic no-binding signature.
    if rpc_errors < 3:
        pubs = corpus[0]["witness"][:n_pub]
        for mname, mutated, expect in L3.malleability_family(base_proof):
            probe_one(f"mall:{mname}", "ZK-PROOF-MALLEABILITY", mutated, pubs)
            time.sleep(delay)
        # Cross-circuit replay: same proof material, foreign vk_hash binding.
        foreign = L3.assemble_proof(corpus[0]["witness"], "foreign_vk_binding")
        probe_one("xvk:cross", "ZK-VERIFIER-CONFIG-MISMATCH", foreign, pubs)
        time.sleep(delay)

    campaign = {
        "address": address,
        "vk_hash": vk["vk_hash"] if vk else None,
        "vk_extracted": bool(vk),
        "local_oracle": local_oracle,
        "corpus_size": len(corpus),
        "sent": sent,
        "accepted": accepted,
        "rejected": rejected,
        "reverted": reverted,
        "rpc_errors": rpc_errors,
        "hits": hits,
        "backend": L3.detect_backend(),
    }

    if persist:
        _persist(campaign)
    return campaign


def _persist(campaign):
    c = db.conn()
    cur = c.execute("""INSERT INTO fuzz_campaigns
        (address, vk_hash, corpus_size, sent, accepted, rejected, reverted,
         backend, findings_json, ts)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (campaign["address"], campaign["vk_hash"], campaign["corpus_size"],
         campaign["sent"], campaign["accepted"], campaign["rejected"],
         campaign["reverted"], campaign["backend"],
         json.dumps(campaign["hits"])[:4000], db.now()))
    campaign_id = cur.lastrowid
    # Every accepted-but-locally-invalid proof is a finding.
    status = None
    for h in campaign["hits"]:
        critical = (h["on_chain"]["outcome"] == "ACCEPTED"
                    and h.get("local_oracle_valid") is False)
        confidence = "differential_confirmed" if critical else "suspect"
        status = "T4_CONFIRMED" if critical else "T4_SUSPECT"
        c.execute("""INSERT INTO findings
            (address, vclass, tier, confidence, status, evidence, created)
            VALUES (?,?,?,?,?,?,?)""",
            (campaign["address"], h["class"], "T4", confidence, status,
             json.dumps({"campaign_id": campaign_id, "label": h["label"],
                         "on_chain": h["on_chain"],
                         "local_oracle_valid": h.get("local_oracle_valid"),
                         "witness_head": h.get("witness_head")})[:500],
             db.now()))
        c.execute("""INSERT INTO probes
            (address, battery, probe, call_data, result, verdict, ts)
            VALUES (?,?,?,?,?,?,?)""",
            (campaign["address"], "t4_differential", h["label"],
             h.get("calldata_head", ""),
             json.dumps(h["on_chain"])[:200],
             "ACCEPTED_LOCAL_INVALID" if critical else h["on_chain"]["outcome"],
             db.now()))
    c.commit(); c.close()
    return campaign_id


# ----------------------------------------------------------------------------
# Rollup
# ----------------------------------------------------------------------------

def summarize(campaign):
    a = campaign["address"]
    print(f"\n[t4] campaign {a}")
    print(f"     vk={'extracted ' + str(campaign['vk_hash']) if campaign['vk_extracted'] else 'none (heuristic found no VK structure)'}")
    print(f"     backend={campaign['backend']} corpus={campaign['corpus_size']} "
          f"sent={campaign['sent']} accepted={campaign['accepted']} "
          f"reverted={campaign['reverted']} rejected={campaign['rejected']} "
          f"rpc_errors={campaign['rpc_errors']}")
    if campaign["hits"]:
        print(f"     *** {len(campaign['hits'])} DIFFERENTIAL HITS ***")
        for h in campaign["hits"]:
            print(f"       {h['label']:<22} {h['class']:<28} "
                  f"on_chain={h['on_chain']['outcome']:<15} "
                  f"local_oracle={h.get('local_oracle_valid')}")
    else:
        print("     no differential hits — on-chain enforcement matches local "
              "soundness oracle (healthy)")
    return campaign
