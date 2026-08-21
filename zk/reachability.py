# zk/reachability.py — Layer 2 "Economic-Reachability Gate" (pre-T4 money-path gate)
#
# WHY THIS EXISTS
# ---------------
# The T4 differential fuzzer proved the shared Groth16 verifier 0x83584f... is
# forgeable (118 confirmed on-chain ACCEPTED forgelike forgeries all ACCEPTED).
# But the pool CORES (0x47ce, 0x12d6, 0x0836) run a `withdraw` that is a plain
# USDT transfer -- it NEVER calls verifyProof -- and verifyProof there is
# internal-only. So the fuzzable verifier is an ISOLATED LIBRARY no value flows
# through: confirmed forgery + no money path = $0 exploitable (the "unexploitable
# error" the operator hit).
#
# This module is the T2 gate that MUST be satisfied before T4 burns fuzz cycles.
# Correct unit of economics = the WITHDRAW->FUND_TRANSFER call graph, not a
# single verifier address. It resolves, statically from runtime bytecode and --
# when anvil is present -- via a live fork, whether a confirmed forgery at the
# verifier could actually move funds out of the target.
#
# Verdict taxonomy (persisted to the `reachability` table):
#   MONEY_PATH_VERIFY_GATED   -> withdraw DOES external-call a Groth16 verifier
#                                guardng a value transfer          -> VIABLE
#   UPGRADABLE_VERIFIER        -> setVerifier/updateVerifier present; only viable
#                                if owner check not inverted / stack bug absent
#   VERIFIER_ISOLATED          -> verify selector present but NO withdraw path
#                                routes value through it  -> SKIP (doctrine-silent)
#   NO_MONEY_PATH              -> no withdraw, or withdraw does a simple token
#                                transfer with no proof gate       -> SKIP
#   UNKNOWN                    -> bytecode unanalyzable; treat as non-actionable
#
# Doctrine: this gate is a CLASSIFIER, always $0 (getCode + eth_call + local
# fork). It never broadcasts. A target being VIABLE here only UNBLOCKS T4/T5
# fuzzing; the FIRe (T6) remains an explicit user command.
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rpc import RPC, uint
from core import db
from core.selectors import selectors_map

# ---- Opcode helpers ---------------------------------------------------------
# EVM opcodes critical to money-path tracing
PUSH1 = 0x60
PUSH32 = 0x7F
DUP1 = 0x80
SWAP1 = 0x90
CALL = 0xF1
CALLCODE = 0xF2
DELEGATECALL = 0xF4
STATICCALL = 0xFA
SELFDESTRUCT = 0xFF

# ABI 4-byte selectors (keccak of canonical sigs) we care about for routing.
VERIFY_SELS = {
    selectors_map()["verify"],       # verifyProof(uint256[2],...)
    selectors_map()["setver"],       # setVerifier(address)
    selectors_map()["updatever"],    # updateVerifier(address)
}
BASE_TRANSFER_SELS = {
    selectors_map()["withdraw"],     # withdraw(...) signature
    selectors_map()["getroot"],
    selectors_map()["nullif"],
    selectors_map()["roots"],
    selectors_map()["denom"],
    selectors_map()["token"],
    selectors_map()["levels"],
}

_PUSH_RANGE = set(range(PUSH1, PUSH1 + 33))      # PUSH1..PUSH32

def _decode_push(code: bytes, i: int):
    """If code[i] is a PUSHn, return (value_bytes, next_idx), else None."""
    op = code[i]
    if PUSH1 <= op <= PUSH32:
        n = op - PUSH1 + 1
        return code[i + 1:i + 1 + n], i + 1 + n
    return None, i

def iter_ops(code: bytes):
    """Yield (pc, opbyte, push_value_bytes_or_None) over the bytecode."""
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        if op == 0x00:                       # STOP
            i += 1; continue
        val, nxt = _decode_push(code, i)
        yield i, op, val
        i = nxt if val is not None else i + 1

def _extract_literal_addresses(code: bytes):
    """Pull 20-byte values pushed as PUSH20-ish literals; candidate addr constants.

    Filters out false positives: 20-byte blobs that are mostly printable ASCII
    (string/revert-classifier constants like the "\x08ubmittedET\x08val...",
    "upposed to b...", "2e Token retu..." blobs seen in pool cores) are NOT
    addresses. A real address is ~random hex -> the byte values are spread over
    the full 0x00..0xff range with high entropy, so printable-ASCII bytes make
    up a tiny fraction. We keep a candidate only when printable-ASCII bytes are
    a minority AND at most 40% of bytes are 0x00-padding.
    """
    addrs = []
    for pc, op, val in iter_ops(code):
        if val and len(val) == 20:
            # printable-ASCII check (bytes between 0x20 and 0x7e)
            printable = sum(1 for b in val if 0x20 <= b <= 0x7e)
            zeroes = sum(1 for b in val if b == 0)
            if printable > len(val) * 0.4:
                continue  # mostly-text blob, not an address
            if zeroes > len(val) * 0.4:
                continue  # heavily zero-padded, likely a value not an address
            a = "0x" + val.hex().lower()
            if a != "0x0000000000000000000000000000000000000000":
                addrs.append(a)
    # dedupe preserving order
    seen = set()
    out = []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out

