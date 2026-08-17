# core/probes.py — T2 deterministic probe batteries ($0, eth_call only)
from core.rpc import uint

ZERO_WORDS_2   = "0x" + "00" * 64
def words(n, val=0):
    # BARE hex (no 0x prefix): callers concatenate after a selector or other
    # hex digits — a prefixed return produced "0xSelector0x000..." malformed
    # calldata that nodes reject with Invalid params before execution.
    return ("%064x" % val) * n

# --- failure classification --------------------------------------------
# DEFECT FIX (task 2d): a transport failure (DNS failure, connection reset,
# timeout after retries) used to be swallowed as REVERTED_HEALTHY /
# NO_MAPPING / HARDENED — a network outage mid-battery produced FALSE
# HEALTHY verdicts (silent false-negatives). Healthy verdicts now require
# an actual chain response; every other exception is RPC_ERROR.
#
# How genuine reverts arrive: core/rpc.py raises RuntimeError with the JSON
# "error" response body (e.g. "rpc eth_call: {'code':3, 'message':'execution
# reverted', ...}") once the node ANSWERED. Transport failures arrive as
# urllib/socket exceptions (URLError, timeout, gaierror...). _is_revert()
# discriminates on that: only a server response naming a revert counts.
def _is_revert(e):
    """True ONLY if the node answered and the answer names a revert.

    Genuine reverts arrive two ways:
      1. RuntimeError from core/rpc.py carrying the JSON-RPC error dict
         ({"code": 3, "message": "execution reverted", ...}).
      2. HTTPError where the gateway maps the revert to HTTP 4xx/5xx but the
         body still carries the JSON-RPC error naming the revert.
    Transport failures (DNS, reset, timeout) match neither -> RPC_ERROR.
    """
    if "revert" in str(e).lower():
        return True
    try:
        body = e.read()  # HTTPError only; other exceptions lack .read()
        return b"revert" in body[:512].lower()
    except Exception:
        return False

def _rpc_error(probe, e):
    # transport/RPC failure — verdict is NOT healthy (fail-closed)
    return {"probe": probe, "verdict": "RPC_ERROR", "error": str(e)[:80]}

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
    except Exception as e:
        if _is_revert(e):
            # node answered: reverted => pairing check ran and failed on
            # hardcoded VK (healthy)
            return {"probe": "self_vk_zero", "verdict": "REVERTED_HEALTHY"}
        return _rpc_error("self_vk_zero", e)

def _is_nonzero(r):
    return r not in ("0x", "", None) and set(r[2:]) != {"0"}

# --- Battery B: nullifier replay --------------------------------------
def probe_nullifier_replay(rpc, address, spent_nullifier_hex):
    # nullifierHashes(bytes32) view read: 1 => already spent and gated (healthy)
    # 0 => mapping unpopulated or unchecked (suspect -> T3 rehearsal)
    data = "0x" + "8c0b1c99" + spent_nullifier_hex.replace("0x", "").rjust(64, "0")
    try:
        r = rpc.eth_call(address, data)
    except Exception as e:
        if _is_revert(e):
            return {"probe": "nullifier_read", "verdict": "REVERTED_HEALTHY"}
        return _rpc_error("nullifier_read", e)
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
            results[name] = "REVERTED" if _is_revert(e) else "RPC_ERROR"
    # REVERTED on all = canonicality enforced (healthy). Any RPC_ERROR means
    # we never got a chain answer — cannot claim HARDENED (fail-closed).
    healthy = all(v == "REVERTED" for v in results.values())
    if healthy:
        verdict = "HARDENED"
    elif any(v == "RPC_ERROR" for v in results.values()):
        verdict = "RPC_ERROR"
    else:
        verdict = "CANONICALITY_GAP_SUSPECT"
    return {"probe": "malformed_points", "results": results, "verdict": verdict}

def run_battery(rpc, address, template_id, known_nullifier=None):
    results = []
    results.append(probe_self_vk(rpc, address))
    if known_nullifier:
        results.append(probe_nullifier_replay(rpc, address, known_nullifier))
    return results
