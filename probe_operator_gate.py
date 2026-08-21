# probe_operator_gate.py — test changeOperator / finishMigration / isMigrated gates
# on the 4 rich pools. All eth_call ($0, no state change).
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), "core"))
from core.rpc import RPC

rpc = RPC("https://ethereum-rpc.publicnode.com", timeout=25, retries=3)
attacker = "0x000000000000000000000000000000000000dead"

POOLS = [
    ("0xa160cdab225685da1d56aa342ad8841c3b53f291", "P100 245900ETH"),
    ("0x910cbd523d972eb0a6f4cae4618ad62622b39dbf", "P10 20120ETH"),
    ("0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936", "P1 3919ETH"),
    ("0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc", "P0.1 600ETH"),
]

def call(to, data, frm=attacker):
    try:
        r = rpc.call("eth_call", [{"to": to, "data": data, "from": frm}, "latest"])
        return ("OK", r)
    except Exception as e:
        s = str(e)
        try:
            d = s.split("'data': '")[1].split("'")[0]
            if d.startswith("0x08c379a0"):
                ln = int(d[10+64:10+128], 16)
                msg = bytes.fromhex(d[10+128:10+128+ln*2]).decode("utf-8", errors="replace")
                return ("REVERT", msg)
            return ("REVERT", s[:150])
        except Exception:
            return ("REVERT", s[:150])

pad_addr = "000000000000000000000000000000000000000000000000000000000000dead"

for addr, tag in POOLS:
    print(f"=== {tag} {addr}")
    # isMigrated()
    st, r = call(addr, "0xb06faf62")
    mig = int(r, 16) if st == "OK" and r and r != "0x" else None
    print(f"  isMigrated()          -> {st} {r if st=='OK' else r}")
    # changeOperator(attacker) from random EOA
    st, r = call(addr, "0x06394c9b" + pad_addr)
    print(f"  changeOperator(rand)  -> {st} {r if st=='OK' else r}")
    # finishMigration() from random EOA
    st, r = call(addr, "0x88d761f2")
    print(f"  finishMigration(rand) -> {st} {r if st=='OK' else r}")
    # operator()
    st, r = call(addr, "0x570ca735")
    print(f"  operator()            -> {st} 0x{r[-40:] if r and len(r)>=42 else r}")
    # verifier()
    st, r = call(addr, "0x2b7ac3f3")
    print(f"  verifier()            -> {st} 0x{r[-40:] if r and len(r)>=42 else r}")
