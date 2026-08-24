# diag_oracle2.py — pure local: decode VK(1,1) from a saved getVK result, run oracle with traceback
import sys, traceback, random
sys.setrecursionlimit(1_000_000)
sys.path.insert(0, r"C:\Users\timot\OneDrive\Documents\VERITAS")
import pyecc_patch
from py_ecc.bn128 import G1, G2, FQ, FQ2, neg, multiply, add, pairing, FQ12
from core.rpc import RPC
from core.selectors import kec256

FQ12_ONE = FQ12([1] + [0] * 11)
PROXY = "0xFA7093CDD9EE6932B4eb2c9e1cde7CE00B1FA4b9"
rpc = RPC("https://ethereum-rpc.publicnode.com")
def sel(sig): return "0x" + kec256(sig.encode()).hex()[:8]
def word(v): return f"{v:064x}"
GETVK = sel("getVerificationKey(uint256,uint256)")

ret = rpc.eth_call(PROXY, GETVK + word(1) + word(1))
b = ret[2:]
w = [int(b[i:i+64], 16) for i in range(0, len(b), 64)]
str_len_w = 1 + w[1] // 32
slen = w[str_len_w]
ipfs = bytes.fromhex(b[str_len_w*64+64 : str_len_w*64+64+slen*2]).decode(errors="replace")
ic_len_w = 1 + w[16] // 32
iclen = w[ic_len_w]
vk = {"ipfs": ipfs, "alpha": (w[2], w[3]),
      "beta":  (w[4], w[5], w[6], w[7]),
      "gamma": (w[8], w[9], w[10], w[11]),
      "delta": (w[12], w[13], w[14], w[15]),
      "ic": [(w[ic_len_w+1+2*k], w[ic_len_w+2+2*k]) for k in range(iclen)]}
print(f"VK(1,1) ic={iclen} ipfs={ipfs}")

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

def to_g1(x, y):
    if x == 0 and y == 0: return None
    return (FQ(x), FQ(y))
def to_g2(x0, x1, y0, y1):
    if x0 == 0 and x1 == 0 and y0 == 0 and y1 == 0: return None
    return (FQ2([x0, x1]), FQ2([y0, y1]))

proof = (rand_g1(), rand_g2(), rand_g1())
inputs = [rng.randrange(1, 2**60) for _ in range(4)]

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
    prod = pairing(B, negA) * pairing(beta, alpha) * pairing(gamma, vkX) * pairing(delta, C)
    print("oracle: prod == 1 ->", prod == FQ12_ONE)
except Exception:
    traceback.print_exc()
