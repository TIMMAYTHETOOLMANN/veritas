#!/usr/bin/env python3
"""
build_zk.py — One-time ZK artifact builder for VERITAS ShadowPath integration.
Compiles circom circuit, generates Groth16 keys, exports Solidity verifier.
Run once: python3 build_zk.py
"""

import json
import subprocess
import sys
import shutil
from pathlib import Path

HERE = Path(__file__).parent
CIRCUITS_DIR = HERE / "zk_circuits"
BUILD_DIR = CIRCUITS_DIR / "build"
PTAU_DIR = HERE / "ptau"
CONTRACTS_DIR = HERE / "contracts"

CIRCUIT_NAME = "arb_proof"

def run(cmd, cwd=None, desc=""):
    print(f"[build] {desc or ' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd or HERE, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[build] FAILED: {result.stderr}")
        sys.exit(1)
    if result.stdout:
        print(result.stdout[:500])
    return result

def main():
    print("[build] === VERITAS ZK Artifact Builder ===")
    
    # Ensure directories exist
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    PTAU_DIR.mkdir(parents=True, exist_ok=True)
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check if circom is available
    try:
        subprocess.run(["circom", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[build] WARNING: circom not found in PATH. Using node_modules version.")
        # Will use node_modules/.bin/circom in the commands below
    
    # 1. Compile circuit
    print("[build] Compiling circuit...")
    run([
        "circom", 
        str(CIRCUITS_DIR / f"{CIRCUIT_NAME}.circom"),
        "--r1cs", "--wasm", "--sym", "-o", str(BUILD_DIR)
    ], desc="Compiling circom circuit")
    
    # 2. Powers of Tau setup (reuse existing or download)
    ptau = PTAU_DIR / "pot14_final.ptau"
    if not ptau.exists():
        print("[build] Downloading Powers of Tau...")
        run([
            "powersoftau", "new", "bn128", "14", 
            str(PTAU_DIR / "pot14_0000.ptau"), "-v"
        ], desc="Creating new Powers of Tau")
        run([
            "powersoftau", "contribute",
            str(PTAU_DIR / "pot14_0000.ptau"),
            str(ptau),
            "--name", "VERITAS", "-v"
        ], desc="Contributing to Powers of Tau")
    else:
        print("[build] Using existing Powers of Tau")
    
    # 3. Groth16 setup
    print("[build] Setting up Groth16...")
    run([
        "snarkjs", "groth16", "setup",
        str(BUILD_DIR / f"{CIRCUIT_NAME}.r1cs"),
        str(ptau),
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0000.zkey")
    ], desc="Groth16 setup")
    
    # 4. Generate contributing key
    print("[build] Generating contributing key...")
    run([
        "snarkjs", "zkey", "contribute",
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0000.zkey"),
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"),
        "--name", "VERITAS", "-v"
    ], desc="ZKey contribute")
    
    # 5. Export verification key
    print("[build] Exporting verification key...")
    run([
        "snarkjs", "zkey", "export", "verificationkey",
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"),
        str(CONTRACTS_DIR / f"{CIRCUIT_NAME}_vkey.json")
    ], desc="Exporting verification key")
    
    # 6. Export Solidity verifier
    print("[build] Exporting Solidity verifier...")
    run([
        "snarkjs", "zkey", "export", "solidityverifier",
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"),
        str(CONTRACTS_DIR / "Groth16Verifier.sol")
    ], desc="Exporting Solidity verifier")
    
    print("[build] ✅ ZK artifacts built successfully!")
    print(f"[build] Circuit: {BUILD_DIR / f'{CIRCUIT_NAME}.wasm'}")
    print(f"[build] Proving key: {BUILD_DIR / f'{CIRCUIT_NAME}_0001.zkey'}")
    print(f"[build] Verification key: {CONTRACTS_DIR / f'{CIRCUIT_NAME}_vkey.json'}")
    print(f"[build] Solidity verifier: {CONTRACTS_DIR / 'Groth16Verifier.sol'}")

if __name__ == "__main__":
    main()