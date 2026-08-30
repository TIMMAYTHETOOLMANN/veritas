# build_zk.py - One-time builder for ZK artifacts (circom + snarkjs)
# This script attempts to compile the circom circuit and generate the necessary artifacts.
# If circom fails due to Windows line ending issues, it will instruct the user to use WSL2.

import os
import subprocess
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ZK_DIR = os.path.join(HERE, "zk_circuits")
CONTRACTS_DIR = os.path.join(HERE, "contracts")

def run(cmd, cwd=None):
    print(f"[build] Running: {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[build] ERROR: {result.stderr}")
    else:
        if result.stdout.strip():
            print(f"[build] {result.stdout.strip()}")
    return result

def main():
    print("[build] Starting ZK artifact build...")

    # 1. Check for circom
    circom_check = run("circom --version")
    if circom_check.returncode != 0:
        print("[build] ERROR: circom not found. Please install circom and snarkjs globally:")
        print("  npm install -g circom snarkjs")
        return 1

    # 2. Check for circomlib
    circomlib_path = os.path.join(HERE, "node_modules", "circomlib")
    if not os.path.exists(circomlib_path):
        print("[build] Installing circomlib...")
        run("npm install circomlib", cwd=HERE)

    # 3. Compile circuit
    circuit_path = os.path.join(ZK_DIR, "arb_proof.circom")
    if not os.path.exists(circuit_path):
        print(f"[build] ERROR: Circuit not found at {circuit_path}")
        return 1

    # Ensure circuit uses LF line endings
    print("[build] Normalizing line endings in circuit...")
    with open(circuit_path, 'rb') as f:
        content = f.read()
    # Replace CRLF with LF
    content = content.replace(b'\r\n', b'\n')
    # Also replace any stray CR
    content = content.replace(b'\r', b'\n')
    with open(circuit_path, 'wb') as f:
        f.write(content)

    # 4. Run circom
    build_dir = os.path.join(ZK_DIR, "build")
    os.makedirs(build_dir, exist_ok=True)
    r1cs_path = os.path.join(build_dir, "arb_proof.r1cs")
    wasm_path = os.path.join(build_dir, "arb_proof_wasm", "arb_proof.wasm")
    sym_path = os.path.join(build_dir, "arb_proof.sym")

    os.makedirs(os.path.dirname(wasm_path), exist_ok=True)

    circom_cmd = f'circom "{circuit_path}" -r "{r1cs_path}" -w "{wasm_path}" -s "{sym_path}" -l "{circomlib_path}"'
    result = run(circom_cmd)
    if result.returncode != 0:
        print("[build] ERROR: Circom compilation failed.")
        print("[build] This is often due to Windows line ending issues or circom version mismatch.")
        print("[build] Please try one of the following:")
        print("  1. Use WSL2 (Ubuntu) and run this script from within Linux.")
        print("  2. Ensure the circuit file uses LF line endings only (no CR).")
        print("  3. Check that the circom version matches the circuit pragma (0.5.46).")
        print("[build] For now, the hunter will fall back to non-ZK mode if ZK artifacts are missing.")
        return 1

    print("[build] Circom compilation successful.")

    # 5. Setup for Groth16 (powers of tau)
    ptau_path = os.path.join(ZK_DIR, "powersOfTau28_hez_final_14.ptau")
    if not os.path.exists(ptau_path):
        print("[build] Downloading powers of tau...")
        # Use curl or wget
        download_cmd = "curl -L -o powersOfTau28_hez_final_14.ptau https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_14.ptau"
        run(download_cmd, cwd=ZK_DIR)
        if not os.path.exists(ptau_path):
            print("[build] WARNING: Failed to download ptau. You may need to download it manually.")
            print("[build] URL: https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_14.ptau")
    else:
        print("[build] Powers of tau already present.")

    # 6. Groth16 setup
    if os.path.exists(ptau_path):
        print("[build] Running Groth16 setup...")
        zkey_path = os.path.join(build_dir, "arb_proof_0001.zkey")
        setup_cmd = f"snarkjs groth16 setup {r1cs_path} {ptau_path} {zkey_path}"
        run(setup_cmd)
        if not os.path.exists(zkey_path):
            print("[build] WARNING: Groth16 setup failed. Continuing anyway.")
        else:
            print("[build] Groth16 setup complete.")

            # 7. Export verification key
            vkey_path = os.path.join(build_dir, "arb_proof_vkey.json")
            export_cmd = f"snarkjs zkey export verificationkey {zkey_path} {vkey_path}"
            run(export_cmd)
            if os.path.exists(vkey_path):
                print("[build] Verification key exported.")
            else:
                print("[build] WARNING: Failed to export verification key.")

            # 8. Generate Solidity verifier
            verifier_path = os.path.join(CONTRACTS_DIR, "Groth16Verifier.sol")
            solidity_cmd = f"snarkjs zkey export solidityverifier {zkey_path} {verifier_path}"
            run(solidity_cmd)
            if os.path.exists(verifier_path):
                print("[build] Solidity verifier exported to contracts/Groth16Verifier.sol")
            else:
                print("[build] WARNING: Failed to export Solidity verifier.")

    print("[build] Build process complete.")
    print("[build] If ZK artifacts were generated successfully, the hunter will use ZK-proof mode.")
    print("[build] Otherwise, it will fall back to legacy fork-sim mode.")
    return 0

if __name__ == "__main__":
    sys.exit(main())