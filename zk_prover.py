#!/usr/bin/env python3
"""
zk_prover.py — VERITAS ZK-Prover: generates Groth16 proofs for private arb execution.
Integrates ShadowPath's Verkle+Groth16 pipeline into the hunter loop.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, List

sys.path.insert(0, str(Path(__file__).parent))
import arb_engine
from core.rpc import RPC

# Optional: Verkle registry sync (ShadowPath go-verkle-zk)
try:
    from verkle_sync import VerkleRegistry
    VERKLE_AVAILABLE = True
except Exception:
    VerkleRegistry = None
    VERKLE_AVAILABLE = False

HERE = Path(__file__).parent
CIRCUITS_DIR = HERE / "zk_circuits"
BUILD_DIR = CIRCUITS_DIR / "build"
PTAU_DIR = HERE / "ptau"

CIRCUIT_NAME = "arb_proof"
PROVING_KEY = BUILD_DIR / f"{CIRCUIT_NAME}_0001.zkey"
VERIFICATION_KEY = BUILD_DIR / f"{CIRCUIT_NAME}_vkey.json"
WASM_FILE = BUILD_DIR / f"{CIRCUIT_NAME}_js" / f"{CIRCUIT_NAME}.wasm"
VERIFIER_SOL = HERE / "contracts" / "Groth16Verifier.sol"

class ZKProver:
    """Generates ZK-proofs for arb opportunities — hides pools, sizes, paths from mempool."""
    
    def __init__(self, rpc: RPC):
        self.rpc = rpc
        self.verkle_registry = VerkleRegistry(rpc) if VERKLE_AVAILABLE else None
        self._ensure_artifacts()
    
    def _ensure_artifacts(self):
        """Compile circuit + generate keys if missing (one-time ~5 min)."""
        if PROVING_KEY.exists() and VERIFICATION_KEY.exists() and VERIFIER_SOL.exists():
            return
        
        print("[zk_prover] Building ZK artifacts (one-time)...")
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        PTAU_DIR.mkdir(parents=True, exist_ok=True)
        
        # 1. Check for circomlib
        circomlib_path = self._find_circomlib()
        if not circomlib_path:
            raise RuntimeError("circomlib not found. Run: npm install -g circomlib")
        
        # 2. Compile circom
        print("[zk_prover] Compiling circuit...")
        subprocess.run([
            "circom", str(CIRCUITS_DIR / f"{CIRCUIT_NAME}.circom"),
            "--r1cs", "--wasm", "--sym", "-o", str(BUILD_DIR),
            "-l", circomlib_path
        ], check=True, capture_output=True)
        
        # 3. Powers of Tau (use universal ptau for BN254)
        ptau = PTAU_DIR / "pot14_final.ptau"
        if not ptau.exists():
            print("[zk_prover] Downloading ptau...")
            subprocess.run([
                "wget", "-O", str(ptau),
                "https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_14.ptau"
            ], check=True)
        
        # 4. Groth16 setup
        print("[zk_prover] Groth16 setup...")
        subprocess.run(["snarkjs", "groth16", "setup",
            str(BUILD_DIR / f"{CIRCUIT_NAME}.r1cs"), str(ptau),
            str(BUILD_DIR / f"{CIRCUIT_NAME}_0000.zkey")], check=True, capture_output=True)
        subprocess.run(["snarkjs", "zkey", "contribute",
            str(BUILD_DIR / f"{CIRCUIT_NAME}_0000.zkey"),
            str(PROVING_KEY), "--name", "VERITAS", "-v"], check=True, capture_output=True)
        subprocess.run(["snarkjs", "zkey", "export", "verificationkey",
            str(PROVING_KEY), str(VERIFICATION_KEY)], check=True, capture_output=True)
        
        # 5. Export Solidity verifier
        print("[zk_prover] Exporting Solidity verifier...")
        subprocess.run(["snarkjs", "zkey", "export", "solidityverifier",
            str(PROVING_KEY), str(VERIFIER_SOL)], check=True, capture_output=True)
        
        print("[zk_prover] Build complete.")
    
    def _find_circomlib(self) -> Optional[str]:
        """Find circomlib installation path."""
        candidates = [
            Path.home() / "node_modules" / "circomlib",
            Path("/usr/local/lib/node_modules/circomlib"),
            Path("/opt/homebrew/lib/node_modules/circomlib"),
            HERE / "node_modules" / "circomlib",
        ]
        for c in candidates:
            if (c / "circuits" / "poseidon.circom").exists():
                return str(c)
        return None
    
    def _fetch_pool_state(self, pool_addr: str) -> Dict:
        """Fetch live reserves + build Verkle witness.

        Returns reserves ordered as (weth_side, quote_side) so the prover can
        feed the circuit's reserve_a0=WETH, reserve_a1=quote convention
        regardless of the pool's token0/token1 ordering.
        """
        # V2 pool state
        t0 = arb_engine.parse_addr(self.rpc.eth_call(pool_addr, "0x" + arb_engine.SEL["token0"]))
        t1 = arb_engine.parse_addr(self.rpc.eth_call(pool_addr, "0x" + arb_engine.SEL["token1"]))
        r0, r1 = arb_engine.parse_reserves(self.rpc.eth_call(pool_addr, "0x" + arb_engine.SEL["reserves"]))

        if not t0 or not t1 or r0 is None or r1 is None:
            raise ValueError(f"Invalid pool state for {pool_addr}")

        # Reorder reserves so r0 = WETH-side, r1 = quote-side
        WETH = arb_engine.WETH
        if t0 == WETH:
            weth_r, quote_r = r0, r1
        elif t1 == WETH:
            weth_r, quote_r = r1, r0
        else:
            # Pool doesn't involve WETH — shouldn't happen for our edges
            raise ValueError(f"Pool {pool_addr} has no WETH side")

        # Get Verkle witness from registry (real KZG or placeholder)
        verkle_data = {"verkle_root": 0, "verkle_witness": [0]*32, "verkle_path": [0]*5}
        
        if self.verkle_registry:
            witness = self.verkle_registry.get_witness(pool_addr)
            if witness:
                verkle_data = {
                    "verkle_root": int(witness["root"], 16) if isinstance(witness["root"], str) else witness["root"],
                    "verkle_witness": [int(w, 16) if isinstance(w, str) else w for w in witness["witness"]],
                    "verkle_path": [int(w, 16) if isinstance(w, str) else w for w in witness["path"]],
                }
            else:
                # Pool not in registry - compute on-demand (fallback)
                verkle_data = self._compute_placeholder_verkle(pool_addr, t0, t1, r0, r1)
        else:
            verkle_data = self._compute_placeholder_verkle(pool_addr, t0, t1, r0, r1)
        
        return {
            "token0": t0, "token1": t1, "r0": weth_r, "r1": quote_r,
            **verkle_data,
        }
    
    def _compute_placeholder_verkle(self, pool_addr: str, t0: str, t1: str, r0: int, r1: int) -> Dict:
        """Compute placeholder Verkle root (hash-based) for testing."""
        import hashlib
        data = f"{pool_addr.lower()}{t0.lower()}{t1.lower()}{r0}{r1}".encode()
        root = int(hashlib.sha256(data).hexdigest()[:64], 16)
        return {
            "verkle_root": root,
            "verkle_witness": [root] * 32,
            "verkle_path": [root] * 5,
        }

    def _compute_state_root(self, pool_a: str, state_a: Dict, pool_b: str, state_b: Dict) -> int:
        """Compute circuit-compatible state_root via poseidon_helper.mjs."""
        helper = (CIRCUITS_DIR / "poseidon_helper.mjs").as_posix()
        path_a0, path_a1 = state_a["verkle_path"][0], state_a["verkle_path"][1]
        path_b0, path_b1 = state_b["verkle_path"][0], state_b["verkle_path"][1]
        cmd = [
            "node", helper, "stateRoot",
            str(int(pool_a, 16)), str(state_a["r0"]), str(state_a["r1"]),
            str(path_a0), str(path_a1),
            str(int(pool_b, 16)), str(state_b["r0"]), str(state_b["r1"]),
            str(path_b0), str(path_b1),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"poseidon_helper failed: {result.stderr}")
        return int(result.stdout.strip())

    def generate_proof(self, edge: Dict, eth_usd: float, gas_usd: float) -> Optional[Dict]:
        """
        Generate Groth16 proof for a vetted edge.
        Returns proof dict ready for on-chain verification, or None on failure.
        """
        try:
            pool_a = edge["buy_venue"]
            pool_b = edge["sell_venue"]
            size_weth = edge["size_weth"]
            buy_kind = edge.get("buy_kind", 0)
            sell_kind = edge.get("sell_kind", 0)
            # Circuit fee_a/fee_b: plain bps. V2 CPMM = 0.3% = 30 bps.
            # V3 fee tiers are stored as hundredths-of-bps (500 = 0.05%),
            # so divide by 10 to get bps (500 -> 50).
            fee_a = 30 if buy_kind == 0 else int(edge.get("buy_fee", 0)) // 10
            fee_b = 30 if sell_kind == 0 else int(edge.get("sell_fee", 0)) // 10
            
            # Fetch live state + witnesses
            state_a = self._fetch_pool_state(pool_a)
            state_b = self._fetch_pool_state(pool_b)

            # Compute Poseidon(2)(Poseidon(5)(A), Poseidon(5)(B)) via the JS helper.
            # The circuit requires state_root to equal this commitment exactly,
            # not the block hash.
            state_root = self._compute_state_root(pool_a, state_a, pool_b, state_b)

            # Build input.json for snarkjs
            input_data = {
                "eth_usd": int(eth_usd * 1e6),
                "gas_usd": int(gas_usd * 1e6),
                "safety_margin": int(0.50 * 1e6),  # $0.50 minimum
                "state_root": state_root,
                
                "pool_a_addr": int(pool_a, 16),
                "pool_b_addr": int(pool_b, 16),
                "reserve_a0": state_a["r0"],
                "reserve_a1": state_a["r1"],
                "reserve_b0": state_b["r0"],
                "reserve_b1": state_b["r1"],
                "amount_in": int(size_weth * 1e18),
                "fee_a": fee_a,
                "fee_b": fee_b,
                
                "verkle_witness_a": state_a["verkle_witness"],
                "verkle_witness_b": state_b["verkle_witness"],
                "verkle_path_a": state_a["verkle_path"],
                "verkle_path_b": state_b["verkle_path"],
            }
            
            input_file = BUILD_DIR / "input.json"
            with open(input_file, "w") as f:
                json.dump(input_data, f)
            
            # Generate witness
            witness_file = BUILD_DIR / "witness.wtns"
            generate_witness = BUILD_DIR / f"{CIRCUIT_NAME}_js" / "generate_witness.js"
            result = subprocess.run([
                "node", str(generate_witness), str(WASM_FILE), str(input_file), str(witness_file)
            ], capture_output=True, timeout=90)
            if result.returncode != 0:
                print(f"[zk_prover] Witness gen failed: {result.stderr.decode()}")
                return None
            
            # Generate proof (Groth16)
            proof_file = BUILD_DIR / "proof.json"
            public_file = BUILD_DIR / "public.json"
            result = subprocess.run([
                "snarkjs", "groth16", "prove",
                str(PROVING_KEY), str(witness_file),
                str(proof_file), str(public_file)
            ], capture_output=True, timeout=120)
            if result.returncode != 0:
                print(f"[zk_prover] Prove failed: {result.stderr.decode()}")
                return None
            
            # Verify locally before returning
            result = subprocess.run([
                "snarkjs", "groth16", "verify",
                str(VERIFICATION_KEY), str(public_file), str(proof_file)
            ], capture_output=True, timeout=30)
            if result.returncode != 0:
                print(f"[zk_prover] Local verify failed: {result.stderr.decode()}")
                return None
            
            with open(proof_file) as f:
                proof = json.load(f)
            with open(public_file) as f:
                public = json.load(f)
            
            return {
                "proof": proof,
                "public_signals": public,
                # Circuit output order: [nullifier, profit_usd, net_profit_usd]
                # Public signal 0 = nullifier (must match contract's bytes32(uint256(publicSignals[0])))
                "nullifier": public[0],
                "profit_usd": float(public[1]) / 1e6,
                "net_profit_usd": float(public[2]) / 1e6,
            }
            
        except Exception as e:
            print(f"[zk_prover] Error: {e}")
            return None
    
    def prove_batch(self, edges: List[Dict], eth_usd: float, gas_usd: float) -> List[Dict]:
        """Generate proofs for multiple edges in parallel."""
        from concurrent.futures import ThreadPoolExecutor
        
        results = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(self.generate_proof, e, eth_usd, gas_usd): e for e in edges}
            for fut in futures:
                result = fut.result(timeout=180)
                if result:
                    results.append(result)
        return results


# Convenience function for hunter integration
def prove_edge(edge: Dict, rpc: RPC, eth_usd: float, gas_usd: float) -> Optional[Dict]:
    prover = ZKProver(rpc)
    return prover.generate_proof(edge, eth_usd, gas_usd)


if __name__ == "__main__":
    # Test mode
    from core.rpc import RPC
    rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=60, retries=3)
    prover = ZKProver(rpc)
    print("[zk_prover] Ready. Proving key:", PROVING_KEY.exists())
    print("[zk_prover] Verification key:", VERIFICATION_KEY.exists())
    print("[zk_prover] Verifier contract:", VERIFIER_SOL.exists())
    print("[zk_prover] Verkle registry:", "active" if VERKLE_AVAILABLE else "placeholder")