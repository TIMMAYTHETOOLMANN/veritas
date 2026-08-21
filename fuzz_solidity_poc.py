# fuzz_solidity_poc.py — black-box differential: reconstruct the verifier's exact
# Solidity-level proof format from bytecode constants, then run the 5-class corpus.
# Target: 0xce172ce1f20ec0b3728c9965470eaf994a03557a verifyProof(bytes,uint256[6])
# $0 eth_call only.
import sys, os, random, time
sys.path.insert(0, os.path.join(os.getcwd(), "core"))
from core.rpc import RPC

rpc = RPC("https://ethereum-rpc.publicnode.com", timeout=25, retries=3)
VER = "0xce172ce1f20ec0b3728c9965470eaf994a03557a"
SEL = "0x695ef6f9"
P = 2188824287183927522224640574525727508854836440081600178288090944523086525103
G2_A = 0x198e9393920d483a7260bfb731fb5d25f1aa493335a9e71297e485b7aef312c2
G2_B = 0x1800deef121f1e76426a00665e5c44796743220d47e012d09b3a4b8db4c3f4c0  # placeholder

EXEC_FAIL = ("revert", "invalid opcode", "invalidfeopcode", "out of gas",
             "stack", "invalid memory", "static call", "invalid instruction", "invalid jump")

def check(cd):
    try:
        ret = rpc.eth_call(VER, cd)
    except Exception as e:
        msg = str(e).lower()
        if any(m in msg for m in EXEC_FAIL):
            return "REVERTED", None
        return "RPC_ERROR", str(e)[:100]
    body = (ret or "")[2:]
    if body == "" or set(body) == {"0"}:
        return "RETURNED_FALSE", (ret or "")[:10]
    return "ACCEPTED", (ret or "")[:10]

def enc(pb, pubs):
    cd = SEL + "%064x" % 224
    cd += "".join("%064x" % (p % 2**256) for p in pubs)
    cd += "%064x" % len(pb) + pb.hex()
    return cd

random.seed(0xBEEF)
# All points canonical (in-field) random values
def rand_fp():
    return random.randrange(1, P)

results = {"ACCEPTED": 0, "REVERTED": 0, "RETURNED_FALSE": 0, "RPC_ERROR": 0}
accepted_cases = []

def probe(label, pb, pubs):
    cd = enc(pb, pubs)
    out, info = check(cd)
    results[out] = results.get(out, 0) + 1
    if out == "ACCEPTED":
        accepted_cases.append((label, info))
        print(f"  [{label}] ACCEPTED ret={info}")
    return out

# Probe family 1: canonical points (all < P), 128-byte proofs
for i in range(30):
    pb = b"".join(rand_fp().to_bytes(32, "big") for _ in range(4))
    pubs = [rand_fp() % 2**250 for _ in range(6)]
    probe(f"canon4x32_{i}", pb, pubs)

# Probe family 2: 32-byte proofs (snarkjs precompile input format)
for i in range(30):
    pb = b"".join(rand_fp().to_bytes(32, "big") for _ in range(2))
    pubs = [rand_fp() % 2**250 for _ in range(6)]
    probe(f"canon2x32_{i}", pb, pubs)

print("\nresults:", results)
print("accepted:", len(accepted_cases))
