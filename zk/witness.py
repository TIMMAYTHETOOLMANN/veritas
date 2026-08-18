# zk/witness.py — Layer 2: Adversarial Witness Generator
# Generates malformed witness vectors targeting the 5 exploit classes:
#   ZK-FIELD-OVERFLOW       boundary values at/around field modulus p
#   ZK-UNDER-CONSTRAINED    garbage witnesses for unconstrained wires
#   ZK-NULLIFIER-COLLISION  secret pairs targeting nullifier collisions
#   ZK-CONFIG-MISMATCH      proofs replayed across mismatched VKs
#   ZK-PROOF-MALLEABILITY   (A,B,C) point mutations (negation etc.)
# Compute: low — random gen + field math, no proving.
import os, sys, random, hashlib, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

P = 21888242871839275222246405745257275088696311157297823662689037894645226208583
R = 21888242871839275222246405745257275088548364400416034343698204186575808495617


def h256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def gen_field_overflow(n_inputs, rng):
    """Witness at the exact boundary of the field modulus (p-1, p-2, 2^k-1)."""
    boundaries = [P - 1, P - 2, P - 3, R - 1, R - 2,
                  2**64 - 1, 2**128 - 1, 2**250, 2**254 - 1, 0]
    out = []
    for i in range(n_inputs):
        v = rng.choice(boundaries) if i == 0 or rng.random() < 0.3 else rng.randrange(R)
        out.append(v % R)
    return {"class": "ZK-FIELD-OVERFLOW", "witness": out}


def gen_garbage(n_inputs, rng, unconstrained=None):
    """Pure garbage witness — anything the circuit still proves is a bug."""
    out = [rng.randrange(R) for _ in range(n_inputs)]
    w = {"class": "ZK-UNDER-CONSTRAINED", "witness": out}
    if unconstrained:
        # hammer the wires we know are unconstrained hardest
        for idx in unconstrained[:16]:
            if 0 <= idx < len(out):
                out[idx] = rng.choice([P - 1, R - 1, 0, 1, rng.randrange(R)])
    return w


def gen_nullifier_collision(rng):
    """Pair of near-identical secrets to hunt nullifier collisions."""
    a = rng.randrange(1, R)
    delta = rng.choice([1, 2, 3, 7, 2**32, 2**64, -1 % R])
    b = (a + delta) % R
    return {"class": "ZK-NULLIFIER-COLLISION",
            "witness": [a, b], "delta": delta}


def gen_config_mismatch(vk_a, vk_b, rng):
    """Proof replay across circuits — the (A,B,C) stays, the binding changes."""
    return {"class": "ZK-VERIFIER-CONFIG-MISMATCH",
            "vk_a": vk_a.get("vk_hash") if isinstance(vk_a, dict) else vk_a,
            "vk_b": vk_b.get("vk_hash") if isinstance(vk_b, dict) else vk_b,
            "witness": [rng.randrange(R) for _ in range(4)]}


def gen_malleability(proof_pi=None):
    """Mutate a valid proof (A,B,C): negate A, swap B coordinates, etc."""
    if proof_pi is None:
        proof_pi = [rng_default().randrange(R) for _ in range(8)]
    mutations = {
        "neg_a": True,       # A -> -A  (classic Groth16 malleability)
        "neg_c": True,       # C -> -C
        "swap_b": True,      # B G2 coordinate swap
    }
    return {"class": "ZK-PROOF-MALLEABILITY", "proof": proof_pi,
            "mutations": mutations}


def rng_default():
    return random.Random(0x5EED)


# ----------------------------------------------------------------------------
# Corpus generation — deterministic per-seed campaign
# ----------------------------------------------------------------------------

def generate_corpus(spec, seed=0x5EED, n=256):
    """Build a witness corpus of size n for a circuit spec dict.

    spec: {"n_inputs": int, "unconstrained": [wire idx], "vk_hash": str}
    Returns list of witness dicts (class, witness).
    """
    rng = random.Random(seed)
    n_in = spec.get("n_inputs", 8)
    unc = spec.get("unconstrained") or []
    corpus = []
    per_class = max(1, n // 4)
    for _ in range(per_class):
        corpus.append(gen_field_overflow(n_in, rng))
    for _ in range(per_class):
        corpus.append(gen_garbage(n_in, rng, unc))
    for _ in range(per_class // 4 or 1):
        corpus.append(gen_nullifier_collision(rng))
    if spec.get("vk_hash"):
        for _ in range(per_class // 4 or 1):
            corpus.append(gen_config_mismatch(spec, spec, rng))
        corpus.append(gen_malleability([rng.randrange(R) for _ in range(8)]))
    return corpus


if __name__ == "__main__":
    spec = {"n_inputs": 8, "unconstrained": [3, 7], "vk_hash": "abc123"}
    corpus = generate_corpus(spec, n=32)
    byc = {}
    for w in corpus:
        byc[w["class"]] = byc.get(w["class"], 0) + 1
    print(json.dumps(byc, indent=2))
    print(f"total={len(corpus)}  sample={json.dumps(corpus[0]['witness'][:4])}")
