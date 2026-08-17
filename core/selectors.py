# core/selectors.py — keccak selectors, template fingerprints, PUSH4 scan
from Crypto.Hash import keccak

def kec256(b: bytes) -> bytes:
    k = keccak.new(digest_bits=256); k.update(b); return k.digest()

def selector(sig: str) -> str:
    return "0x" + kec256(sig.encode()).hex()[:8]

# Canonical signature corpus (extend freely — this is the T0 fingerprint DB)
SIGS = {
    "deposit":  "deposit(bytes32)",
    "withdraw": "withdraw(uint256[2],uint256[2][2],uint256[2],uint256[2],bytes32,address,uint256,uint256)",
    "verify":   "verifyProof(uint256[2],uint256[2][2],uint256[2],uint256[2])",
    "getroot":  "getLastRoot()",
    "nullif":   "nullifierHashes(bytes32)",
    "roots":    "roots(uint32)",
    "denom":    "denomination()",
    "token":    "token()",
    "levels":   "levels()",
    "setver":   "setVerifier(address)",
    "updatever":"updateVerifier(address)",
    "ecrecover_like": "recover(address,uint256,uint256,bytes32,bytes32,uint8)",
}

TEMPLATES = {
    # Tornado-family mixer: hardcoded VK, nullifier mapping, merkle roots
    "tornado_v2": ["deposit", "withdraw", "verify", "getroot", "nullif", "roots", "denom", "levels"],
    # Upgradable-verifier family (the dangerous config class)
    "zk_upgradable": ["withdraw", "verify", "setver"],
}

def selectors_map():
    return {k: selector(v) for k, v in SIGS.items()}

def scan_code(code_hex: str):
    """PUSH4 (0x63) scan of runtime bytecode -> set of present selectors."""
    code = code_hex[2:] if code_hex.startswith("0x") else code_hex
    sm = selectors_map()
    present = {}
    for name, sel in sm.items():
        needle = "63" + sel[2:]
        present[name] = code.count(needle) > 0
    return present

def match_template(present: dict):
    best_id, best_sim = None, 0.0
    for tid, keys in TEMPLATES.items():
        hit = sum(1 for k in keys if present.get(k))
        sim = hit / len(keys)
        if sim > best_sim:
            best_id, best_sim = tid, sim
    return best_id, round(best_sim, 3)
