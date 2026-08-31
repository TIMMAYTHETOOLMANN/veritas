#!/usr/bin/env python3
"""test_zk_deploy.py — fork-simulate FlashloanArbV2 (ZK) deploy + verifier bind."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eth_utils import keccak, to_checksum_address
from eth_abi import encode
import sim_gate

DEPLOYER = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"  # anvil key0
AAVE_POOL = to_checksum_address("0x794a61358D6845594F94dc1DB02A252b5b4814aD")
V3_ROUTER = to_checksum_address("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45")
WETH = to_checksum_address("0x82af49447d8a07e3bd95bd0d56f35241523fbab1")


def main():
    verifier_bin = open("contracts/Groth16Verifier.bin").read().strip()
    if not verifier_bin.startswith("0x"):
        verifier_bin = "0x" + verifier_bin
    executor_bin = open("contracts/out/FlashloanArbV2.bin").read().strip()
    if not executor_bin.startswith("0x"):
        executor_bin = "0x" + executor_bin

    print("[sim] launching anvil fork...", flush=True)
    proc, host, head, fork_url = sim_gate.launch_fork()
    try:
        fork = sim_gate.Fork(host)
        fork.set_balance(DEPLOYER, sim_gate.wad(1000))
        fork.impersonate(DEPLOYER)

        print("[sim] deploying verifier...", flush=True)
        vaddr = fork.deploy_contract(verifier_bin, DEPLOYER)
        vcode = fork.code(vaddr)
        print(f"[sim] verifier {vaddr} code_len={len(vcode)}", flush=True)

        ctor = encode(["address", "address", "address"],
                      [AAVE_POOL, V3_ROUTER, WETH]).hex()
        deploy_data = executor_bin + ctor[2:]
        print("[sim] deploying executor...", flush=True)
        eaddr = fork.deploy_contract(deploy_data, DEPLOYER)
        ecode = fork.code(eaddr)
        ok = len(ecode) > 2
        print(f"[sim] executor {eaddr} code_len={len(ecode)} -> "
              f"{'DEPLOYED OK' if ok else 'REVERTED/EMPTY'}", flush=True)
        if not ok:
            print("[sim] DEPLOY FAILED", flush=True)
            return

        sel = keccak(text="setVerifier(address)")[:4].hex()
        bind = "0x" + sel + encode(["address"], [vaddr]).hex()[2:]
        txid = fork.send_from(DEPLOYER, eaddr, bind)
        fork.wait_tx(txid)
        gsel = keccak(text="verifier()")[:4].hex()
        res = fork.call(eaddr, "0x" + gsel)
        bound = "0x" + res[26:66] if res and len(res) >= 66 else res
        print(f"[sim] executor.verifier() = {bound}", flush=True)
        print(f"[sim] BIND OK: {bound.lower() == vaddr.lower()}", flush=True)
        print(f"\n[sim] RESULT: executor={eaddr} verifier={vaddr}", flush=True)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()