#!/usr/bin/env python3
"""
build_zk.py — One-time ZK artifact builder for VERITAS ShadowPath integration.
Compiles circom circuit, generates Groth16 keys, exports Solidity verifier.
Run once: python3 build_zk.py
"""
import json
import subprocess
import sys
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
    
    # 1. Check dependencies
    for tool in ["circom", "snarkjs", "node"]:
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            print(f"[build] ERROR: {tool} not found in PATH")
            print("  Install: npm install -g circom snarkjs")
            sys.exit(1)
    
    # 2. Find circomlib
    circomlib = None
    for base in [Path.home() / "node_modules", Path("/usr/local/lib/node_modules"), 
                 Path("/opt/homebrew/lib/node_modules"), HERE / "node_modules"]:
        p = base / "circomlib" / "circuits" / "poseidon.circom"
        if p.exists():
            circomlib = str(base / "circomlib")
            break
    
    if not circomlib:
        print("[build] circomlib not found. Install with: npm install -g circomlib")
        print("[build] Or install locally: npm install circomlib")
        sys.exit(1)
    
    print(f"[build] Using circomlib: {circomlib}")
    
    # 3. Create directories
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    PTAU_DIR.mkdir(parents=True, exist_ok=True)
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # 4. Compile circuit
    print("[build] Compiling circuit...")
    run([
        "circom", str(CIRCUITS_DIR / f"{CIRCUIT_NAME}.circom"),
        "--r1cs", "--wasm", "--sym", "-o", str(BUILD_DIR),
        "-l", circomlib
    ], desc="circom compile")
    
    # 5. Powers of Tau (BN254 universal ptau)
    ptau = PTAU_DIR / "pot14_final.ptau"
    if not ptau.exists():
        print("[build] Downloading ptau...")
        run([
            "wget", "-O", str(ptau),
            "https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_14.ptau"
        ], desc="download ptau")
    
    # 6. Groth16 setup
    print("[build] Groth16 setup...")
    run(["snarkjs", "groth16", "setup",
        str(BUILD_DIR / f"{CIRCUIT_NAME}.r1cs"), str(ptau),
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0000.zkey")], desc="groth16 setup")
    
    print("[build] Contributing to proving key...")
    run(["snarkjs", "zkey", "contribute",
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0000.zkey"),
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"), "--name", "VERITAS", "-v"],
        desc="zkey contribute")
    
    print("[build] Exporting verification key...")
    run(["snarkjs", "zkey", "export", "verificationkey",
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"),
        str(BUILD_DIR / f"{CIRCUIT_NAME}_vkey.json")], desc="export vkey")
    
    print("[build] Exporting Solidity verifier...")
    run(["snarkjs", "zkey", "export", "solidityverifier",
        str(BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"),
        str(CONTRACTS_DIR / "Groth16Verifier.sol")], desc="export solidity verifier")
    
    # 7. Verify artifacts
    artifacts = [
        BUILD_DIR / f"{CIRCUIT_NAME}.r1cs",
        BUILD_DIR / f"{CIRCUIT_NAME}_js" / f"{CIRCUIT_NAME}.wasm",
        BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey",
        BUILD_DIR / f"{CIRCUIT_NAME}_vkey.json",
        CONTRACTS_DIR / "Groth16Verifier.sol",
    ]
    
    print("[build] Verifying artifacts...")
    for a in artifacts:
        if a.exists():
            print(f"  ✓ {a}")
        else:
            print(f"  ✗ MISSING: {a}")
            sys.exit(1)
    
    print("[build] === BUILD COMPLETE ===")
    print(f"  Proving key: {BUILD_DIR / f'{CIRCUIT_NAME}_0001.zkey'}")
    print(f"  Verification key: {BUILD_DIR / f'{CIRCUIT_NAME}_vkey.json'}")
    print(f"  Solidity verifier: {CONTRACTS_DIR / 'Groth16Verifier.sol'}")
    print(f"  WASM: {BUILD_DIR / f'{CIRCUIT_NAME}_js' / f'{CIRCUIT_NAME}.wasm'}")

if __name__ == "__main__":
    main()