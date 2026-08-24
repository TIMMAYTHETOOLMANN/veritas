# l2_sweep.py — T0 discovery sweep on L2 chains for live privacy-pool emitters.
#
# Rationale: mainnet clone family is hardened (verifier sound, operator dead).
# The two classes where P can structurally be >0 are (a) live/unprotected admin
# gates on funded pools and (b) broken verifiers on fresh deployments. L2
# deployments are historically sloppier than mainnet — that's the hunting ground.
#
# Sweep = eth_getLogs for all configured Deposit/Withdrawal topics over the
# recent window on each L2, aggregate emitters, persist to emitters table.
# Read-only, $0. Run: python3 l2_sweep.py [blocks]
import sys, time, json
sys.path.insert(0, ".")
from core import db
db.init()
from core.walker import Walker, chain_by
from core.config import config

BLOCKS = int(sys.argv[1]) if len(sys.argv) > 1 else 300_000
CHAINS = ["arbitrum", "optimism", "base", "zksync", "scroll", "linea"]

summary = {}
for key in CHAINS:
    cid, name, url, topics, _ = chain_by(key)
    try:
        w = Walker(cid, name, rpc_url=url, fleet=config.rpc_fleet.get(cid))
        head = w.rpc.block_number()
        start = max(0, head - BLOCKS + 1)
        print(f"\n=== {name} (chain {cid}) head={head} sweep [{start}..{head}] ===", flush=True)
        agg, meta = w.run(start, BLOCKS, checkpoint_every=50_000,
                          progress_every=15.0)
        funded = [(a, e) for a, e in agg.items() if e["deposits"] > 0]
        summary[name] = {"head": head, "emitters": len(agg),
                         "deposit_active": len(funded),
                         "stats": meta["stats"]}
        for a, e in sorted(funded, key=lambda x: -x[1]["deposits"])[:20]:
            print(f"  {a}  dep={e['deposits']} wd={e['withdrawals']} "
                  f"blocks {e['first_block']}..{e['last_block']}")
    except Exception as ex:
        summary[name] = {"error": str(ex)[:160]}
        print(f"[{name}] ERROR: {str(ex)[:160]}", flush=True)
    time.sleep(1.0)

print("\n" + "=" * 70)
print(json.dumps(summary, indent=1))
with open("cache/l2_sweep_summary.json", "w") as f:
    json.dump({"ts": int(time.time()), "summary": summary}, f, indent=1)
