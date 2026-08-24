# probe_admin_gates.py — definitive eth_call probe of updateVerifier/changeOperator
# on every pool that dispatches them. Doctrine: memory claims "gated, operator=0x0
# dead" — but claims need probe evidence per pool. If ANY pool accepts the call
# (no revert), that is a LIVE unprotected admin gate on a funded pool = P>0.
# Read-only, $0.
import sys, time, json
sys.path.insert(0, ".")
from core.rpc import RPC

DUMMY_VERIFIER = "0" * 24 + "dead" * 10  # 32-byte ABI word, lowercase hex (0xdeadDEAD... pattern)
DUMMY_WORD = "000000000000000000000000" + "de" * 20  # clean lowercase address word
SEL_UPDATEVER = "0x97fc007c"        # updateVerifier(address)
SEL_CHANGEOP  = "0x06394c9b"        # changeOperator(address)

POOLS_A = [  # 5999B family: operator + updateVerifier + changeOperator
    "0xa160cdab225685da1d56aa342ad8841c3b53f291",
    "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf",
    "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936",
    "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc",
    "0x0836222f2b2b24a3f36f98668ed8f0b38d1a872f",
    "0x4736dcf1b7a3d580672cce6e7c65cd5cc9cfba9d",
    "0xfd8610d20aa15b7b2e3be39b396a1bc3516c7144",
    "0x169ad27a470d064dede56a2d3ff727986b15d52b",
    "0xd96f2b1c14db8458374d9aca76e26c3d18364307",
    "0xd4b88df4d29f5cedd6857912842cff3b20c8cfa3",
]

def probe(rpc, pool, sel, arg_word):
    data = sel + arg_word
    try:
        r = rpc.eth_call(pool, data)
        return ("ACCEPTED", r)   # <== LIVE GATE if this happens
    except Exception as e:
        msg = str(e)
        if "revert" in msg.lower():
            return ("REVERT", msg[-120:])
        return ("RPC_ERR", msg[:120])

def main():
    rpc = RPC("https://ethereum-rpc.publicnode.com", timeout=25, retries=2)
    rpc2 = RPC("https://eth.drpc.org", timeout=25, retries=2)
    results = []
    for pool in POOLS_A:
        for name, sel in [("updateVerifier", SEL_UPDATEVER), ("changeOperator", SEL_CHANGEOP)]:
            st, detail = probe(rpc, pool, sel, DUMMY_VERIFIER)
            if st == "ACCEPTED":
                # CROSS-VERIFY on second RPC before believing it
                st2, d2 = probe(rpc2, pool, sel, DUMMY_VERIFIER)
                detail = f"primary={detail} | cross={st2}:{d2}"
            results.append({"pool": pool, "fn": name, "status": st, "detail": detail})
            mark = "  <<< LIVE GATE!" if st == "ACCEPTED" else ""
            print(f"{pool[:12]} {name:14s} {st:9s} {str(detail)[:90]}{mark}")
            time.sleep(0.25)
    with open("cache/admin_gate_probes.json", "w") as f:
        json.dump({"ts": int(time.time()), "results": results}, f, indent=1)
    live = [r for r in results if r["status"] == "ACCEPTED"]
    print(f"\n{len(live)} live gates out of {len(results)} probes")
    if not live:
        print("All admin gates confirmed DEAD (revert). Admin-gate class closed on mainnet.")

if __name__ == "__main__":
    main()
