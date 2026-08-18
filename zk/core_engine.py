# zk/core_engine.py — Layer 3: Icicle-style GPU Compute Core (BN254)
# Native backend: py_ecc bn128 (pure-Python) — correct, laptop-class.
# CUDA backend slot: icicle-cuda — drops in behind the same interface.
#
# Targets the installed py_ecc API: affine 2-tuples (x, y), G1 = (1, 2),
# Z1 = None, neg(pt) = (x, -y), pairing(Q_g2, P_g1).
import os, sys, json, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

P = 21888242871839275222246405745257275088696311157297823662689037894645226208583
R = 21888242871839275222246405745257275088548364400416034343698204186575808495617

_BACKEND = None

def detect_backend():
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        import icicle  # noqa
        _BACKEND = "icicle-cuda"
    except ImportError:
        try:
            from py_ecc import bn128  # noqa
            _BACKEND = "py_ecc-cpu"
        except ImportError:
            _BACKEND = "none"
    return _BACKEND


# ----------------------------------------------------------------------------
# Field arithmetic (scalar field R, plain ints)
# ----------------------------------------------------------------------------

def f_add(a, b):
    return (a + b) % R

def f_sub(a, b):
    return (a - b) % R

def f_mul(a, b):
    return (a * b) % R

def f_inv(a):
    return pow(a, R - 2, R)

def f_pow(a, e):
    return pow(a, e, R)


# ----------------------------------------------------------------------------
# NTT — iterative Cooley-Tukey over scalar field R (2-adicity 28)
# ----------------------------------------------------------------------------

