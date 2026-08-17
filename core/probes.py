# core/probes.py — T2 deterministic probe batteries ($0, eth_call only)
from core.rpc import uint

ZERO_WORDS_2   = "0x" + "00" * 64
def words(n, val=0):
    return "0x" + ("%064x" % val) * n

# --- Battery A: caller-supplied VK ------------------------------------
# If verifyProof accepts VK as calldata, a zero-VK + zero-proof call that
# reverts ONLY on proof math (not on input validation) distinguishes
# caller-VK verifiers from hardcoded-VK verifiers.
from core.selectors import selectors_map

def probe_self_vk(rpc, address, sel=None):
    if sel is None:
        sel = selectors_map()["verify"]  # verifyProof(uint[2],uint[2][2],uint[2],uint[2])
    data = sel + words(8)  # all-zero points
    try:
        r = rpc.eth_call(address, data)
        # returned without revert: zero-proof ACCEPTED => catastrophic forgery
        return {"probe": "self_vk_zero", "raw": r[:10],
                "verdict": "CONFIRMED_FORGERY" if _is_nonzero(r)
                           else "ZERO_RETURNED_SUSPECT"}
    except Exception:
        # reverted => pairing check ran and failed on hardcoded VK (healthy)
        return {"probe": "self_vk_zero", "verdict": "REVERTED_HEALTHY"}

def _is_nonzero(r):
    return r not in ("0x", "", None) and set(r[2:]) != {"0"}

# --- Battery B: nullifier replay --------------------------------------
def probe_nullifier_replay(rpc, address, spent_nullifier_hex):
    # nullifierHashes(bytes32) view read: 1 => already spent and gated (healthy)
    # 0 => mapping unpopulated or unchecked (suspect -> T3 rehearsal)
    data = "0x" + "8c0b1c99" + spent_nullifier_hex.replace("0x", "").rjust(64, "0")
    try:
        r = rpc.eth_call(address, data)
    except Exception:
        return {"probe": "nullifier_read", "verdict": "NO_MAPPING"}
    val = uint(r)
    return {"probe": "nullifier_read", "spent": val == 1,
            "verdict": "GATED_HEALTHY" if val == 1 else "UNGATED_SUSPECT"}

# --- Battery C: malformed point canonicality ---------------------------
def probe_malformed_points(rpc, address, sel):
    # p = 21888242871839275222246405745257275088696311157297823662689037894645226208583
    p = 21888242871839275222246405745257275088696311157297823662689037894645226208583
    variants = {
        "x_geq_p": sel + ("%064x" % p) + words(7),
        "p_plus_1": sel + ("%064x" % (p + 1)) + words(7),
    }
    results = {}
    for name, data in variants.items():
        try:
            r = rpc.eth_call(address, data)
            results[name] = "ACCEPTED" if _is_nonzero(r) or r == "0x" + "00"*32 and False else ("RETURNED" if r not in ("0x",) else "EMPTY")
        except Exception as e:
            results[name] = "REVERTED"
    # REVERTED on all = canonicality enforced (healthy)
    healthy = all(v == "REVERTED" for v in results.values())
    return {"probe": "malformed_points", "results": results,
            "verdict": "HARDENED" if healthy else "CANONICALITY_GAP_SUSPECT"}

def run_battery(rpc, address, template_id, known_nullifier=None):
    results = []
    results.append(probe_self_vk(rpc, address))
    if known_nullifier:
        results.append(probe_nullifier_replay(rpc, address, known_nullifier))
    return results
