# core/walker.py — event-graph walker, fingerprint, dedup (integrity-first)
import hashlib, time, json, traceback
from core.db import conn, now, put
from core.config import config
from core.cache import cache
from core.state import state
from core.selectors import scan_code, match_template, selectors_map
from core.value import uint
from core import rpc as rpc_mod

# --- minimal RPC client with backoff + fleet rotation ---
class WalkerRPC:
    def __init__(self):
        self.queue = list(config.rpc_endpoints)
        self._rotate()
    def _rotate(self):
        self.client = rpc_mod.RPC(self.queue[0],
                                   timeout=20, retries=config.max_retries)
    def next(self):
        self.queue.append(self.queue.pop(0))
        self._rotate()
    def eth_call(self, *a, **k):
        try:
            return self.client.eth_call(*a, **k)
        except Exception as e:
            self.next()
            raise

# --- Walker class ---
class Walker:
    def __init__(self, chain_id, chain_name, rpc_url, event_topics, start_block):
        self.chain_id = chain_id
        self.chain_name = chain_name
        self.rpc_url = rpc_url
        self.event_topics = event_topics
        self.start_block = start_block
        self.client = rpc_mod.RPC(rpc_url, timeout=20, retries=config.max_retries)
        self.sm = selectors_map()
        self.stats = {"candidates": 0, "scanned": 0, "skipped": 0, "errors": 0,
                      "bytecode_fetches": 0, "deduped": 0}

    def _format_log_topic(self, topic_hex):
        return "0x" + topic_hex[2:].lower()

    def scan_cursor(self, from_block, to_block):
        """Scan logs for event signatures in [from_block, to_block], yield unique contract addresses."""
        seen = set()
        for topic_key, sel_hex in self.event_topics.items():
            topic = self._format_log_topic(sel_hex)
            try:
                logs = self.client.call("eth_getLogs", [
                    {"fromBlock": hex(from_block), "toBlock": hex(to_block),
                     "topics": [topic], "address": ""}
                ])
            except Exception:
                self.stats["errors"] += 1
                continue
            if not logs:
                continue
            for log in logs:
                addr = log.get("address", "")
                if not addr or addr in seen:
                    continue
                seen.add(addr)
                yield addr
        self.stats["scanned"] += 1

    def analyze_address(self, addr):
        """Fingerprint an address: bytecode, select

ors present, template sim. Return normalized candidate dict or None."""
        # skip zero/non-contract code
        code = self.client.get_code(addr)
        if not code or code in ("0x", "0x0"):
            self.stats["skipped"] += 1
            return None
        bc_len = len(code) - 2 if isinstance(code, str) else len(code)
        if bc_len < config.min_bytecode_size:
            self.stats["skipped"] += 1
            return None
        # deduplicate on bytecode hash
        bc_hash = hashlib.sha256(code.encode() if isinstance(code, str) else code).hexdigest()
        c = conn()
        dup = c.execute("SELECT COUNT(*) FROM targets WHERE bc_hash=?", (bc_hash,)).fetchone()[0]
        if dup > 0:
            self.stats["deduped"] += 1
            c.close()
            return None
        c.close()
        # fingerprint
        present = scan_code(code)
        tid, sim = match_template(present)
        if sim < config.template_sim_floor:
            self.stats["skipped"] += 1
            return None
        # config reads (denom, root, levels)
        denom = uint(self.client.eth_call(addr, self.sm["denom"]))
        root = self.client.eth_call(addr, self.sm["getroot"])
        levels = uint(self.client.eth_call(addr, self.sm["levels"]))
        return {
            "address": addr,
            "chain_id": self.chain_id,
            "chain_name": self.chain_name,
            "code_size": bc_len,
            "bytecode_hash": bc_hash,
            "template_id": tid,
            "similarity": round(sim, 3),
            "denom": str(denom) if denom else None,
            "root": str(root)[:32] if root else None,
            "levels": levels,
            "deposit_sel": present["deposit"],
            "withdraw_sel": present["withdraw"],
            "nullif_sel": present["nullif"],
            "setver_sel": present["setver"],
            "ts": now(),
            "status": "candidate",
        }

    def run(self, from_block=None, to_block=None):
        """Walk [from_block, to_block], persist candidates, return count."""
        cur = from_block if from_block is not None else self.start_block
        end = to_block if to_block is not None else cur + config.blocks_per_page
        c = conn()
        processed_count = 0
        for addr in self.scan_cursor(cur, end):
            cand = self.analyze_address(addr)
            if cand is None:
                continue
            # persist
            put(c, "INSERT OR REPLACE INTO targets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cand["address"], cand["chain_name"], cand["code_size"],
                 cur, cand["template_id"], cand["similarity"],
                 cand["denom"], cand["root"], cand["levels"],
                 cand["deposit_sel"], cand["withdraw_sel"],
                 cand["nullif_sel"], cand["setver_sel"],
                 cand["bytecode_hash"], cand["ts"], cand["status"]))
            put(c, "INSERT OR REPLACE INTO walker_state(chain_id,cur_block,processed_count,status,ts) "
                 "VALUES(?,?,?,?,?)",
                 (self.chain_id, cur, processed_count, "ok", now()))
            self.stats["candidates"] += 1
            processed_count += 1
            if self.stats["candidates"] >= config.max_candidates_per_chain:
                break
        c.close()
        state.checkpoint(self.chain_id, cur, processed_count)
        return processed_count

