# walker.py — CLI: chunked eth_getLogs sweep w/ checkpoint/resume + progress
# Usage:
#   python walker.py --chain sepolia --start 0 --count 2000
#   python walker.py --chain ethereum --start 25700000 --count 5000
#   python walker.py --chain sepolia --count 2000 --resume      (from checkpoint)
#   python walker.py --chain ethereum --count 0 --latest N      (trailing window)
import sys, os, argparse, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db
from core.walker import Walker, chain_by

KNOWN_POOLS = {  # mainnet reference targets for deposit-activity reporting
    "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc": "TC 0.1 ETH pool",
    "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936": "TC 1 ETH pool",
}


def main():
    ap = argparse.ArgumentParser(
        description="Chunked Deposit/Withdrawal event sweep with checkpoints")
    ap.add_argument("--chain", default="sepolia",
                    help="chain name or id (sepolia | ethereum | 1 | 11155111)")
    ap.add_argument("--start", type=int, default=None,
                    help="start block (default: latest-count, or 0 for --count-from-zero)")
    ap.add_argument("--count", type=int, default=2000,
                    help="number of blocks to sweep")
    ap.add_argument("--chunk", type=int, default=None,
                    help="initial chunk size in blocks (default from config)")
    ap.add_argument("--resume", action="store_true",
                    help="resume from last checkpoint instead of --start")
    ap.add_argument("--reset", action="store_true",
                    help="ignore + clear checkpoint, sweep from --start")
    ap.add_argument("--latest", type=int, default=None,
                    metavar="N",
                    help="sweep the trailing N blocks (overrides --start/--count)")
    ap.add_argument("--top", type=int, default=15,
                    help="how many top emitters to print")
    args = ap.parse_args()

    cid, name, url, topics, seed = chain_by(args.chain)
    db.init()

    w = Walker(cid, name, rpc_url=url)
    latest = w.rpc.block_number()

    if args.latest is not None:
        start, count = max(0, latest - args.latest), args.latest
    else:
        count = args.count
        start = args.start if args.start is not None else max(0, latest - count)

    if args.reset:
        from core.state import state
        state.checkpoint(cid, 0, 0, status="fresh")
        print(f"[reset] cleared checkpoint for chain {cid}")

    print(f"[walker] chain={name}({cid}) endpoint={w.rpc.fleet[0]}")
    print(f"[walker] fleet={len(w.rpc.fleet)} endpoints, latest={latest}")
    print(f"[walker] sweep [{start} .. {start+count-1}] count={count} "
          f"chunk={args.chunk or 'auto'} resume={args.resume}")
    for tname, topic in w.event_topics.items():
        print(f"[walker] topic {tname} = {topic}")

    agg, meta = w.run(start, count, chunk=args.chunk,
                      resume_from_state=args.resume)

    deps = sum(v["deposits"] for v in agg.values())
    wds = sum(v["withdrawals"] for v in agg.values())
    print(f"\n[done] {name} [{meta['from']}..{meta['to']}] in {meta['seconds']}s")
    print(f"[done] chunks={meta['stats']['chunks']} logs={meta['stats']['logs']} "
          f"halvings={meta['stats']['retries']} skipped_windows={meta['stats']['skipped_windows']}")
    print(f"[done] {len(agg)} emitters | {deps} deposits | {wds} withdrawals")

    hits = [a for a in agg if a in KNOWN_POOLS]
    if hits:
        for a in hits:
            print(f"[ref] {KNOWN_POOLS[a]} {a}: "
                  f"{agg[a]['deposits']} deposits, {agg[a]['withdrawals']} withdrawals")
    elif cid == 1:
        print("[ref] no known Tornado pool activity in this range")

    top = sorted(agg.items(), key=lambda kv: -(kv[1]["deposits"] + kv[1]["withdrawals"]))
    print(f"\n{'ADDRESS':<44}{'DEP':>6}{'WD':>6}  RANGE")
    for a, e in top[:args.top]:
        print(f"{a:<44}{e['deposits']:>6}{e['withdrawals']:>6}  "
              f"[{e['first_block']}..{e['last_block']}]")
    if len(top) > args.top:
        print(f"... +{len(top)-args.top} more (see emitters table in veritas.db)")


if __name__ == "__main__":
    main()
