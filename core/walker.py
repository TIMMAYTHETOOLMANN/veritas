# core/walker.py — event-graph sweep walker with fleet rotation, checkpoint/resume
# Sweeps eth_getLogs for full 32-byte Deposit/Withdrawal topics, aggregates
# emitter addresses (discovery.py-style), checkpoints every chunk via state.py.
import time
from core import db
from core.config import config
from core.state import state


# Error text that means "this endpoint refuses unauthenticated traffic":
# retrying it is pointless — it must leave the rotation for the whole run.
AUTH_ERROR_MARKERS = ("unauthorized", "api key", "apikey", "authenticate")


def _is_auth_error(exc):
    """True if an RPC error smells like a key wall / auth refusal."""
    msg = str(exc).lower()
    return any(m in msg for m in AUTH_ERROR_MARKERS)


class AllEndpointsDead(RuntimeError):
    """Every endpoint in the fleet was marked dead — abort, never skip."""


class WalkerRPC:
    """JSON-RPC client with retry + same-chain endpoint fleet rotation.

    Hardened auth policy: an endpoint whose error message contains
    'Unauthorized' / 'API key' / 'authenticate' is marked DEAD for the
    rest of the run and removed from rotation immediately. It never
    burns retries again and can never cause a window skip. When the
    last live endpoint dies, AllEndpointsDead aborts the sweep (the
    checkpoint stays intact) instead of silently skipping windows."""

    def __init__(self, chain_id, rpc_url=None, fleet=None):
        from core.rpc import RPC
        self.fleet = list(fleet or config.rpc_fleet.get(chain_id) or [rpc_url])
        if rpc_url and rpc_url not in self.fleet:
            self.fleet.insert(0, rpc_url)
        self.idx = 0
        self.dead = 0
        self.rotations = 0
        self._spun = 0
        self.client = RPC(self.fleet[0], timeout=config.rpc_timeout,
                          retries=config.max_retries)

    def rotate(self):
        self.idx = (self.idx + 1) % len(self.fleet)
        self.rotations += 1
        from core.rpc import RPC
        self.client = RPC(self.fleet[self.idx], timeout=config.rpc_timeout,
                          retries=config.max_retries)

    def _mark_dead(self, reason):
        """Remove the current endpoint from the fleet for the rest of the
        run and point the client at the next live one. Raises
        AllEndpointsDead if nothing live remains."""
        url = self.fleet.pop(self.idx)
        self.dead += 1
        print(f"[rpc] endpoint DEAD for this run ({reason}): {url} | "
              f"{len(self.fleet)} live remaining", flush=True)
        if not self.fleet:
            raise AllEndpointsDead(
                f"all RPC endpoints for this chain are dead "
                f"(last reason: {reason}) — aborting sweep; checkpoint "
                f"is preserved, fix the fleet in core/config.py")
        self.idx = self.idx % len(self.fleet)
        from core.rpc import RPC
        self.client = RPC(self.fleet[self.idx], timeout=config.rpc_timeout,
                          retries=config.max_retries)

    def call(self, method, params):
        """Call with rotation. A non-auth failure gets max_retries on one
        endpoint (inside RPC), then rotates; after every live endpoint
        has failed once, raise. An auth failure marks the endpoint dead
        for the run and immediately retries on the next live one."""
        last = None
        self._spun = 0
        while self.fleet:
            try:
                out = self.client.call(method, params)
                self._spun = 0
                return out
            except Exception as e:
                last = e
                self._spun += 1
                if _is_auth_error(e):
                    self._spun = 0           # fresh budget on smaller fleet
                    self._mark_dead(str(e))  # raises AllEndpointsDead if empty
                elif self._spun >= len(self.fleet):
                    raise last
                else:
                    self.rotate()
        raise last

    def block_number(self):
        return int(self.call("eth_blockNumber", []), 16)

    def eth_call(self, *a, **k):
        return self.client.eth_call(*a, **k)