# --- Event index walker -> pull deposit/withdrawal/nullifier events from chain ---
def event_index_walk(rpc, chain_id, start_from, topics, max_pages=None):
    """Paginated log fetch. Yields (address, event_key, block). For active chain scanning."""
    page = 0
    cursor = start_from
    while max_pages is None or page < max_pages:
        page += 1
        try:
            logs = rpc.call("eth_getLogs", [
                {"fromBlock": hex(cursor), "toBlock": hex(cursor + config.blocks_per_page - 1),
                 "topics": [], "address": ""}
            ])
        except Exception:
            yield None, None, None
            page -= 1  # retry on failure
            continue
        if not logs:
            cursor += config.blocks_per_page
            continue
        for log in logs:
            addr = log.get("address", "")
            if not addr:
                continue
            topics_log = log.get("topics", [])
            for key, sel in topics.items():
                if topics[topic_key] in topics_log:  # matching topic present
                    yield addr, key, log.get("blockNumber")
        cursor += config.blocks_per_page
    return

# --- Harness ---
def walk_chain(chain_id, chain_name, rpc_url, event_topics, start_block=0, end_block=None):
    """Run walker on one chain. Returns count of candidates found."""
    w = Walker(chain_id, chain_name, rpc_url, event_topics, start_block)
    return w.run(from_block=start_block, to_block=end_block)

def walk_all(chains=None):
    """Walk all configured chains, return totals."""
    if chains is None:
        chains = config.chains
    totals = {}
    for cid, name, url, topics, start in chains:
        try:
            n = walk_chain(cid, name, url, topics, start)
            totals[name] = n
        except Exception:
            totals[name] = 0
            traceback.print_exc()
    return totals

def report():
    """Read candidate pool from DB and return summary."""
    c = conn()
    rows = c.execute("""
        SELECT address, chain, template_id, similarity, denom, code_size,
               deposit_sel, withdraw_sel, nullif_sel, setver_sel
        FROM targets WHERE status='candidate' ORDER BY similarity DESC
    """).fetchall()
    c.close()
    return [dict(r) for r in rows]

def main():
    """CLI: walk configured chains, print candidates."""
    print("[walker] initializing fortified walker...")
    c = conn()
    # self-create schema — idempotent, survives old validate.py DB
    c.executescript("""
        DROP TABLE IF EXISTS targets;
        DROP TABLE IF EXISTS walker_state;
        CREATE TABLE IF NOT EXISTS targets(
            address TEXT PRIMARY KEY, chain TEXT, code_size INTEGER, cur_block INTEGER,
            template_id TEXT, similarity REAL, denom TEXT, root TEXT,
            levels INTEGER, deposit_sel TEXT, withdraw_sel TEXT,
            nullif_sel TEXT, setver_sel TEXT, bytecode_hash TEXT,
            ts INTEGER, status TEXT);
        CREATE TABLE IF NOT EXISTS walker_state(
            chain_id INTEGER PRIMARY KEY, cur_block INTEGER,
            processed_count INTEGER, status TEXT, ts INTEGER);
    """)
    c.commit()
    c.close()
    totals = walk_all()
    print(f"[walker] done: {totals}")
    pool = report()
    print(f"[walker] candidates: {len(pool)}")
    for p in pool[:5]:
        print(f"  {p['address'][:10]}... {p['chain']} sim={p['similarity']} "
              f"template={p['template_id']} denom={p['denom']}")
    if len(pool) > 5:
        print(f"  ... +{len(pool)-5} more")

if __name__ == "__main__":
    main()