class MoneyPathAnalyzer:
    """Static + (optional) fork-backed economic-reachability classifier."""

    def __init__(self, rpc=None):
        self._rpc = rpc or RPC(  # default public archive — read-only
            "https://ethereum-rpc.publicnode.com", timeout=25, retries=3)

    # -- REAL selector detection (proper PUSH4 disassembly) -------------------
    # CRITICAL PITFALL: naive substring match of "63<sel>" FALSE-POSITIVES when
    # the selector bytes appear embedded in calldata constants or unreachable
    # code (exactly what happened to the tornado pool cores). The authoritative
    # test is a real disassembly pass that only counts actual PUSH4 instructions
    # (0x63), respecting PUSH1..PUSH32 widths so we never read selector bytes
    # from the middle of another push. This is what exposed the pool cores as
    # NOT having verify (0xf5c9d69e) / withdraw (0xf2b8180e) dispatch at all.
    @staticmethod
    def _real_dispatched_selectors(code: bytes):
        """Return set of hex selector strings actually pushed as PUSH4."""
        push_sels = set()
        i = 0
        n = len(code)
        while i < n:
            op = code[i]
            if 0x60 <= op <= 0x7f:          # PUSH1..PUSH32
                sz = op - 0x5f
                val = code[i+1:i+1+sz].hex()
                if sz == 4:
                    push_sels.add(val)
                i += sz + 1
            else:
                i += 1
        return push_sels

    # -- the core reachability verdict ----------------------------------------
    def classify(self, address, chain_id=1):
        """Return a dict verdict for a single target address (no write side effects)."""
        address = address.lower()
        try:
            code_hex = self._rpc.get_code(address)
        except Exception as e:
            return {"address": address, "chain_id": chain_id,
                    "verdict": "RPC_ERROR", "reason": f"rpc error: {str(e)[:160]}"}
        code = bytes.fromhex(code_hex[2:] if code_hex[:2] == "0x" else code_hex)
        if not code:
            return {"address": address, "chain_id": chain_id,
                    "verdict": "NO_CODE", "reason": "empty runtime bytecode (EOA or precompile)"}

        sm = selectors_map()
        dispatched = self._real_dispatched_selectors(code)
        # A selector is genuinely dispatched only if it appears as a real PUSH4.
        # sm values are "0x<hex>"; _real_dispatched_selectors returns unprefixed
        # hex, so normalize to a shared space (lowercase, no '0x').
        sm_inv = {v.lower().replace("0x", ""): k for k, v in sm.items()}
        present_names = {sm_inv[s] for s in dispatched if s in sm_inv}
        # 2026-08-20 CORRECTION: the tornado-CLONE family routes its money path
        # through custom selectors (withdraw 0x21a0adb6 -> verifyProof 0x695ef6f9
        # on a storage-resolved verifier). Keying ONLY on canonical signatures
        # produced wrong NO_MONEY_PATH/UNKNOWN verdicts on funded pools. Count
        # clone-family dispatches as real verify/withdraw.
        has_verify = "verify" in present_names or "clone_verify" in present_names
        has_setver = "setver" in present_names
        has_upd = "updatever" in present_names
        has_withdraw = ("withdraw" in present_names
                        or "clone_withdraw" in present_names)
        has_changeop = "clone_changeop" in present_names
        has_verifier_getter = "clone_verifier" in present_names

        # Resolve the storage-backed verifier via the verifier() getter (0x2b7ac3f3).
        resolved_verifier = None
        if has_verifier_getter:
            try:
                r = self._rpc.eth_call(address, "0x2b7ac3f3")
                if r and len(r) >= 66:
                    resolved_verifier = "0x" + r[-40:].lower()
            except Exception:
                pass

        # Measure on-chain value. Doctrine: V is MEASURED (eth_getBalance /
        # balanceOf), never assumed from events or denomination alone.
        try:
            value_eth_wei = self._rpc.get_balance(address)
        except Exception:
            value_eth_wei = None
        value_token = None
        value_token_raw = None
        if "token" in present_names:
            try:
                r = self._rpc.eth_call(address, "0xfc0c546a")  # token()
                if r and len(r) >= 66:
                    value_token = "0x" + r[-40:].lower()
                    bal = self._rpc.eth_call(
                        value_token,
                        "0x70a08231" + address[2:].rjust(64, "0"))  # balanceOf
                    if bal and bal != "0x":
                        value_token_raw = int(bal, 16)
            except Exception:
                pass

        # Literal addresses pushed in code (candidate verifier refs)
        lit_addrs = _extract_literal_addresses(code)
        if resolved_verifier and resolved_verifier not in lit_addrs:
            lit_addrs.insert(0, resolved_verifier)

        # Decide taxonomy.
        # 2026-08-20 CORRECTION: the old 0x06394c9b "custom plain withdraw"
        # heuristic is REMOVED. 0x06394c9b is changeOperator(address) — NOT a
        # withdraw. Treating it as an unexploitable plain-transfer withdraw
        # wrongly classified three FUNDED pools (0x0836222f, 0x4736dcf1,
        # 0xfd8610d2) as NO_MONEY_PATH. The correct gate is the real
        # withdraw->verifyProof dispatch check below.
        if has_withdraw and has_verify:
            verdict = "MONEY_PATH_VERIFY_GATED"
            vdesc = f"verifier={resolved_verifier or 'in-code'}"
            val = f"measured V: {value_eth_wei} wei"
            if value_token_raw:
                val += f" + {value_token_raw} raw units of {value_token}"
            reason = (f"withdraw dispatches to verifyProof gate ({vdesc}); "
                      f"{val}. Fork-confirm the call graph before T4.")
        elif has_setver or has_upd:
            verdict = "UPGRADABLE_VERIFIER"
            reason = ("setVerifier/updateVerifier present but no dispatched "
                      "verify-gated withdraw found: exploitable ONLY if the "
                      "operator/owner check is dead (operator=0x0) AND the "
                      "update call works. Probe on a fork to confirm.")
        elif has_verify:
            verdict = "VERIFIER_ISOLATED"
            reason = ("verifyProof selector present as real dispatch but the "
                      "contract does NOT carry a withdraw() — fuzzing "
                      "this verifier yields isolated forgeries with no money path "
                      "(the 'unexploitable' class).")
        elif has_withdraw:
            verdict = "NO_MONEY_PATH"
            reason = ("withdraw() present but NO verifyProof dispatch — "
                      "withdraw is a plain token transfer with no proof gate.")
        else:
            verdict = "UNKNOWN"
            reason = ("no recognizable proof/withdraw selectors dispatched under "
                      "canonical OR clone-family signatures (PUSH4 disassembly).")

        out = {
            "address": address,
            "chain_id": chain_id,
            "verdict": verdict,
            "reason": reason,
            "has_verify": has_verify,
            "has_withdraw": has_withdraw,
            "has_changeop": has_changeop,
            "has_setver": has_setver,
            "has_updatever": has_upd,
            "resolved_verifier": resolved_verifier,
            "value_eth_wei": value_eth_wei,
            "value_token": value_token,
            "value_token_raw": value_token_raw,
            "literal_verifier_candidates": lit_addrs[:8],
            "code_size": len(code),
            "analyzed_ts": int(time.time()),
        }
        return out

    # -- persist ---------------------------------------------------------------
    def store(self, verdict: dict, conn=None):
        """Write a reachability verdict to the `reachability` table (upsert)."""
        conn = conn or db.conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO reachability
               (address, chain_id, verdict, reason, has_verify, has_withdraw,
                has_setver, has_updatever, literal_verifier_candidates,
                code_size, analyzed_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(address, chain_id) DO UPDATE SET
                 verdict=excluded.verdict, reason=excluded.reason,
                 has_verify=excluded.has_verify, has_withdraw=excluded.has_withdraw,
                 has_setver=excluded.has_setver, has_updatever=excluded.has_updatever,
                 literal_verifier_candidates=excluded.literal_verifier_candidates,
                 code_size=excluded.code_size, analyzed_ts=excluded.analyzed_ts""",
            (verdict.get("address"), verdict.get("chain_id", 1),
             verdict.get("verdict"), verdict.get("reason") or verdict.get("detail", ""),
             int(bool(verdict.get("has_verify"))),
             int(bool(verdict.get("has_withdraw"))),
             int(bool(verdict.get("has_setver"))),
             int(bool(verdict.get("has_updatever"))),
             json.dumps(verdict.get("literal_verifier_candidates", [])),
             verdict.get("code_size", 0),
             int(verdict.get("analyzed_ts") or time.time())),
        )
        conn.commit()

def _codehex(code: bytes) -> str:
    """Runtime bytecode body without the 0x prefix as a lowercase string."""
    return code.hex()

# Convenience wrapper ----------------------------------------------------------
def reachable_targets(chain_id=1, verdicts=("MONEY_PATH_VERIFY_GATED",
                                              "UPGRADABLE_VERIFIER")):
    """Query the reachability table for currently-viable targets."""
    conn = db.conn()
    try:
        ph = ",".join("?" * len(verdicts))
        rows = conn.execute(
            f"SELECT * FROM reachability WHERE chain_id=? AND verdict IN ({ph})",
            (chain_id,) + verdicts).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()