#!/usr/bin/env python3
"""Unit check for the T2 economic-reachability gate: prove it can CLASSIFY a
genuine verify-gated money path as VIABLE (not all-negative), AND correctly
flag a plain-transfer withdraw as NO_MONEY_PATH. Uses SYNTHETIC bytecode, so it
does not touch RPC or the DB. Pure static logic test.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.selectors import selector
from zk import reachability as R

def build_dispatcher_for(*sels):
    """Assemble minimal runtime dispatcher with PUSH4(sel)+DUP+PUSH2+JUMPI per
    selector, guaranteeing each appears as a real PUSH4 dispatch."""
    code = bytearray()
    for sel in sels:
        b = bytes.fromhex(sel)
        code += bytes([0x63]) + b          # PUSH4 sel
        code += bytes([0x80])              # DUP1
        code += bytes([0x61, 0x00, 0x00])  # PUSH2 0
        code += bytes([0x57])              # JUMPI
    code += bytes([0x00])                  # STOP
    return bytes(code)

V = selector("verifyProof(uint256[2],uint256[2][2],uint256[2],uint256[2])")[2:]
W = selector("withdraw(uint256[2],uint256[2][2],uint256[2],uint256[2],bytes32,address,uint256,uint256)")[2:]

# Real address literal to embed (check _extract_literal_addresses keeps it)
VERIFIER = "0x83584f83f26af4edda9cbe8c730bc87c364b28fe"
LIT = bytes.fromhex(VERIFIER[2:])

def with_addr_pushes(code):
    out = bytearray(code)
    out += bytes([0x73]) + LIT   # PUSH20 verifier
    return bytes(out)

results = {}

# Case 1: verify-gated money path (should be MONEY_PATH_VERIFY_GATED)
code_viable = with_addr_pushes(build_dispatcher_for(V, W))
# overriding RPC to avoid network: subclass and return synthetic code
class FakeRPC:
    def __init__(self, code): self._code = code
    def get_code(self, addr, block="latest"):
        return "0x" + self._code.hex()

a = R.MoneyPathAnalyzer(rpc=FakeRPC(code_viable))
v = a.classify("0x0000000000000000000000000000000000000001", chain_id=1)
results["verify_gated_withdraw"] = v["verdict"]
print(f"[1] verify+withdraw dispatch   -> {v['verdict']}  (expect MONEY_PATH_VERIFY_GATED)")
print(f"    verifier cands: {v['literal_verifier_candidates']}")

# Case 2: plain-transfer withdraw (custom 0x06394c9b), no verify -> NO_MONEY_PATH
# the 0x06394c9b selector embedded as a plain PUSH4 (simulating the real pool)
CUSTOM = "06394c9b"
code_plain = build_dispatcher_for(CUSTOM, VERIFIER[2:])  # include a literal-ish
# also embed a text-blob that should be filtered
code_plain += bytes([0x73]) + b"NOTANADDRESS00" + b"xx"  # 20 bytes of printable ASCII
class FakeRPC2:
    def __init__(self, code): self._code = code
    def get_code(self, addr, block="latest"):
        return "0x" + self._code.hex()
a2 = R.MoneyPathAnalyzer(rpc=FakeRPC2(code_plain))
v2 = a2.classify("0x0000000000000000000000000000000000000002", chain_id=1)
results["plain_custom_withdraw"] = v2["verdict"]
print(f"[2] custom plain withdraw      -> {v2['verdict']}  (expect NO_MONEY_PATH)")
print(f"    verifier cands (ASCII blob should be filtered): {v2['literal_verifier_candidates']}")

# Case 3: verify only, no withdraw -> VERIFIER_ISOLATED
code_iso = build_dispatcher_for(V)
class FakeRPC3(FakeRPC):
    pass
a3 = R.MoneyPathAnalyzer(rpc=FakeRPC3(code_iso))
v3 = a3.classify("0x0000000000000000000000000000000000000003", chain_id=1)
results["verify_isolated"] = v3["verdict"]
print(f"[3] verify-only (library)      -> {v3['verdict']}  (expect VERIFIER_ISOLATED)")

ok = (results["verify_gated_withdraw"] == "MONEY_PATH_VERIFY_GATED"
      and results["plain_custom_withdraw"] == "NO_MONEY_PATH"
      and results["verify_isolated"] == "VERIFIER_ISOLATED")
print("\n" + ("ALL PASS -- gate distinguishes viable vs dead-end correctly"
              if ok else "FAIL -- mismatch above"))
sys.exit(0 if ok else 1)