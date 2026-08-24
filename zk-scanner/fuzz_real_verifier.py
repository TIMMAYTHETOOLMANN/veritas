# fuzz_real_verifier.py — differential fuzz of 0xce172ce1 (verifyProof(bytes,uint256[6]))
# $0: eth_call only. Local py_ecc oracle = ground truth.
import sys, os, random
sys.path.insert(0, os.path.join(os.getcwd(), "core"))
from core.rpc import RPC
from zk import extract as L1
from zk import witness as L2
from zk import core_engine as L3

rpc = RPC("https://ethereum-rpc.publicnode.com", timeout=25, retries=3)
VER = "0xce172ce1f20ec0b3728c9965470eaf994a03557a"
SEL = "0x695ef6f9"
P = 2188824287183927522224640574525727508854836440081600178288090944523086525103

code = rpc.get_code(VER)
vk = L1.extract_vk_from_bytecode(code)
print(f"vk extracted: {bool(vk)}, ic_count={vk['ic_count'] if vk else '-'}")

def enc(proof_bytes, pubs):
    # verifyProof(bytes proof, uint256[6] pub): 7 head words -> bytes offset 0xe0
    head = "00000000000000000000000000000000000000000000000000000000000000e0"
    head += "".join("%064x" % (p % 2**256) for p in pubs)
    tail = "%064x" % len(proof_bytes) + proof_bytes.hex()
    return SEL + head + tail

def check(cd):
    try:
        ret = rpc.eth_call(VER, cd)
    except Exception as e:
        if "revert" in str(e).lower():
            return "REVERTED", None
        return "RPC_ERROR", str(e)[:100]
    body = (ret or "")[2:]
    if body == "" or set(body) == {"0"}:
        return "RETURNED_FALSE", (ret or "")[:10]
    return "ACCEPTED", (ret or "")[:10]

def local_oracle(proof_bytes, pubs):
    """Ground truth via py_ecc against the extracted VK."""
    if not vk:
        return None
    words = [int.from_bytes(proof_bytes[i:i+32], "big") for i in range(0, len(proof_bytes), 32)]
    if len(words) < 8:
        return False
    proof = words[0:8]  # a.x a.y b.x.c0 b.x.c1 b.y.c0 b.y.c1 c.x c.y
    try:
        return L3.groth16_verify(vk, proof, list(pubs))
    except Exception:
        return None

sent = accepted = rejected = reverted = err = 0
hits = []

def probe(label, vclass, proof_bytes, pubs):
    global sent, accepted, rejected, reverted, err
    cd = enc(proof_bytes, pubs)
    outcome, info = check(cd)
    sent += 1
    if outcome == "RPC_ERROR":
        err += 1
        print(f"  [{label}] {vclass}: RPC_ERROR {info}")
        return
    if outcome == "ACCEPTED":
        accepted += 1
        loc = local_oracle(proof_bytes, pubs)
        confirmed = (loc is False)
        hits.append({"label": label, "class": vclass, "ret": info,
                     "local_oracle_valid": loc, "confirmed": confirmed})
        print(f"  [{label}] {vclass}: ACCEPTED ret={info} local_oracle={loc} CONFIRMED={confirmed}")
    else:
        if outcome == "REVERTED":
            reverted += 1
        else:
            rejected += 1
        print(f"  [{label}] {vclass}: {outcome}")

Z8 = b"\x00" * 8
# 1. all-zero proof, zero pubs
probe("zero", "ZK-FIELD-OVERFLOW", b"\x00" * 128, [0] * 6)
# 2. all-0xff (every limb >= p)
probe("ones", "ZK-FIELD-OVERFLOW", b"\xff" * 128, [P] * 6)
# 3. p-1 exact boundary
probe("p_minus_1", "ZK-FIELD-OVERFLOW", b"\x00" * 128, [P - 1] * 6)
# 4. non-canonical G1 point: A.x = p
pb = bytearray(b"\x11" * 128)
pb[0:32] = P.to_bytes(32, "big")
probe("noncanonA", "ZK-FIELD-OVERFLOW", bytes(pb), [0] * 6)
# 5. non-canonical B limbs
pb = bytearray(b"\x22" * 128)
pb[32:64] = P.to_bytes(32, "big")
probe("noncanonB", "ZK-FIELD-OVERFLOW", bytes(pb), [0] * 6)
# 6. malleability: negate C.y (Groth16 (A,B,-C) is valid iff (A,B,C) is)
pb = bytearray(b"\x33" * 128)
cy = int.from_bytes(pb[96:128], "big")
pb[96:128] = ((P - cy) % P).to_bytes(32, "big")
probe("negC", "ZK-PROOF-MALLEABILITY", bytes(pb), [0] * 6)
# 7. empty proof
probe("empty", "ZK-VERIFIER-CONFIG-MISMATCH", b"", [0] * 6)
# 8. short proof (32B)
probe("short", "ZK-VERIFIER-CONFIG-MISMATCH", b"\x44" * 32, [0] * 6)
# 9. long proof (256B)
probe("long", "ZK-VERIFIER-CONFIG-MISMATCH", b"\x55" * 256, [0] * 6)
# 10. valid-shaped random proof, random pubs
random.seed(0xC0FFEE)
rb = bytes(random.getrandbits(8) for _ in range(128))
probe("rand128", "ZK-UNDER-CONSTRAINED", rb, [random.getrandbits(250) for _ in range(6)])

print(f"\nsent={sent} accepted={accepted} rejected={rejected} reverted={reverted} rpc_errors={err}")
print(f"hits={len(hits)} confirmed={sum(1 for h in hits if h['confirmed'])}")
