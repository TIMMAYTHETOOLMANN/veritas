# core/t2.py — T2 deterministic probe batteries: run on T1 candidates
from core.db import conn, now, put
from core.config import config
from core import rpc as rpc_mod
from core.probes import probe_self_vk, probe_malformed_points, probe_nullifier_replay, _is_revert
from core.selectors import selectors_map
import time

def run_t2_on_candidate(chain_id, address, rpc_url=None):
    """Run the three T2 probe batteries on a single candidate address."""
    if rpc_url is None:
        # Find RPC for this chain from config
        rpc_url = None
        for cid, name, url, topics, start in config.chains:
            if cid == chain_id:
                rpc_url = url
                break
    if rpc_url is None:
        print(f"[T2] No RPC URL found for chain {chain_id}")
        return []
    rpc = rpc_mod.RPC(rpc_url, timeout=30, retries=3)
    sel = selectors_map()["verify"]  # verifyProof selector
    results = []
    
    # Probe A: self-VK zero-proof
    print(f"  [T2] Running self-VK zero-proof on {address}...")
    try:
        res = probe_self_vk(rpc, address, sel)
        results.append(res)
        print(f"    -> {res.get('verdict', 'UNKNOWN')}")
    except Exception as e:
        print(f"    -> ERROR: {e}")
        results.append({"probe": "self_vk_zero", "verdict": "ERROR", "error": str(e)[:80]})
    
    # Probe B: malformed point canonicality
    print(f"  [T2] Running malformed point canonicality on {address}...")
    try:
        res = probe_malformed_points(rpc, address, sel)
        results.append(res)
        print(f"    -> {res.get('verdict', 'UNKNOWN')}")
    except Exception as e:
        print(f"    -> ERROR: {e}")
        results.append({"probe": "malformed_points", "verdict": "ERROR", "error": str(e)[:80]})
    
    # Probe C: nullifier replay – we need a spent nullifier
    # For now, we'll skip unless we have one; we'll add a helper to fetch one later.
    # We'll leave a placeholder.
    print(f"  [T2] Skipping nullifier replay probe (need spent nullifier)")
    # We'll implement fetching a spent nullifier from logs in a separate function.
    
    # Persist all results
    c = conn()
    for res in results:
        put(c, """INSERT INTO probes 
            (address, battery, probe, call_data, result, verdict, ts)
            VALUES (?,?,?,?,?,?,?)""",
            (address,
             res.get("probe", "unknown"),
             res.get("probe", "unknown"),  # battery same as probe for now, we can refine
             res.get("raw", ""),
             res.get("raw", ""),
             res.get("verdict", "ERROR"),
             now()))
    c.commit()
    c.close()
    return results

def fetch_recent_nullifier(chain_id, pool_address, rpc_url=None):
    """Fetch a recent withdrawal nullifier from logs for the given pool.
    Returns the nullifier hex string (with 0x) or None."""
    if rpc_url is None:
        for cid, name, url, topics, start in config.chains:
            if cid == chain_id:
                rpc_url = url
                break
    if rpc_url is None:
        return None
    rpc = rpc_mod.RPC(rpc_url, timeout=30, retries=3)
    # Withdrawal topic0: keccak256("Withdrawal(address,bytes32,address,uint256)")
    # We'll compute it
    from core.selectors import kec256
    WITHDRAWAL_TOPIC = "0x" + kec256(b"Withdrawal(address,bytes32,address,uint256)").hex()
    # Look back 5000 blocks for a withdrawal log
    latest = rpc.call("eth_blockNumber", [])
    latest_block = int(latest, 16)
    from_block = max(0, latest_block - 5000)
    # Use eth_getLogs with address and topic0
    try:
        logs = rpc.call("eth_getLogs", [{
            "address": pool_address,
            "fromBlock": hex(from_block),
            "toBlock": hex(latest_block),
            "topics": [WITHDRAWAL_TOPIC]
        }])
    except Exception as e:
        print(f"[T2] Failed to fetch withdrawal logs: {e}")
        return None
    if not logs:
        return None
    # Take the most recent log (highest block number)
    logs_sorted = sorted(logs, key=lambda lg: int(lg["blockNumber"], 16), reverse=True)
    for lg in logs_sorted:
        topics = lg.get("topics", [])
        if len(topics) >= 3:
            # topic2 is the nullifier (bytes32)
            nullifier = "0x" + topics[2].lower()
            # Ensure it's 32 bytes
            if len(nullifier) == 66:  # 0x + 64 hex
                return nullifier
    return None

