# diag_oracle.py — replicate oracle_verify + ABI encode for ONE case, print raw errors
import sys, traceback
sys.setrecursionlimit(1_000_000)
import pyecc_patch
from py_ecc.bn128 import G1, G2, FQ, FQ2, neg, multiply, add, pairing, FQ12

FQ12_ONE = FQ12([1] + [0] * 11)

def is_inf_g1(p): return p is None or (int(p[0]) == 0 and int(p[1]) == 0)

def to_g1(x, y):
    if x == 0 and y == 0: return None
    return (FQ(x), FQ(y))

def to_g2(x0, x1, y0, y1):
    if x0 == 0 and x1 == 0 and y0 == 0 and y1 == 0: return None
    return (FQ2([x0, x1]), FQ2([y0, y1]))

# synthetic VK + proof, mirroring railgun_probe structures
s = 12345
alpha = multiply(G1, 7)
beta = multiply(G2, 11)
gamma = multiply(G2, 13)
delta = multiply(G2, 17)
ic = [multiply(G1, 19 + 4 * i) for i in range(5)]
A = multiply(G1, 999)
B = multiply(G2, 888)
C = multiply(G1, 777)

vk = {"alpha": (int(alpha[0]), int(alpha[1])),
      "beta": (int(B[0].coeffs[0]), int(B[0].coeffs[1]), int(B[1].coeffs[0]), int(B[1].coeffs[1])),
      "gamma": (int(gamma[0].coeffs[0]), int(gamma[0].coeffs[1]), int(gamma[1].coeffs[0]), int(gamma[1].coeffs[1])),
      "delta": (int(delta[0].coeffs[0]), int(delta[0].coeffs[1]), int(delta[1].coeffs[0]), int(delta[1].coeffs[1])),
      "ic": [(int(p[0]), int(p[1])) for p in ic]}

proof = ((int(A[0]), int(A[1])),
         (int(B[0].coeffs[0]), int(B[0].coeffs[1]), int(B[1].coeffs[0]), int(B[1].coeffs[1])),
         (int(C[0]), int(C[1])))
inputs = [5, 6, 7, 8]

# replicate oracle_verify step by step with traceback
try:
    A_ = to_g1(*proof[0]); B_ = to_g2(*proof[1]); C_ = to_g1(*proof[2])
    alpha_ = to_g1(*vk["alpha"]); beta_ = to_g2(*vk["beta"])
    gamma_ = to_g2(*vk["gamma"]); delta_ = to_g2(*vk["delta"])
    print("[1] point coercion OK")

    vkX = to_g1(*vk["ic"][0])
    for i, v in enumerate(inputs):
        pt = to_g1(*vk["ic"][i + 1])
        term = multiply(pt, v) if pt is not None else None
        vkX = term if vkX is None else (add(vkX, term) if term is not None else vkX)
    print("[2] vkX scalar-mul chain OK:", int(vkX[0]) % 1000)

    negA = neg(A_) if A_ is not None else None
    print("[3] neg(A) OK")

    t1 = pairing(B_, negA)
    print("[4] pairing(-A,B) OK")
    t2 = pairing(beta_, alpha_)
    print("[5] pairing(alpha,beta) OK")
    t3 = pairing(gamma_, vkX)
    print("[6] pairing(vkX,gamma) OK")
    t4 = pairing(delta_, C_)
    print("[7] pairing(C,delta) OK")

    prod = t1 * t2 * t3 * t4
    print("[8] product OK; == 1:", prod == FQ12_ONE)
except Exception:
    traceback.print_exc()
