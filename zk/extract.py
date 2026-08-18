# zk/extract.py — Layer 1: Extraction & Configuration
# Path A (on-chain): pull deployed bytecode, extract Groth16 Verifying Key
#                    (alpha, beta, gamma, delta, IC[]) from the constant pool.
# Path B (off-chain): parse .r1cs constraint matrices; flag under-constrained
#                    wires (outputs not feeding any constraint).
# Compute: near-zero (data scraping + parsing). $0: public RPC, eth_getCode only.
import json, os, re, struct, sys, time, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import conn, now
from core import rpc as rpc_mod

RPC_URL = "https://ethereum-rpc.publicnode.com"

# BN254 field/curve params (alt_bn128) — the only curve in EVM precompiles
P = 21888242871839275222246405745257275088696311157297823662689037894645226208583
R = 21888242871839275222246405745257275088548364400416034343698204186575808495617  # scalar field


# ----------------------------------------------------------------------------
# Path A: on-chain VK extraction from runtime bytecode
# ----------------------------------------------------------------------------

def _push32_limbs(code_hex):
    """All PUSH32 immediates in opcode stream order (heuristic scan).

    Walks the hex assuming roughly linear code; a misaligned hit only adds a
    garbage limb which later canonicality filters discard, so no disassembler
    is needed for this purpose.
    """
    code = code_hex[2:] if code_hex.startswith("0x") else code_hex
    limbs, i, n = [], 0, len(code)
    while i + 66 <= n:
        if code[i:i + 2] == "7f":
            limbs.append(code[i + 2:i + 66])
            i += 66
        else:
            i += 2
    return limbs


def _canonical(limb):
    """True if limb is a canonical BN254 base-field element (0 <= v < p)."""
    return len(limb) == 64 and int(limb, 16) < P and int(limb, 16) > 0


def extract_vk_from_bytecode(code_hex):
    """Extract candidate Groth16 VK parameters from EVM bytecode.

    Groth16 verifiers hardcode alpha (G1), beta/gamma/delta (G2) and IC[]
    (G1) as 32-byte PUSH32 immediates. Heuristic: keep canonical BN254
    limbs; consecutive quartets form G2 candidates, consecutive pairs form
    G1/IC candidates. Real verifiers (snarkjs output) show a dense run of
    10+ canonical limbs; ordinary contracts almost never do.

    Returns dict or None when no VK-like constant structure is found.
    """
    limbs = _push32_limbs(code_hex)
    if len(limbs) < 6:
        return None
    canon = [l for l in limbs if _canonical(l)]
    # A Groth16 verifier needs at minimum: alpha(x,y) + 3 G2s (4 limbs each)
    # + >=2 IC points (2 limbs each) = 18 canonical limbs. Require a dense
    # run to cut false positives from ordinary arithmetic constants.
    if len(canon) < 18:
        return None

    # G2 points: consecutive canonical quartets (x.c0,x.c1,y.c0,y.c1)
    g2s, i = [], 0
    while i + 4 <= len(canon):
        w = canon[i:i + 4]
        if w[0] != w[2] and w[1] != w[3]:
            g2s.append(w)
            i += 4
            continue
        i += 1

    # G1 points: consecutive canonical pairs (x,y), x != y
    g1s, i = [], 0
    while i + 2 <= len(canon):
        a, b = canon[i], canon[i + 1]
        if a != b:
            g1s.append((a, b))
            i += 2
            continue
        i += 1

    if len(g2s) < 3 or len(g1s) < 2:
        return None

    vk_hash = hashlib.sha256("|".join(canon[:64]).encode()).hexdigest()[:16]
    # alpha is a G1 point: needs BOTH limbs (x,y). Legacy field kept the x
    # limb alone which made local pairing checks impossible; alpha_pair is
    # canonical now, alpha kept for schema compat.
    alpha_pair = ["0x" + canon[0], "0x" + canon[1]] if len(canon) > 1 else None
    return {
        "curve": "bn254",
        "proof_system": "groth16",
        "vk_hash": vk_hash,
        "alpha": "0x" + canon[0],
        "alpha_pair": json.dumps(alpha_pair),
        "beta2": "0x" + "".join(g2s[0]),
        "gamma2": "0x" + "".join(g2s[1]),
        "delta2": "0x" + "".join(g2s[2]),
        "ic_count": len(g1s),
        "ic_points": json.dumps([["0x" + a, "0x" + b] for a, b in g1s[:32]]),
        "g1_point_count": len(g1s),
    }


