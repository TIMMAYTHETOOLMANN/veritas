# VERITAS end-to-end validation: known Tornado mainnet deployments
# T0 fingerprint -> value census -> T2 probe -> EV scoring -> SQLite persistence
import sys, json
sys.path.insert(0, r"C:\Users\timot\OneDrive\Documents\VERITAS")
from core import db
from core.rpc import RPC, uint
from core.selectors import scan_code, match_template, selectors_map
from core import value, probes, scoring

RPC_URL = "https://ethereum-rpc.publicnode.com"

# Public, well-known Tornado Cash pool deployments (mainnet)
# NOTE: addresses verified live 2026-08-17 via eth_getCode on 2 RPCs.
# 0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936 is the REAL 1-ETH pool
# (5191B code, ~3947 ETH). The 10/100-ETH spellings below are UNVERIFIED —
# validate before adding more.
TARGETS = {
    "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc": "TC 0.1 ETH",
    "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936": "TC 1 ETH (verified)",
}

def fmt(wei):
    return f"{int(wei)/1e18:.4f} ETH" if isinstance(wei, (int, float)) else str(wei)

def main():
    path = db.init()
    print(f"[db] {path}")
    rpc = RPC(RPC_URL)
    chain = uint(rpc.call("eth_chainId", []))
    print(f"[rpc] connected chainId={chain}")
    sm = selectors_map()
    print(f"[t0] verifyProof sel = {sm['verify']}  withdraw sel = {sm['withdraw']}")

    c = db.conn()
    for addr, label in TARGETS.items():
        print(f"\n=== {label}  {addr} ===")
        code = rpc.get_code(addr)
        present = scan_code(code)
        tid, sim = match_template(present)
        print(f"[t0] template={tid} sim={sim} code={len(code)//2-1}B "
              f"deposit={present['deposit']} withdraw={present['withdraw']} "
              f"nullif={present['nullif']} setver={present['setver']}")

        # config reads
        denom = uint(rpc.eth_call(addr, sm["denom"]))
        root = rpc.eth_call(addr, sm["getroot"])
        levels = uint(rpc.eth_call(addr, sm["levels"]))

        # value census (L0/L1)
        inv = value.inventory(addr, rpc, denom=denom)
        l0 = inv[0]["eth_wei"]; l1 = inv[1] if len(inv) > 1 else {}
        print(f"[t1] denom={fmt(denom)} levels={levels} root={root[:18]}...")
        print(f"[val] L0 balance={fmt(l0)} | L1 notes~{l1.get('approx_notes')}")

        # T2 probe battery
        res = probes.run_battery(rpc, addr, tid)
        for r in res:
            print(f"[t2] {r['probe']}: {r['verdict']}")

        # EV scoring on measured inventory (demonstration of actionability objectification)
        s = scoring.score("ungated_nullifier", inv, confirmed=False)  # suspect only
        s2 = scoring.score("caller_supplied_vk", inv, confirmed=True)  # hypothetical confirm
        print(f"[ev] ungated_nullifier (SUSPECT): ev={fmt(s['ev_wei'])} sev={s['severity']} actionable={s['actionable']}")
        print(f"[ev] caller_vk (CONFIRMED hypoth): ev={fmt(s2['ev_wei'])} sev={s2['severity']} actionable={s2['actionable']}")

        # persist
        db.put(c, "INSERT OR REPLACE INTO targets VALUES(?,?,?,?,?,?,?)",
               (addr, "ethereum", len(code)//2-1, None, tid, sim, db.now()))
        for i in inv:
            db.put(c, "INSERT OR REPLACE INTO inventory VALUES(?,?,?,?,?,?,?)",
                   (addr, i["layer"], "ETH", str(i.get("eth_wei") or i.get("balance_wei") or 0),
                    None, "census", db.now()))
        for r in res:
            db.put(c, "INSERT INTO probes(address,battery,probe,result,verdict,ts) VALUES(?,?,?,?,?,?)",
                   (addr, "A_nullifier", r["probe"], json.dumps(r)[:500], r["verdict"], db.now()))
        db.put(c, "INSERT INTO findings(address,vclass,tier,confidence,status,evidence,created) VALUES(?,?,?,?,?,?,?)",
               (addr, "baseline_scan", "T2", "deterministic",
                "HARDENED" if all(x["verdict"] in ("REVERTED_HEALTHY","GATED_HEALTHY") for x in res) else "SUSPECT",
                json.dumps({"template": tid, "sim": sim, "denom": str(denom)})[:500], db.now()))
    c.close()
    print("\n[done] findings persisted to veritas.db")

if __name__ == "__main__":
    main()