def run_t2_with_nullifier(chain_id, address, rpc_url=None):
    """Run T2 probes including nullifier replay using a freshly fetched nullifier."""
    if rpc_url is None:
        for cid, name, url, topics, start in config.chains:
            if cid == chain_id:
                rpc_url = url
                break
    if rpc_url is None:
        print(f"[T2] No RPC URL for chain {chain_id}")
        return []
    rpc = rpc_mod.RPC(rpc_url, timeout=30, retries=3)
    sel = selectors_map()["verify"]
    nullifier_hex = fetch_recent_nullifier(chain_id, address, rpc_url)
    if nullifier_hex is None:
        print(f"[T2] Warning: could not fetch a recent nullifier for {address}; skipping nullifier probe")
        nullifier_hex = "0x" + "00"*32  # fallback to zero (may not be spent)
    results = []
    
    # Probe A: self-VK zero-proof
    print(f"  [T2] Running self-VK zero-proof on {address}...")
    try:
        res = probe_self_vk(rpc, address, sel)
        results.append(res)
        print(f"    -> {res.get('verdict', 'UNKNOWN')}")
    except Exception as e:
        print(f"    -> ERROR: {e}")
        results.append({"probe": "self_vk_zero", "verdict": "ERROR", "error": str(e)[:80]})
    
    # Probe B: malformed point canonicality
    print(f"  [T2] Running malformed point canonicality on {address}...")
    try:
        res = probe_malformed_points(rpc, address, sel)
        results.append(res)
        print(f"    -> {res.get('verdict', 'UNKNOWN')}")
    except Exception as e:
        print(f"    -> ERROR: {e}")
        results.append({"probe": "malformed_points", "verdict": "ERROR", "error": str(e)[:80]})
    
    # Probe C: nullifier replay
    print(f"  [T2] Running nullifier replay on {address} with nullifier {nullifier_hex[:10]}...")
    try:
        res = probe_nullifier_replay(rpc, address, nullifier_hex)
        results.append(res)
        print(f"    -> {res.get('verdict', 'UNKNOWN')} (spent={res.get('spent', '?')})")
    except Exception as e:
        print(f"    -> ERROR: {e}")
        results.append({"probe": "nullifier_replay", "verdict": "ERROR", "error": str(e)[:80]})
    
    # Persist results
    c = conn()
    for res in results:
        put(c, """INSERT INTO probes 
            (address, battery, probe, call_data, result, verdict, ts)
            VALUES (?,?,?,?,?,?,?)""",
            (address,
             res.get("probe", "unknown"),
             res.get("probe", "unknown"),
             res.get("raw", ""),
             res.get("raw", ""),
             res.get("verdict", "ERROR"),
             now()))
    c.commit()
    c.close()
    return results

def run_t2_on_all_t1(min_similarity=0.0, max_targets=None):
    """Run T2 probes on all T1 candidates (status='t1_complete')."""
    c = conn()
    query = """
        SELECT chain_id, address, similarity, template_id
        FROM targets
        WHERE status='t1_complete' AND similarity >= ?
        ORDER BY similarity DESC
    """
    params = [min_similarity]
    if max_targets:
        query += " LIMIT ?"
        params.append(max_targets)
    rows = c.execute(query, params).fetchall()
    c.close()
    
    print(f"[T2] Running probes on {len(rows)} T1 candidates (min_sim={min_similarity})")
    all_results = []
    for row in rows:
        chain_id = row["chain_id"]
        address = row["address"]
        print(f"[{row['similarity']:.3f}] {address} (chain {chain_id}, template {row['template_id']})")
        results = run_t2_with_nullifier(chain_id, address)
        all_results.extend(results)
        # brief pause to be nice to RPC
        time.sleep(0.2)
    return all_results

def get_latest_probes(limit=20):
    """Retrieve the most recent probe results from DB."""
    c = conn()
    rows = c.execute("""
        SELECT address, battery, probe, verdict, ts
        FROM probes
        ORDER BY ts DESC
        LIMIT ?
    """, (limit,)).fetchall()
    c.close()
    return [dict(row) for row in rows]

if __name__ == "__main__":
    print("[T2] Starting probe batteries on T1 candidates...")
    results = run_t2_on_all_t1(min_similarity=0.0, max_targets=10)
    print(f"[T2] Completed {len(results)} probe executions")
    latest = get_latest_probes(limit=10)
    print("[T2] Latest probe results:")
    for r in latest:
        print(f"  {r['address']} {r['probe']} -> {r['verdict']}")