def _fmt_agg(agg):
    """Aggregate dict -> compact display string."""
    deps = sum(v["deposits"] for v in agg.values())
    wds = sum(v["withdrawals"] for v in agg.values())
    return f"{len(agg)} emitters / {deps} deposits / {wds} withdrawals"


def merge_agg(dst, add):
    """Merge aggregate {addr: {deposits,withdrawals,first_block,last_block}}."""
    for addr, e in add.items():
        d = dst.setdefault(addr, {"deposits": 0, "withdrawals": 0,
                                  "first_block": e["first_block"],
                                  "last_block": e["last_block"]})
        d["deposits"] += e["deposits"]
        d["withdrawals"] += e["withdrawals"]
        d["first_block"] = min(d["first_block"], e["first_block"])
        d["last_block"] = max(d["last_block"], e["last_block"])
    return dst


class Walker:
    """Chunked eth_getLogs sweep with adaptive range, checkpoint, progress."""

    def __init__(self, chain_id, chain_name, rpc_url=None, fleet=None):
        self.chain_id = chain_id
        self.chain_name = chain_name
        self.rpc = WalkerRPC(chain_id, rpc_url=rpc_url, fleet=fleet)
        from core.config import EVENT_TOPICS
        self.event_topics = dict(EVENT_TOPICS)
        self.stats = {"chunks": 0, "retries": 0, "logs": 0,
                      "skipped_windows": 0, "endpoints_rotated": 0}

    # -- one getLogs chunk for one topic, adaptive retry via WalkerRPC -----
    def fetch_chunk(self, topic, lo, hi):
        return self.rpc.call("eth_getLogs", [{
            "fromBlock": hex(lo), "toBlock": hex(hi),
            "topics": [topic],
        }])

    def scan_chunk(self, lo, hi):
        """Scan [lo, hi] for all topics. Returns aggregate dict for the chunk."""
        agg = {}
        for name, topic in self.event_topics.items():
            logs = self.fetch_chunk(topic, lo, hi)
            self.stats["logs"] += len(logs)
            for lg in logs:
                a = lg["address"].lower()
                b = int(lg["blockNumber"], 16)
                e = agg.setdefault(a, {"deposits": 0, "withdrawals": 0,
                                       "first_block": b, "last_block": b})
                if name == "Deposit":
                    e["deposits"] += 1
                else:
                    e["withdrawals"] += 1
                e["first_block"] = min(e["first_block"], b)
                e["last_block"] = max(e["last_block"], b)
        return agg

    def persist_emitters(self, agg):
        c = db.conn()
        try:
            for addr, e in agg.items():
                row = c.execute(
                    "SELECT deposits, withdrawals, first_block, last_block "
                    "FROM emitters WHERE chain_id=? AND address=?",
                    (self.chain_id, addr)).fetchone()
                if row:
                    e = {"deposits": row["deposits"] + e["deposits"],
                         "withdrawals": row["withdrawals"] + e["withdrawals"],
                         "first_block": min(row["first_block"], e["first_block"]),
                         "last_block": max(row["last_block"], e["last_block"])}
                c.execute(
                    "INSERT OR REPLACE INTO emitters"
                    "(chain_id,address,deposits,withdrawals,first_block,last_block,ts) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (self.chain_id, addr, e["deposits"], e["withdrawals"],
                     e["first_block"], e["last_block"], db.now()))
            c.commit()
        finally:
            c.close()

    def run(self, start_block, count, chunk=None, checkpoint_every=None,
            resume_from_state=False, progress_every=30.0, on_chunk=None):
        """Sweep [start_block, start_block + count - 1].

        checkpoint_every: persist a walker_state checkpoint every N blocks
        (defaults to the chunk size — one checkpoint per chunk).
        resume_from_state: continue from the last checkpoint instead of
        start_block (start_block then acts as the floor).
        Returns (agg, meta) where meta has block-range and stats.
        """
        end = start_block + count - 1
        if resume_from_state:
            cur, done, status = state.resume(self.chain_id)
            if cur and status == "ok":
                start_block = max(start_block, cur + 1)
                print(f"[resume] chain {self.chain_id} from checkpoint "
                      f"block {cur} ({done} processed) -> starting at {start_block}")
        chunk = chunk or config.blocks_per_page
        checkpoint_every = checkpoint_every or chunk
        agg = {}
        lo = start_block
        last_ckpt = start_block - 1
        t0 = time.time()
        t_last = t0
        processed = 0

        while lo <= end:
            hi = min(lo + chunk - 1, end)
            try:
                chunk_agg = self.scan_chunk(lo, hi)
            except AllEndpointsDead:
                # Fleet is exhausted (e.g. every endpoint key-walled):
                # persist the checkpoint + what we have, then ABORT.
                # A dead fleet must never be punished with chunk halving
                # or window skips — that is silent data loss.
                state.checkpoint(self.chain_id, lo - 1, processed)
                self.persist_emitters(agg)
                self.stats["endpoints_rotated"] = self.rpc.rotations
                self.stats["endpoints_dead"] = self.rpc.dead
                raise
            except Exception as e:
                if chunk > config.min_chunk_blocks:
                    chunk = max(config.min_chunk_blocks, chunk // 2)
                    self.stats["retries"] += 1
                    continue
                # floor chunk unrecoverable — record skip, advance
                self.stats["skipped_windows"] += 1
                print(f"[warn] skipping unrecoverable window "
                      f"[{lo}..{hi}] after error: {e}")
                lo = hi + 1
                continue
            if len(chunk_agg) >= config.log_cap and chunk > config.min_chunk_blocks:
                # response likely truncated — refetch narrower
                chunk = max(config.min_chunk_blocks, chunk // 2)
                self.stats["retries"] += 1
                continue

            merge_agg(agg, chunk_agg)
            self.stats["chunks"] += 1
            processed += (hi - lo + 1)
            lo = hi + 1
            chunk = min(config.blocks_per_page, int(chunk * 1.25) + 1)

            if hi - last_ckpt >= checkpoint_every or lo > end:
                state.checkpoint(self.chain_id, hi, processed)
                self.persist_emitters(chunk_agg)
                last_ckpt = hi
            if on_chunk:
                on_chunk(lo - 1, chunk, chunk_agg)

            now = time.time()
            if now - t_last >= progress_every or lo > end:
                pct = 100.0 * (lo - start_block) / max(1, end - start_block + 1)
                print(f"[walk] {self.chain_name} block {lo-1} "
                      f"({pct:.1f}%) chunk={chunk} logs={self.stats['logs']} "
                      f"| {_fmt_agg(agg)} | {now - t0:.1f}s", flush=True)
                t_last = now

        state.checkpoint(self.chain_id, end, processed, status="done")
        self.stats["endpoints_rotated"] = self.rpc.rotations
        self.stats["endpoints_dead"] = self.rpc.dead
        return agg, {"from": start_block, "to": end, "stats": dict(self.stats),
                     "seconds": round(time.time() - t0, 1)}


def chain_by(key):
    """Resolve chain config by name or chain id."""
    k = str(key).lower()
    for cid, name, url, topics, start in config.chains:
        if k in (name, str(cid)):
            return (cid, name, url, topics, start)
    raise SystemExit(f"[error] unknown chain '{key}' "
                     f"(known: {[c[1] for c in config.chains]})")


def walk_chain(key, start_block, count, chunk=None, resume=False, fleet=None):
    """Sweep one configured chain over [start_block, start_block+count)."""
    cid, name, url, topics, start = chain_by(key)
    db.init()
    w = Walker(cid, name, rpc_url=url, fleet=fleet)
    return w.run(start_block, count, chunk=chunk, resume_from_state=resume)


if __name__ == "__main__":
    print("library module — run walker.py (repo root) for the CLI")
