#!/usr/bin/env python3
"""T4/T5 positive+negative control harness.

Deploys two contracts to local anvil:
  1. VerifierVulnerable -- broken verifyProof (returns true unconditionally)
  2. VerifierHardened   -- real BN254 pairing precompile check

Runs the T4 differential loop against both and asserts:
  - Vulnerable contract: on-chain ACCEPTED, local oracle False = CONFIRMED
  - Hardened contract:    on-chain REVERTED = HEALTHY

This is the positive control that proves the system detects real attack
surfaces rather than just emitting "everything is fine" on any target.
"""
import json, os, sys, subprocess, time, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.rpc import RPC, uint

# ---------------------------------------------------------------------------
# solc compilation
# ---------------------------------------------------------------------------

def compile_contracts():
    import solcx
    try:
        solcx.install_solc("0.8.20")
    except Exception:
        pass
    from solcx import compile_files
    here = os.path.dirname(os.path.abspath(__file__))
    sources = [
        os.path.join(here, "VerifierVulnerable.sol"),
        os.path.join(here, "VerifierHardened.sol"),
    ]
    out = compile_files(
        sources,
        solc_version="0.8.20",
        output_values=["abi", "bin"],
        allow_paths=here,
    )
    contracts = {}
    for key, val in out.items():
        name = key.split(":")[-1]
        contracts[name] = {
            "abi": json.loads(val["abi"]) if isinstance(val["abi"], str) else val["abi"],
            "bytecode": "0x" + val["bin"],
        }
    return contracts

# ---------------------------------------------------------------------------
# anvil
# ---------------------------------------------------------------------------

def find_anvil():
    for p in [
        os.path.join(os.path.expanduser("~"), ".foundry", "anvil.exe"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "anvil.exe"),
    ]:
        if os.path.isfile(p):
            return p
    return shutil.which("anvil")

def start_anvil(anvil_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen(
        [anvil_path, "--port", str(port), "--accounts", "1",
         "--balance", "100", "--block-time", "1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    host = f"http://127.0.0.1:{port}"
    rpc = RPC(host, timeout=5, retries=1)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            rpc.call("eth_chainId", [])
            return proc, host
        except Exception:
            time.sleep(0.5)
    proc.kill()
    return None, None

def deploy(host, bytecode):
    """Deploy a contract on anvil, return the deployed address (lowercase)."""
    rpc = RPC(host, timeout=30, retries=3)
    from_account = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    tx_hash = rpc.call("eth_sendTransaction", [{
        "from": from_account,
        "data": bytecode,
        "gas": "0x500000",
    }])
    time.sleep(1.5)  # let the block mine
    receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])
    if not receipt or receipt.get("status") != "0x1":
        raise RuntimeError(f"deploy failed: {receipt}")
    return receipt["contractAddress"].lower()

# ---------------------------------------------------------------------------
# T4 differential loop on a local target
# ---------------------------------------------------------------------------

def run_t4_on(address, rpc_url, corpus=16, seed=0xCAFE):
    from zk.differential import run_campaign
    return run_campaign(address, rpc_url=rpc_url, corpus_size=corpus,
                        seed=seed, delay=0.0, persist=False, verbose=True)

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("[control] compiling contracts...")
    contracts = compile_contracts()
    vuln = contracts["VerifierVulnerable"]
    hard = contracts["VerifierHardened"]
    print(f"  VerifierVulnerable: {len(vuln['bytecode'])//2} bytes")
    print(f"  VerifierHardened:   {len(hard['bytecode'])//2} bytes")

    anvil = find_anvil()
    if not anvil:
        print("[control] anvil not found -- cannot run positive control")
        return False

    print(f"[control] starting anvil: {anvil}")
    proc, host = start_anvil(anvil)
    if not host:
        print("[control] anvil failed to start")
        return False
    print(f"[control] anvil ready at {host}")

    try:
        print("[control] deploying VerifierVulnerable...")
        vuln_addr = deploy(host, vuln["bytecode"])
        print(f"  deployed at {vuln_addr}")

        print("[control] deploying VerifierHardened...")
        hard_addr = deploy(host, hard["bytecode"])
        print(f"  deployed at {hard_addr}")

        # ---- negative control: hardened verifier ----
        print(f"\n{'='*60}")
        print("[control] NEGATIVE CONTROL: VerifierHardened (should be HEALTHY)")
        print(f"{'='*60}")
        hard_camp = run_t4_on(hard_addr, host, corpus=16)
        print(f"  accepted={hard_camp.get('accepted',0)} "
              f"reverted={hard_camp.get('reverted',0)} "
              f"hits={len(hard_camp.get('hits',[]))}")
        hard_ok = (hard_camp.get("accepted", 0) == 0
                   and len(hard_camp.get("hits", [])) == 0)

        # ---- positive control: vulnerable verifier ----
        print(f"\n{'='*60}")
        print("[control] POSITIVE CONTROL: VerifierVulnerable (MUST be CONFIRMED)")
        print(f"{'='*60}")
        vuln_camp = run_t4_on(vuln_addr, host, corpus=16)
        print(f"  accepted={vuln_camp.get('accepted',0)} "
              f"reverted={vuln_camp.get('reverted',0)} "
              f"hits={len(vuln_camp.get('hits',[]))}")
        vuln_ok = (vuln_camp.get("accepted", 0) > 0
                   and any(h.get("confirmed", False)
                           for h in vuln_camp.get("hits", [])))

        # ---- verdict ----
        print(f"\n{'='*60}")
        print("[control] RESULTS")
        print(f"{'='*60}")
        print(f"  VerifierHardened:   {'PASS' if hard_ok else 'FAIL'} "
              f"(expected: 0 accepted, 0 hits)")
        print(f"  VerifierVulnerable: {'PASS' if vuln_ok else 'FAIL'} "
              f"(expected: >0 accepted with local oracle False)")

        if hard_ok and vuln_ok:
            print("\n[control] BOTH CONTROLS PASSED -- T4 differential loop")
            print("  correctly distinguishes vulnerable from hardened contracts.")
            print("  Positive control proves the system detects REAL attack surfaces.")
            return True
        else:
            print("\n[control] CONTROL FAILURE -- system cannot be trusted:")
            if not vuln_ok:
                print("  FAIL: Vulnerable contract was NOT detected (false negative)")
            if not hard_ok:
                print("  FAIL: Hardened contract was NOT rejected (false positive)")
            return False

    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        print("[control] anvil terminated")

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
