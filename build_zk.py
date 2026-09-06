#!/usr/bin/env python3
"""
build_zk.py - One-time ZK artifact builder for VERITAS ShadowPath integration.

Builds:
  1. Compiles circom circuit to R1CS + WASM
  2. Groth16 trusted setup (powers of tau + circuit-specific zkey)
  3. Exports Solidity verifier contract

Usage:
    python3 build_zk.py

Expected outputs:
    zk_circuits/build/arb_proof.r1cs
    zk_circuits/build/arb_proof_js/arb_proof.wasm
    zk_circuits/build/arb_proof_0001.zkey
    zk_circuits/build/arb_proof_vkey.json
    contracts/Groth16Verifier.sol
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CIRCUITS_DIR = HERE / "zk_circuits"
BUILD_DIR = CIRCUITS_DIR / "build"
PTAU_DIR = HERE / "ptau"

CIRCUIT_NAME = "arb_proof"
PROVING_KEY = BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"
VERIFICATION_KEY = BUILD_DIR / f"{CIRCUIT_NAME}_vkey.json"
WASM_FILE = BUILD_DIR / f"{CIRCUIT_NAME}_js" / f"{CIRCUIT_NAME}.wasm"
VERIFIER_SOL = HERE / "contracts" / "Groth16Verifier.sol"


def find_circomlib():
    """Find circomlib installation path."""
    common_paths = [
        Path("/usr/local/lib/node_modules/circomlib"),
        Path("/usr/lib/node_modules/circomlib"),
        Path.home() / ".npm-global" / "lib" / "node_modules" / "circomlib",
        Path(os.environ.get("NODE_PATH", "")) / "circomlib",
    ]
    for p in common_paths:
        if p.exists():
            return str(p)
    return None


def run_cmd(cmd, **kwargs):
    """Run a command and print output."""
    print(f"[build] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, **kwargs)
    if result.returncode != 0:
        print(f"[build] STDERR: {result.stderr.decode()[:500]}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


def main():
    print("=" * 60)
    print("VERITAS ZK Artifact Builder (ShadowPath Pipeline)")
    print("=" * 60)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    PTAU_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Check for circomlib
    print("\n[1/5] Checking circomlib...")
    circomlib_path = find_circomlib()
    if not circomlib_path:
        print("[build] circomlib not found. Install with: npm install -g circomlib")
        sys.exit(1)
    print(f"[build] circomlib found: {circomlib_path}")

    # Step 2: Compile circom circuit
    print("\n[2/5] Compiling circuit...")
    r1cs_file = BUILD_DIR / f"{CIRCUIT_NAME}.r1cs"
    if not r1cs_file.exists():
        run_cmd([
            "circom", str(CIRCUITS_DIR / f"{CIRCUIT_NAME}.circom"),
            "--r1cs", "--wasm", "--sym", "-o", str(BUILD_DIR),
            "-l", circomlib_path
        ])
        print(f"[build] R1CS: {r1cs_file}")
        print(f"[build] WASM: {WASM_FILE}")
    else:
        print(f"[build] R1CS already exists, skipping compile")

    # Step 3: Powers of Tau ceremony
    print("\n[3/5] Powers of Tau...")
    ptau_file = PTAU_DIR / "pot14_final.ptau"
    if not ptau_file.exists():
        print("[build] Downloading ptau (this may take a moment)...")
        run_cmd([
            "wget", "-O", str(ptau_file),
            "https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_14.ptau"
        ])
    print(f"[build] ptau: {ptau_file}")

    # Step 4: Groth16 setup
    print("\n[4/5] Groth16 setup...")
    zkey_0 = BUILD_DIR / f"{CIRCUIT_NAME}_0000.zkey"
    if not PROVING_KEY.exists():
        run_cmd([
            "snarkjs", "groth16", "setup",
            str(r1cs_file), str(ptau_file), str(zkey_0)
        ])
        run_cmd([
            "snarkjs", "zkey", "contribute",
            str(zkey_0), str(PROVING_KEY),
            "--name", "VERITAS", "-v"
        ])
    print(f"[build] Proving key: {PROVING_KEY}")

    # Step 5: Export verification key and Solidity verifier
    print("\n[5/5] Exporting verifier...")
    if not VERIFICATION_KEY.exists():
        run_cmd([
            "snarkjs", "zkey", "export", "verificationkey",
            str(PROVING_KEY), str(VERIFICATION_KEY)
        ])
    print(f"[build] Verification key: {VERIFICATION_KEY}")

    if not VERIFIER_SOL.exists():
        run_cmd([
            "snarkjs", "zkey", "export", "solidityverifier",
            str(PROVING_KEY), str(VERIFIER_SOL)
        ])
    print(f"[build] Verifier contract: {VERIFIER_SOL}")

    print("\n" + "=" * 60)
    print("Build complete! Artifacts:")
    print(f"  R1CS: {r1cs_file}")
    print(f"  WASM: {WASM_FILE}")
    print(f"  Proving key: {PROVING_KEY}")
    print(f"  Verification key: {VERIFICATION_KEY}")
    print(f"  Verifier: {VERIFIER_SOL}")
    print("=" * 60)


if __name__ == "__main__":
    main()