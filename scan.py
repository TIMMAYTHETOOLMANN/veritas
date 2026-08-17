# scan.py — full T0->T2 pipeline over discovered ecosystem, findings to SQLite
import sys, json
sys.path.insert(0, r"C:\Users\timot\OneDrive\Documents\VERITAS")
from core import db, value, probes, scoring
from core.rpc import RPC, uint
from core.selectors import scan_code, match_template, selectors_map
from core.discovery import discover

RPC_URL = "https://ethereum-rpc.publicnode.com"
LOOKBACK = 300_000  # ~41 days of mainnet

def safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def fmt(wei):
    try: return f"{int(wei)/1e18:,.2f}"
    except Exception: return "0.00"

def main():
    db.init()
    rpc = RPC(RPC_URL)
    sm = selectors_map()
    latest = int(rpc.call("eth_blockNumber", []), 16)
    print(f"[t0] mainnet latest={latest} lookback={LOOKBACK} blocks")

    found, from_block = discover(rpc, latest, LOOKBACK)
    total_events = sum(v["deposits"] + v["withdrawals"] for v in found.values())
    print(f"[t0] discovered {len(found)} unique emitters / {total_events} events "
          f"in [{from_block}..{latest}]")

    c = db.conn()
    rows = []
    for addr, ev in sorted(found.items(), key=lambda kv: -kv[1]["last_block"]):
        code = rpc.get_code(addr)
        size = (len(code) - 2) // 2
        if size < 300:  # proxies/stubs logged separately, skipped for pipeline
            continue
        present = scan_code(code)
        tid, sim = match_template(present)
        denom = safe(lambda: uint(rpc.eth_call(addr, sm["denom"])))
        bal = rpc.get_balance(addr)
        res = safe(lambda: probes.run_battery(rpc, addr, tid), default=[])
        verdicts = {r["probe"]: r["verdict"] for r in res} if res else {}
        healthy = all(v in ("REVERTED_HEALTHY", "GATED_HEALTHY")
                      for v in verdicts.values()) if verdicts else False
        status = "HARDENED" if healthy else "SUSPECT"

        inv = value.inventory(addr, rpc, denom=denom)
        db.put(c, "INSERT OR REPLACE INTO targets VALUES(?,?,?,?,?,?,?)",
               (addr, "ethereum", size, ev["first_block"], tid, sim, db.now()))
        db.put(c, "INSERT OR REPLACE INTO inventory VALUES(?,?,?,?,?,?,?)",
               (addr, "L0", "ETH", str(bal), ev["last_block"], "walker", db.now()))
        db.put(c, "INSERT INTO findings(address,vclass,tier,confidence,status,evidence,created) VALUES(?,?,?,?,?,?,?)",
               (addr, "walker_scan", "T2", "deterministic", status,
                json.dumps({"template": tid, "sim": sim, "deposits": ev["deposits"],
                            "withdrawals": ev["withdrawals"],
                            "last_block": ev["last_block"]})[:500], db.now()))
        rows.append({"addr": addr, "size": size, "template": tid, "sim": sim,
                     "bal": bal, "notes": (bal // denom) if denom else None,
                     "dep": ev["deposits"], "wd": ev["withdrawals"],
                     "last": ev["last_block"], "status": status})

    c.close()
    rows.sort(key=lambda r: -r["bal"])
    print(f"\n{'ADDRESS':<44}{'TPL':<13}{'SIM':<6}{'BAL(ETH)':>12}{'DEP':>6}{'WD':>5}{'STATUS':>10}")
    for r in rows:
        print(f"{r['addr']:<44}{str(r['template']):<13}{r['sim']:<6}"
              f"{fmt(r['bal']):>12}{r['dep']:>6}{r['wd']:>5}{r['status']:>10}")
    total = sum(r["bal"] for r in rows)
    print(f"\n[rollup] {len(rows)} contracts pipelined | aggregate custody {fmt(total)} ETH "
          f"| suspects: {sum(1 for r in rows if r['status']=='SUSPECT')}")

if __name__ == "__main__":
    main()