def _find_root_of_unity(order):
    exp = (R - 1) // order
    for g in range(2, 200):
        w = pow(g, exp, R)
        if pow(w, order // 2, R) != 1:
            return w
    raise ValueError("no root of unity found for order %d" % order)

_ROOTS = {}

def ntt(a, invert=False):
    """Iterative CT NTT over F_R. len(a) must be a power of two."""
    n = len(a)
    if n & (n - 1):
        raise ValueError("NTT size must be power of 2")
    if n == 1:
        return a[:]
    if n not in _ROOTS:
        _ROOTS[n] = _find_root_of_unity(n)
    w_n = _ROOTS[n]
    if invert:
        w_n = f_inv(w_n)
    a = a[:]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        w_l = pow(w_n, n // length, R)
        for start in range(0, n, length):
            w = 1
            half = length // 2
            for k in range(half):
                u = a[start + k]
                v = f_mul(a[start + k + half], w)
                a[start + k] = f_add(u, v)
                a[start + k + half] = f_sub(u, v)
                w = f_mul(w, w_l)
        length <<= 1
    if invert:
        n_inv = f_inv(n)
        a = [f_mul(x, n_inv) for x in a]
    return a


# ----------------------------------------------------------------------------
# Point coercion — installed py_ecc dialect: affine 2-tuples.
# ----------------------------------------------------------------------------

def _g1(x, y):
    from py_ecc.bn128 import FQ
    if x >= P or y >= P:
        raise ValueError("non-canonical G1 input")
    return (FQ(x), FQ(y))

def _g2(c0x, c1x, c0y, c1y):
    from py_ecc.bn128 import FQ2
    return (FQ2([c0x % P, c1x % P]), FQ2([c0y % P, c1y % P]))

def hex_g1(hex64x, hex64y):
    return _g1(int(hex64x, 16), int(hex64y, 16))

def hex_g2(hex128):
    """256-hex-char G2 point: x.c0|x.c1|y.c0|y.c1 (64 hex chars each)."""
    h = hex128[2:] if hex128.startswith("0x") else hex128
    return _g2(int(h[0:64], 16), int(h[64:128], 16),
               int(h[128:192], 16), int(h[192:256], 16))


def is_canonical_g1(pt):
    """Canonicality + on-curve + subgroup check for a G1 point (2-tuple)."""
    from py_ecc.bn128 import is_on_curve, multiply, is_inf
    from py_ecc.bn128.bn128_curve import b as B1
    try:
        if pt is None or is_inf(pt):
            return False
        if not is_on_curve(pt, B1):
            return False
        return is_inf(multiply(pt, R))
    except Exception:
        return False


# ----------------------------------------------------------------------------
# MSM — batch multi-scalar multiplication
# ----------------------------------------------------------------------------

def msm(points, scalars):
    """Batch G1 MSM. points: 2-tuples (FQ or int), scalars: int."""
    from py_ecc.bn128 import add, multiply, FQ
    acc = None
    for pt, sc in zip(points, scalars):
        if pt is None or sc % R == 0:
            continue
        if isinstance(pt, tuple) and len(pt) == 2 and not isinstance(pt[0], FQ):
            pt = _g1(pt[0], pt[1])
        term = multiply(pt, sc % R)
        acc = term if acc is None else add(acc, term)
    return acc


def neg_g1(pt):
    from py_ecc.bn128 import neg, FQ
    if pt is None:
        return None
    if isinstance(pt[0], FQ):
        return neg(pt)
    return (pt[0], (-pt[1]) % P)


# ----------------------------------------------------------------------------
# Groth16 full verification — pairing product:
#   e(alpha, beta) * e(-A, B) * e(-C, delta) * e(-IC*pub, gamma) == 1
# ----------------------------------------------------------------------------

def groth16_verify(vk, proof, pub_inputs):
    """Full native Groth16 verification. Returns True/False, never raises."""
    try:
        from py_ecc.bn128 import (pairing, final_exponentiate, multiply,
                                  add, neg, FQ2)
        a_g1 = _g1(proof[0], proof[1])
        b_g2 = _g2(proof[2], proof[3], proof[4], proof[5])
        c_g1 = _g1(proof[6], proof[7])

        ap = vk.get("alpha_pair")
        if isinstance(ap, str):
            ap = json.loads(ap)
        if not ap:
            return False
        alpha = hex_g1(ap[0], ap[1])
        beta = hex_g2(vk["beta2"])
        gamma = hex_g2(vk["gamma2"])
        delta = hex_g2(vk["delta2"])

        ic = vk.get("ic_points") or []
        if isinstance(ic, str):
            ic = json.loads(ic)
        ic_pts = [hex_g1(x[0], x[1]) for x in ic]
        pv = [1] + [int(x) % R for x in pub_inputs]
        if len(pv) > len(ic_pts):
            return False

        ic_pub = None
        for pt, s in zip(ic_pts, pv):
            term = multiply(pt, s)
            ic_pub = term if ic_pub is None else add(ic_pub, term)
        if ic_pub is None:
            return False

        prod = (pairing(beta, alpha)
                * pairing(b_g2, neg(a_g1))
                * pairing(delta, neg(c_g1))
                * pairing(gamma, neg(ic_pub)))
        return final_exponentiate(prod) == FQ2.one()
    except Exception:
        return False


# ----------------------------------------------------------------------------
# Differential proving path — structured candidate proofs from witnesses.
# Hash-to-curve (try-and-increment): canonical, on-curve, in-subgroup G1
# points derived deterministically from the witness. A sound verifier rejects
# these; the differential question is whether the REAL on-chain one does.
# ----------------------------------------------------------------------------

def _hash_to_g1(seed_int, attempt=0):
    seed = seed_int ^ (attempt * 0x9E3779B97F4A7C15)
    for k in range(256):
        h = hashlib.sha256(seed.to_bytes(32, "big") + k.to_bytes(1, "big")).digest()
        x = int.from_bytes(h, "big") % P
        y2 = (pow(x, 3, P) + 3) % P
        y = pow(y2, (P + 1) // 4, P)
        if pow(y, 2, P) == y2:
            return x, (P - y) % P
    raise RuntimeError("hash_to_g1 failed")


def assemble_proof(witness, vk_hash=""):
    """Deterministic structured proof from a witness vector.

    Returns [a_x, a_y, b_x_c0, b_x_c1, b_y_c0, b_y_c1, c_x, c_y] ints —
    the shape a snarkjs Groth16 proof unpacks to. A and C are real curve
    points; B is carried as field elements (G2 membership is not needed for
    the differential screen — the on-chain verifier is the ground truth).
    """
    seed_material = json.dumps([str(w) for w in witness], separators=(",", ":")) + vk_hash
    base = int.from_bytes(hashlib.sha256(seed_material.encode()).digest(), "big")
    ax, ay = _hash_to_g1(base, 0)
    cx, cy = _hash_to_g1(base, 2)
    h2 = int.from_bytes(hashlib.sha256((seed_material + "|g2").encode()).digest(), "big")
    return [ax, ay,
            (base >> 0) % P, (base >> 64) % P, (base >> 128) % P, (base >> 192) % P,
            cx, cy]


def mutate_proof(proof, mutation):
    """Apply a malleability mutation to an (A,B,C) proof. Returns new list."""
    pi = list(proof)
    if mutation == "neg_a":
        pi[1] = (-pi[1]) % P
    elif mutation == "neg_c":
        pi[7] = (-pi[7]) % P
    elif mutation == "neg_both":
        pi[1] = (-pi[1]) % P
        pi[7] = (-pi[7]) % P
    elif mutation == "swap_b":
        pi[2], pi[4] = pi[4], pi[2]
        pi[3], pi[5] = pi[5], pi[3]
    elif mutation == "identity":
        pass
    else:
        raise ValueError("unknown mutation %r" % mutation)
    return pi


def malleability_family(proof):
    """All mutations worth testing, with expected verify outcomes."""
    return [
        ("neg_both", mutate_proof(proof, "neg_both"), "valid_if_malleable"),
        ("neg_a", mutate_proof(proof, "neg_a"), "reject_expected"),
        ("neg_c", mutate_proof(proof, "neg_c"), "reject_expected"),
        ("swap_b", mutate_proof(proof, "swap_b"), "reject_expected"),
    ]


# ----------------------------------------------------------------------------
# CRS / trusted-setup structural audit
# ----------------------------------------------------------------------------

def audit_vk_points(vk):
    """Canonicality + on-curve checks for every extracted VK point."""
    from py_ecc.bn128 import is_on_curve
    from py_ecc.bn128.bn128_curve import b as B1, b2 as B2
    issues = []
    try:
        ap = vk.get("alpha_pair")
        if isinstance(ap, str):
            ap = json.loads(ap)
        if ap:
            x, y = int(ap[0], 16), int(ap[1], 16)
            if x >= P or y >= P:
                issues.append({"point": "alpha", "issue": "non-canonical"})
            elif not is_on_curve(_g1(x, y), B1):
                issues.append({"point": "alpha", "issue": "off_curve"})
        for name in ("beta2", "gamma2", "delta2"):
            h = vk.get(name)
            if not h:
                continue
            raw = h[2:] if h.startswith("0x") else h
            if len(raw) < 256:
                issues.append({"point": name, "issue": "truncated"})
                continue
            limbs = [int(raw[i:i + 64], 16) for i in range(0, 256, 64)]
            if any(l >= P for l in limbs):
                issues.append({"point": name, "issue": "non-canonical"})
                continue
            try:
                pt = hex_g2(raw)
                if not is_on_curve(pt, B2):
                    issues.append({"point": name, "issue": "off_curve"})
            except Exception:
                issues.append({"point": name, "issue": "unparseable"})
        ic = vk.get("ic_points") or []
        if isinstance(ic, str):
            ic = json.loads(ic)
        for i, pair in enumerate(ic):
            x, y = int(pair[0], 16), int(pair[1], 16)
            if x >= P or y >= P:
                issues.append({"point": "ic[%d]" % i, "issue": "non-canonical"})
            elif not is_on_curve(_g1(x, y), B1):
                issues.append({"point": "ic[%d]" % i, "issue": "off_curve"})
    except Exception as e:
        issues.append({"point": "audit", "issue": "error: %s" % e})
    return issues


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    print("backend=%s" % detect_backend())
    a = [i * 7 + 3 for i in range(64)]
    assert ntt(ntt(a), invert=True) == [x % R for x in a], "NTT round-trip failed"
    print("NTT round-trip OK (n=64)")
    p1 = assemble_proof([1, 2, 3], "deadbeef")
    p2 = assemble_proof([1, 2, 3], "deadbeef")
    p3 = assemble_proof([1, 2, 4], "deadbeef")
    assert p1 == p2 and p1 != p3
    print("proof assembly OK: a_x=%s..." % hex(p1[0])[:18])
    m = mutate_proof(p1, "neg_a")
    assert m[1] == (-p1[1]) % P
    mb = mutate_proof(p1, "neg_both")
    assert mb[1] == (-p1[1]) % P and mb[7] == (-p1[7]) % P
    print("malleability mutations OK")
    from py_ecc.bn128 import is_on_curve, multiply, is_inf
    from py_ecc.bn128.bn128_curve import b as B1
    A = _g1(p1[0], p1[1])
    C = _g1(p1[6], p1[7])
    assert is_on_curve(A, B1) and is_on_curve(C, B1), "assembled points not on curve"
    assert is_inf(multiply(A, R)), "A not in subgroup"
    assert is_inf(multiply(C, R)), "C not in subgroup"
    print("A/C on curve + in subgroup OK")
    # malformed-proof screen must never raise
    assert groth16_verify({}, [1, 2, 3], []) is False
    assert groth16_verify({"alpha_pair": None}, [1, 2, 3, 4, 5, 6, 7, 8], []) is False
    print("malformed vk screen OK (returns False, no raise)")
    print("core_engine self-test PASSED")
