# diag_case.py — one VK, one case, full revert reason + oracle traceback
import sys, traceback, json
sys.setrecursionlimit(1_000_000)
sys.path.insert(0, r"C:\Users\timot\OneDrive\Documents\VERITAS")
import pyecc_patch
from py_ecc.bn128 import G1, G2, FQ, FQ2, neg, multiply, add, pairing, FQ12
from core.rpc import RPC
from core.selectors import kec256
import random

FQ12_ONE = FQ12([1] + [0] * 11)
PROXY = "0xFA7093CDD9EE6932B4eb2c9e1cde7CE00B1FA4b9"
rpc = RPC("https://ethereum-rpc.publicnode.com")
def sel(sig): return "0x" + kec256(sig.encode()).hex()[:8]
def word(v): return f"{v:064x}"
GETVK = sel("getVerificationKey(uint256,uint256)")
VERIFYPROOF = sel("verifyProof(tuple,tuple,uint256[])")

def decode_vk(ret):
    b = ret[2:]
    w = [int(b[i:i+64], 16) for i in range(0, len(b), 64)]
    if len(w) < 17 or w[2] == 0: return None
    str_len_w = 1 + w[1] // 32
    slen = w[str_len_w]
    ipfs = bytes.fromhex(b[str_len_w*64+64 : str_len_w*64+64+slen*2]).decode(errors="replace")
    ic_len_w = 1 + w[16] // 32
    iclen = w[ic_len_w]
    pts = [(w[ic_len_w+1+2*k], w[ic_len_w+2*2*k//2]) for k in range(iclen)]
    pts = [(w[ic_len_w+1+2*k], w[ic_len_w+2+2*k]) for k in range(iclen)]
    return {"ipfs": ipfs, "alpha": (w[2], w[3]),
            "beta":  (w[4], w[5], w[6], w[7]),
            "gamma": (w[8], w[9], w[10], w[11]),
            "delta": (w[12], w[13], w[14], w[15]),
            "ic": pts}

def enc_g1(p): return word(p[0]) + word(p[1])
def enc_g2(p): return word(p[0]) + word(p[1]) + word(p[2]) + word(p[3])

def enc_vk(vk):
    head_words = 16
    sdata = vk["ipfs"].encode()
    str_off = head_words * 32
    str_words = 1 + (len(sdata) + 31) // 32
    ic_off = str_off + str_words * 32
    head = (word(str_off) + enc_g1(vk["alpha"]) + enc_g2(vk["beta"]) +
            enc_g2(vk["gamma"]) + enc_g2(vk["delta"]) + word(ic_off))
    sh = sdata.hex()
    sh += "0" * ((32 - len(sh) % 32) % 32)
    stail = word(len(sdata)) + sh
    ictail = word(len(vk["ic"])) + "".join(enc_g1(pt) for pt in vk["ic"])
    return head + stail + ictail

def call_verifyproof(vk, proof, inputs):
    proof_enc = enc_g1(proof[0]) + enc_g2(proof[1]) + enc_g1(proof[2])
    vk_enc = enc_vk(vk)
    off_vk = 10 * 32
    off_inputs = off_vk + len(vk_enc) // 2
    inputs_enc = word(len(inputs)) + "".join(word(v) for v in inputs)
    data = VERIFYPROOF + word(off_vk) + proof_enc + word(off_inputs) + vk_enc + inputs_enc
    return rpc.eth_call(PROXY, data)

# --- get VK (1,1)
ret = rpc.eth_call(PROXY, GETVK + word(1) + word(1))
vk = decode_vk(ret)
print("VK(1,1): ic =", len(vk["ic"]), "ipfs =", vk["ipfs"])
print("alpha:", hex(vk["alpha"][0])[:20], "...")
print("beta :", [hex(x)[:14] for x in vk["beta"]])
print("gamma:", [hex(x)[:14] for x in vk["gamma"]])
print("delta:", [hex(x)[:14] for x in vk["delta"]])

rng = random.Random(0xFA70)
def rand_g1():
    s = rng.randrange(1, 2**60)
    pt = multiply(G1, s)
    return (int(pt[0]), int(pt[1]))
def rand_g2():
    s = rng.randrange(1, 2**60)
    pt = multiply(G2, s)
    return (int(pt[0].coeffs[0]), int(pt[0].coeffs[1]),
            int(pt[1].coeffs[0]), int(pt[1].coeffs[1]))

proof = (rand_g1(), rand_g2(), rand_g1())
inputs = [rng.randrange(1, 2**60) for _ in range(4)]

# --- on-chain with RAW response (no exception swallowing)
def build_payload():
    return {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": PROXY, "data": call_verifyproof(vk, proof, inputs)}, "latest"]}
import urllib.request, json as J
req = urllib.request.Request(rpc.url, data=J.dumps(build_payload()).encode(),
                             headers={"Content-Type": "application/json", "User-Agent": rpc._ua})
with urllib.request.urlopen(req, timeout=30) as r:
    out = J.loads(r.read())
print("\nRAW eth_call response:")
print(J.dumps(out, indent=1)[:800])

# --- oracle with full traceback
def to_g1(x, y):
    if x == 0 and y == 0: return None
    return (FQ(x), FQ(y))
def to_g2(x0, x1, y0, y1):
    if x0 == 0 and x1 == 0 and y0 == 0 and y1 == 0: return None
    return (FQ2([x0, x1]), FQ2([y0, y1]))

try:
    A = to_g1(*proof[0]); B = to_g2(*proof[1]); C = to_g1(*proof[2])
    alpha = to_g1(*vk["alpha"]); beta = to_g2(*vk["beta"])
    gamma = to_g2(*vk["gamma"]); delta = to_g2(*vk["delta"])
    vkX = to_g1(*vk["ic"][0])
    for i, v in enumerate(inputs):
        pt = to_g1(*vk["ic"][i+1])
        term = multiply(pt, v) if pt is not None else None
        vkX = term if vkX is None else (add(vkX, term) if term is not None else vkX)
    negA = neg(A)
    t = pairing(B, negA) * pairing(beta, alpha) * pairing(gamma, vkX) * pairing(delta, C)
    print("\noracle result: prod == 1 ->", t == FQ12_ONE)
except Exception:
    print("\noracle EXCEPTION:")
    traceback.print_exc()
