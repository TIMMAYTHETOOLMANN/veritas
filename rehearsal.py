# VERITAS T3 rehearsal.py — rehearsal lab: archive-replay + anvil fork ($0, eth_call only)
#
# Two rehearsal paths, both READ-ONLY (eth_call only, never a transaction):
#   1. archive-replay: replay the T2 probe battery against historical state via
#      archive RPC (eth_call with block param N replays state at that block).
#      No local node needed. https://eth.drpc.org is the default archive RPC.
#   2. anvil fork: launch anvil --fork-url ... --fork-block-number N, run the
#      battery against 127.0.0.1:8545, report, kill anvil.
#
# Usage:
#   python rehearsal.py --target 0xADDR --battery replay --block 25000000
#   python rehearsal.py --target 0xADDR --battery replay --block latest
#   python rehearsal.py --target 0xADDR --battery fork   --block 25000000
#   python rehearsal.py --target 0xADDR --battery fork   --block latest
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db
from core import probes
from core.rpc import RPC, uint
from core.selectors import selectors_map

ARCHIVE_RPC = "https://eth.drpc.org"                # archive-depth eth_call (free tier)
# Fork sources — must serve ARCHIVE state at the fork block. anvil round-robins
# across multiple --fork-url flags, which spreads the bursty genesis fetch so no
# single free endpoint rate-limits (publicnode 403s archive: "personal token";
# drpc free plan 408s on the parallel genesis fetch but is fine for serial
# eth_call). CAVEAT (observed 2026-08-17): eth.merkle.io intermittently
# Cloudflare-1015-bans burst traffic — a 429 on anvil's boot-time chain-id fetch
# is FATAL for the whole launch, so merkle must never be the only/primary URL.
# run_fork() therefore retries: all endpoints together, then each one alone.
FORK_RPCS = [
    "https://gateway.tenderly.co/public/mainnet",
    "https://eth.merkle.io",
]

# ---- anvil discovery -------------------------------------------------

