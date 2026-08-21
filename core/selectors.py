# core/selectors.py — keccak selectors, template fingerprints, PUSH4 scan
from Crypto.Hash import keccak

def kec256(b: bytes) -> bytes:
    k = keccak.new(digest_bits=256); k.update(b); return k.digest()

def selector(sig: str) -> str:
    return "0x" + kec256(sig.encode()).hex()[:8]

# Canonical signature corpus (extend freely — this is the T0 fingerprint DB)
SIGS = {
    # Tornado family
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
    # Tornado-CLONE family (verified 2026-08-20 via 4byte + fork probes).
    # These pools do NOT use the canonical tornado selectors — the money path
    # routes through these custom sigs: withdraw -> verifyProof on a
    # storage-resolved verifier (verifier() getter).
    "clone_withdraw":  "withdraw(bytes,bytes32,bytes32,address,address,uint256,uint256)",
    "clone_verify":    "verifyProof(bytes,uint256[6])",
    "clone_operator":  "operator()",
    "clone_changeop":  "changeOperator(address)",
    "clone_verifier":  "verifier()",
    "clone_deposit":   "deposit(bytes32)",
    "clone_hasher":    "hasher()",
    # Railgun family (broadcaster + shield)
    "railgun_deposit":    "Deposit(bytes32,uint256,address)",
    "railgun_withdraw":   "Withdraw(bytes32,uint256,address,bytes)",
    "railgun_verify":     "verifyProof(bytes,bytes32)",
    "railgun_broadcast":  "broadcast(bytes,bytes32)",
    # Aztec family (private execution)
    "aztec_deposit":      "Deposit(bytes32,uint256,address)",
    "aztec_withdraw":     "Withdraw(bytes32,uint256,address)",
    "aztec_verify":       "verifyProof(bytes32,bytes)",
    # Generic ZK rollup / privacy pool patterns
    "zk_deposit":         "Deposit(bytes32,uint256)",
    "zk_withdraw":        "Withdraw(bytes32,uint256,address)",
    "zk_verify":          "verifyProof(bytes,bytes32)",
    # Upgradable verifier pattern
    "upgrade_setver":     "setVerifier(address)",
    "upgrade_updatever":  "updateVerifier(address)",
    "upgrade_verify":     "verifyProof(uint256[2],uint256[2][2],uint256[2],uint256[2])",
}

TEMPLATES = {
    # Tornado-family mixer: hardcoded VK, nullifier mapping, merkle roots
    "tornado_v2": ["deposit", "withdraw", "verify", "getroot", "nullif", "roots", "denom", "levels"],
    # Upgradable-verifier family (the dangerous config class)
    "zk_upgradable": ["withdraw", "verify", "setver", "updatever"],
    # Railgun family
    "railgun": ["railgun_deposit", "railgun_withdraw", "railgun_verify", "railgun_broadcast"],
    # Aztec family
    "aztec": ["aztec_deposit", "aztec_withdraw", "aztec_verify"],
    # Generic ZK privacy pool (deposit + withdraw + verify gate)
    "zk_pool": ["zk_deposit", "zk_withdraw", "zk_verify"],
    # Upgradable verifier pattern (setVerifier + updateVerifier + verify)
    "upgradable_verifier": ["upgrade_setver", "upgrade_updatever", "upgrade_verify"],
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