def extract_vk_for_address(address, chain_id=1, rpc_url=None, persist=True):
    """Path A driver: bytecode -> VK -> vk_registry row. Returns VK dict or None."""
    rpc = rpc_mod.RPC(rpc_url or RPC_URL, timeout=25, retries=3)
    code = rpc.get_code(address.lower())
    if not code or code in ("0x", "0x0"):
        return None
    vk = extract_vk_from_bytecode(code)
    if vk and persist:
        c = conn()
        c.execute("""INSERT OR REPLACE INTO vk_registry
            (address, chain_id, curve, proof_system, vk_hash, alpha, alpha_pair,
             beta2, gamma2, delta2, ic_count, ic_points, g1_point_count,
             extracted_from, extracted_ts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (address.lower(), chain_id, vk["curve"], vk["proof_system"],
             vk["vk_hash"], vk["alpha"], vk.get("alpha_pair"), vk["beta2"],
             vk["gamma2"], vk["delta2"], vk["ic_count"], vk["ic_points"],
             vk["g1_point_count"], "bytecode_push32", now()))
        c.commit(); c.close()
    return vk


# ----------------------------------------------------------------------------
# Path B: off-chain .r1cs parsing + under-constraint analysis
# ----------------------------------------------------------------------------

def parse_r1cs(path):
    """Minimal R1CS (snarkjs format) parser -> config dict.

    Bin layout (post-magic 'r1cs' v1): n8, prime, n_wires, n_pub_out,
    n_pub_in, n_prvt, n_labels, n_constraints, then constraint matrices.
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"r1cs":
        raise ValueError("not an r1cs file")
    off = 4
    ver, n_sections = struct.unpack_from("<II", data, off); off += 8
    sections = []
    for _ in range(n_sections):
        st, sl = struct.unpack_from("<qQ", data, off); off += 16
        sections.append((st, sl))
    # section type 1 = header
    t1 = [s for s in sections if s[0] == 1]
    if not t1:
        raise ValueError("no header section")
    # header starts after all section headers
    hdr_off = 4 + 8 + 16 * n_sections + sum(s[1] for s in sections if s[0] != 1)
    # find offset of section 1 properly: sections are laid out in order after headers
    pos = 4 + 8 + 16 * n_sections
    hdr_off = None
    for st, sl in sections:
        if st == 1:
            hdr_off = pos
            break
        pos += sl
    off = hdr_off
    n8 = struct.unpack_from("<I", data, off)[0]; off += 4
    prime = int.from_bytes(data[off:off + n8], "little"); off += n8
    n_wires, n_pub_out, n_pub_in, n_prvt, n_labels, n_cons = \
        struct.unpack_from("<IIIIII", data, off); off += 24
    return {
        "fmt": "r1cs",
        "prime": prime,
        "n_wires": n_wires,
        "n_pub_out": n_pub_out,
        "n_pub_in": n_pub_in,
        "n_prvt": n_prvt,
        "n_labels": n_labels,
        "n_constraints": n_cons,
        "file": path,
    }


def find_under_constrained(config, r1cs_path=None, sample_max=200000):
    """Wires that appear in NO constraint (neither A,B nor C matrix).

    An output wire absent from every constraint is pure decoration: the
    prover can set it arbitrarily — the classic under-constrained-output
    defect. Needs the constraint section (type 2), so re-reads the file.
    """
    with open(r1cs_path or config["file"], "rb") as f:
        data = f.read()
    n_sections = struct.unpack_from("<I", data, 8)[0]
    pos = 4 + 8 + 16 * n_sections
    sec2 = None
    for _ in range(n_sections):
        st, sl = struct.unpack_from("<qQ", data, pos); pos += 16
        if st == 2:
            sec2 = (pos, sl)
            break
        pos += sl
    if sec2 is None:
        return []
    off, end = sec2
    n_cons = config["n_constraints"]
    # header said how many constraints; walk each constraint's 3 vectors
    used = set()
    for _ in range(n_cons):
        if off >= end:
            break
        for _mat in range(3):  # A, B, C
            n_vals = struct.unpack_from("<I", data, off)[0]; off += 4
            for _v in range(n_vals):
                wid = struct.unpack_from("<I", data, off)[0]; off += 4
                off += 32  # skip the value (big-int of n8 bytes assumed 32)
                used.add(wid)
    n_wires = config["n_wires"]
    return sorted(w for w in range(1, n_wires) if w not in used)


def parse_circuit_artifact(path, persist=True):
    """Path B driver: .r1cs -> circuit_configs row. Returns config dict or None."""
    try:
        config = parse_r1cs(path)
    except Exception as e:
        print(f"[extract] r1cs parse failed for {path}: {e}")
        return None
    try:
        uc = find_under_constrained(config, path)
    except Exception as e:
        print(f"[extract] under-constraint walk failed for {path}: {e}")
        uc = []
    config["under_constrained_wires"] = json.dumps(uc[:256])
    config["wire_stats"] = json.dumps({
        "n_wires": config["n_wires"], "n_constraints": config["n_constraints"],
        "n_pub_inputs": config["n_pub_in"], "n_labels": config["n_labels"],
        "n_under_constrained": len(uc)})
    if persist:
        c = conn()
        c.execute("""INSERT INTO circuit_configs
            (source, fmt, n_constraints, n_wires, n_pub_inputs, n_labels,
             under_constrained_wires, wire_stats, parsed_ts)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (path, config["fmt"], config["n_constraints"], config["n_wires"],
             config["n_pub_in"], config["n_labels"],
             config["under_constrained_wires"], config["wire_stats"], now()))
        c.commit(); c.close()
    return config


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", help="extract VK from deployed bytecode")
    ap.add_argument("--r1cs", help="parse off-chain .r1cs artifact")
    ap.add_argument("--rpc", default=RPC_URL)
    a = ap.parse_args()
    if a.address:
        vk = extract_vk_for_address(a.address, rpc_url=a.rpc)
        print(json.dumps(vk, indent=2) if vk else "no VK-like structure found")
    if a.r1cs:
        cfg = parse_circuit_artifact(a.r1cs)
        print(json.dumps(cfg, indent=2) if cfg else "parse failed")
