#!/usr/bin/env python3
"""End-to-end: zk/run.run_target against a locally-deployed vulnerable verifier.

Proves the orchestration wiring:
  T4 differential campaign  -> confirmed hits -> static impact rows
                            -> PoC file auto-written to artifacts/pocs/.

Uses a fresh local anvil (no fork, no network) so it is $0 and deterministic.
"""
import json, os, socket, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.rpc import RPC
from zk import divergence as L5D

HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.join(HERE, "VerifierVulnerable.sol")
FUNDER = L5D.UNLOCKED_ATTACKER  # anvil account #0, unlocked


def main():
    import solcx
    try:
        solcx.install_solc("0.8.20")
    except Exception:
        pass
    out = solcx.compile_files([SELF], solc_version="0.8.20",
                              output_values=["abi", "bin"], allow_paths=HERE)
    bytecode = None
    for k, v in out.items():
        if k.endswith("VerifierVulnerable"):
            bytecode = "0x" + v["bin"]
    assert bytecode

    anvil = L5D.find_anvil()
    assert anvil
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    proc = subprocess.Popen([anvil, "--port", str(port), "--accounts", "1", "--balance", "100"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    host = f"http://127.0.0.1:{port}"
    rpc = RPC(host, timeout=10, retries=3)
    t = time.time()
    while time.time() < t + 30:
        try:
            rpc.call("eth_chainId", []); break
        except Exception:
            time.sleep(0.5)
    try:
        txh = rpc.call("eth_sendTransaction", [{"from": FUNDER, "data": bytecode, "gas": "0x400000"}])
        time.sleep(1.5)
        rcpt = rpc.call("eth_getTransactionReceipt", [txh])
        vuln = rcpt["contractAddress"].lower()
        print(f"[e2e] VerifierVulnerable deployed: {vuln}")

        from zk import run as RUN
        camp = RUN.run_target(vuln, corpus=8, seed=0xCAFE, delay=0.0,
                              rpc_url=host, impact=True, divergence=False)
        print(f"[e2e] accepted={camp.get('accepted')} hits={len(camp.get('hits', []))} "
              f"confirmed={sum(1 for h in camp['hits'] if h.get('confirmed'))}")

        ok = (camp.get("accepted", 0) > 0
              and any(h.get("confirmed", False) for h in camp.get("hits", []))
              and camp.get("poc")
              and os.path.isfile(camp["poc"]))
        print(f"[e2e] PoC written: {camp.get('poc')}")
        print("[RESULT]", "PASS" if ok else "FAIL",
              "- run_target confirms + auto-writes PoC for a real vulnerable target")
        return 0 if ok else 1
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())