def find_anvil(explicit=None):
    """Return path to anvil.exe if found, else None. Search order:
    0. explicit --anvil-path
    1. VERITAS/tools/anvil.exe
    2. %USERPROFILE%\\.foundry\\anvil.exe
    3. PATH (shutil.which)"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        explicit,
        os.path.join(here, "tools", "anvil.exe"),
        os.path.join(os.path.expanduser("~"), ".foundry", "anvil.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return shutil.which("anvil")


def resolve_block(rpc, block_arg):
    """Resolve 'latest'/'pending'/int-string to (label, hex_block_for_eth_call)."""
    block_arg = str(block_arg).strip()
    if block_arg.lower() in ("latest", "pending", "earliest", "safe", "finalized"):
        return block_arg.lower(), block_arg.lower()
    try:
        n = int(block_arg)
    except ValueError:
        raise ValueError(
            f"invalid --block '{block_arg}': expected an integer block number "
            f"or one of latest/pending/earliest/safe/finalized")
    return str(n), hex(n)  # eth_call accepts either; hex is unambiguous


def run_rehearsal(rpc_url, address, block_arg, battery_name, block_label):
    """Execute T2 battery at a given block against rpc_url. Returns (results, meta)."""
    rpc = RPC(rpc_url, timeout=60, retries=3)
    label, blk = resolve_block(rpc, block_arg)
    # chain + block sanity
    chain_id = uint(rpc.call("eth_chainId", []))
    head = uint(rpc.call("eth_blockNumber", []))
    # Code presence at this block — catches archive gaps (code empty => wrong era).
    code = rpc.get_code(address, blk)
    meta = {
        "rpc": rpc_url, "chain_id": chain_id, "head_block": head,
        "rehearsal_block": label, "code_size": (len(code) // 2 - 1) if code.startswith("0x") else 0,
        "battery": battery_name,
    }
    if meta["code_size"] == 0:
        return None, meta  # no code at this block — archive gap or pre-deploy

    # Re-run T2 battery with a block-pinned eth_call shim: probes.py calls
    # rpc.eth_call(to, data) with default block "latest"; we pass a wrapper
    # that pins every call to the rehearsal block.
    class PinnedRPC:
        def __init__(self, inner, block):
            self.inner, self.block = inner, block
        def eth_call(self, to, data, block=None):
            return self.inner.eth_call(to, data, block or self.block)
        def __getattr__(self, name):
            return getattr(self.inner, name)

    pinned = PinnedRPC(rpc, blk)
    results = probes.run_battery(pinned, address, template_id="t3_rehearsal")
    return results, meta


def persist(c, address, battery, results, meta):
    ts = db.now()
    for r in results or []:
        c.execute(
            "INSERT INTO probes(address, battery, probe, call_data, result, verdict, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (address, battery, r.get("probe", "?"), "", json.dumps(r), r.get("verdict", "?"), ts),
        )
    c.commit()


def report(results, meta):
    print(f"[t3] rpc={meta['rpc']} chain={meta['chain_id']} head={meta['head_block']}")
    print(f"[t3] rehearsal block={meta['rehearsal_block']} code={meta['code_size']}B battery={meta['battery']}")
    if results is None:
        print("[t3] NO CODE at rehearsal block (pre-deploy or archive gap) — no probes run")
        return
    for r in results:
        extra = ""
        if "raw" in r:
            extra = f" raw={r['raw']}"
        if "results" in r:
            extra = f" variants={r['results']}"
        print(f"[t3] {r['probe']}: {r['verdict']}{extra}")


# ---- anvil fork path --------------------------------------------------

def anvil_ready(url, timeout_s=180):
    """Poll http JSON-RPC until eth_blockNumber answers. Returns bool."""
    rpc = RPC(url, timeout=5, retries=1)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            rpc.call("eth_blockNumber", [])
            return True
        except Exception:
            time.sleep(1.0)
    return False


def _kill_tree(proc):
    """Terminate anvil and any children. Windows: taskkill /F /T; fallback kill()."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _free_port():
    """Pick an unused TCP port so a stale anvil on 8545 can never shadow us."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _launch_attempt(anvil, port, pinned, urls):
    """One anvil launch attempt with this URL set. Returns (proc|None, host|None)."""
    cmd = [anvil, "--port", str(port)]
    for u in urls:
        cmd += ["--fork-url", u]
    if pinned is not None:
        cmd += ["--fork-block-number", str(pinned)]
    print(f"[t3] launching: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    host = f"http://127.0.0.1:{port}"
    if anvil_ready(host, timeout_s=60):
        return proc, host
    _kill_tree(proc)  # never leave a dead/hung attempt behind
    return None, None


def run_fork(address, block_arg, anvil_path=None):
    anvil = find_anvil(anvil_path)
    if not anvil:
        print("[t3] anvil.exe NOT FOUND — cannot run fork rehearsal.")
        print("[t3] Archive-replay is the $0 fallback and is ALWAYS available:")
        print(f"     python rehearsal.py --target {address} --battery replay --block {block_arg}")
        print("[t3] To enable fork mode: install foundry win32 zip, extract anvil.exe to")
        print("     VERITAS/tools/anvil.exe or %USERPROFILE%\\.foundry\\anvil.exe")
        return 1

    try:
        label, blk = resolve_block(None, block_arg)
    except ValueError as e:
        print(f"[t3] fork rehearsal aborted: {e}")
        return 2
    pinned = None if label in ("latest",) else int(label)

    # Retry ladder: all endpoints together first (anvil round-robins across
    # --fork-url flags), then each endpoint alone. A single free endpoint that
    # 429s/Cloudflare-bans at boot must not kill the whole rehearsal.
    attempts = [FORK_RPCS] + [[u] for u in FORK_RPCS]
    proc = None
    host = None
    for i, urls in enumerate(attempts):
        if i > 0:
            print(f"[t3] retry {i}/{len(attempts) - 1} with fallback URL set: {urls}")
        proc, host = _launch_attempt(anvil, _free_port(), pinned, urls)
        if proc is not None:
            break
    if proc is None:
        print("[t3] anvil did not become ready in 180s — aborting fork rehearsal")
        return 2
    try:
        print(f"[t3] anvil ready at {host} (pid {proc.pid})")
        # Verify the fork head matches the pinned block — guards against a
        # stale anvil or a silently-degraded fork serving the wrong state.
        head = uint(RPC(host, timeout=10, retries=1).call("eth_blockNumber", []))
        if pinned is not None and head != pinned:
            print(f"[t3] FORK HEAD MISMATCH: expected {pinned}, fork head {head} — aborting")
            return 3
        results, meta = run_rehearsal(host, address, block_arg, "fork", label)
        report(results, meta)
        c = db.conn(); db.init()
        persist(c, address, "fork", results, meta)
        c.close()
        return 0
    finally:
        _kill_tree(proc)
        print(f"[t3] anvil terminated (pid {proc.pid})")


# ---- CLI ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="VERITAS T3 rehearsal lab ($0, eth_call only)")
    ap.add_argument("--target", required=True, help="contract address")
    ap.add_argument("--battery", choices=["replay", "fork"], default="replay",
                    help="replay=archive eth_call at block; fork=local anvil fork")
    ap.add_argument("--block", default="latest",
                    help="block number, or 'latest'. fork mode pins fork-block-number")
    ap.add_argument("--rpc", default=None, help="override archive RPC (replay mode)")
    ap.add_argument("--anvil-path", default=None, help="explicit path to anvil.exe")
    args = ap.parse_args()

    # Canonical lowercase key — matches targets/report/lineage normalization
    # (siblings do the same); probe rows previously stored the checksummed
    # spelling, splitting the key space in the DB.
    address = args.target.strip().lower()
    db.init()
    c = db.conn()

    if args.battery == "fork":
        rc = run_fork(address, args.block, anvil_path=args.anvil_path)
        c.close()
        sys.exit(rc)

    rpc_url = args.rpc or ARCHIVE_RPC
    print(f"[t3] archive-replay rehearsal: {address} @ block {args.block} via {rpc_url}")
    try:
        results, meta = run_rehearsal(rpc_url, address, args.block, "replay", str(args.block))
    except Exception as e:
        print(f"[t3] archive-replay FAILED: {e}")
        c.close()
        sys.exit(2)
    report(results, meta)
    persist(c, address, "replay", results, meta)
    c.close()
    print("[t3] persisted to probes table (veritas.db)")


if __name__ == "__main__":
    main()
