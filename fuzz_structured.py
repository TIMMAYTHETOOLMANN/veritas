# fuzz_structured.py — differential campaign with STRUCTURED proofs (assembled via
# core_engine against the extracted VK) against 0xce172ce1 (verifyProof(bytes,uint256[6])).
# Local py_ecc oracle = ground truth. Any on-chain ACCEPT of a locally-invalid
# structured proof = CONFIRMED differential violation. $0: eth_call only.
import sys, os, random, time, json
sys.path.insert(0, os.path.join(os.getcwd(), "core"))
from core.rpc import RPC
from zk import extract as L1
from zk import witness as L2
from zk import core_engine as L3

rpc = RPC("https://ethereum-rpc.publicnode.com", timeout=25, retries=3)
VER = "0xce172ce1f20ec0b3728c9965470eaf994a03557a"
SEL = "0x695ef6f9"
CORPUS = 64
SEED = 0x5EED
DELAY = 0.25

code = rpc.get_code(VER)
vk = L1.extract_vk_from_bytecode(code)
assert vk, "VK extraction failed"
print(f"vk ok ic_count={vk['ic_count']} vk_hash={vk['vk_hash']}")

# The verifier takes 6 public inputs but the VK has 12 IC points. The circuit
# has 12 public signals; the pool passes 6 scalars + proof carries the rest?
# Actually verifyProof(bytes,uint256[6]) = Nova/Noir-style: proof bytes carry
# (A,B,C) points; 6 = (root, nullifier, recipient, relayer, fee, refund).
# ic_count=13 suggests 12 pubs inside the circuit. We probe both shapes.

def enc_bytes_u256x6(proof_bytes, pubs):
    head = "%064x" % 224  # bytes offset = 7 head words * 32
    head += "".join("%064x" % (p % 2**256) for p in pubs)
    return SEL + head + "%064x" % len(proof_bytes) + proof_bytes.hex()

EXEC_FAIL_MARKERS = ("revert", "invalid opcode", "out of gas", "stack",
                     "invalid memory", "static call", "invalidfeopcode",
                     "invalid instruction", "invalid jump")

def check(cd):
    try:
        ret = rpc.eth_call(VER, cd)
    except Exception as e:
        msg = str(e).lower()
        if any(m in msg for m in EXEC_FAIL_MARKERS):
            return "REVERTED", None  # EVM-level rejection: proof NOT accepted
        return "RPC_ERROR", str(e)[:100]
    body = (ret or "")[2:]
    if body == "" or set(body) == {"0"}:
        return "RETURNED_FALSE", (ret or "")[:10]
    return "ACCEPTED", (ret or "")[:10]

random.seed(SEED)
spec = {"n_inputs": 6, "unconstrained": [], "vk_hash": vk["vk_hash"]}
corpus = L2.generate_corpus(spec, seed=SEED, n=CORPUS)
print(f"corpus: {len(corpus)} entries")

sent = accepted = rejected = reverted = err = 0
confirmed_hits = []

for i, w in enumerate(corpus):
    if err >= 3:
        print("[abort] 3 RPC errors — fail-closed")
        break
    if "witness" not in w:
        continue
    proof = L3.assemble_proof(w["witness"], vk["vk_hash"])
    # pack proof ints into 128 bytes: 8 words
    pb = b"".join((v % 2**256).to_bytes(32, "big") for v in proof)
    pubs = w["witness"][:6]
    cd = enc_bytes_u256x6(pb, pubs)
    outcome, info = check(cd)
    sent += 1
    if outcome == "ACCEPTED":
        accepted += 1
        loc = None
        try:
            loc = L3.groth16_verify(vk, list(proof), list(pubs))
        except Exception:
            loc = None
        conf = (loc is False)
        if conf:
            confirmed_hits.append({"i": i, "class": w["class"], "ret": info})
        print(f"  [{i:03d}:{w['class']}] ACCEPTED ret={info} local={loc} confirmed={conf}")
    elif outcome == "REVERTED":
        reverted += 1
    elif outcome == "RETURNED_FALSE":
        rejected += 1
    else:
        err += 1
    time.sleep(DELAY)

print(f"\nsent={sent} accepted={accepted} rejected={rejected} reverted={reverted} rpc_errors={err}")
print(f"CONFIRMED differential violations: {len(confirmed_hits)}")
if confirmed_hits:
    print(json.dumps(confirmed_hits[:5], indent=2))
