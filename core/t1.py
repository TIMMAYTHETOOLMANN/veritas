# core/t1.py — T1 structural analysis: bytecode fetch, selector scan, template match, dedup
from core.db import conn, now, put
from core.selectors import scan_code, match_template, selectors_map
from core.config import config
from core import rpc as rpc_mod
from core.cache import cache
import hashlib, time, json

def analyze_emitter(chain_id, address, rpc_url=None):
    """Fetch bytecode, scan selectors, match template, return candidate dict."""
    # Skip if we've already analyzed this address recently (cache)
    c = conn()
    row = c.execute(
        "SELECT bytecode_hash, analyzed_ts FROM targets WHERE address=? AND chain_id=?",
        (address.lower(), chain_id)
    ).fetchone()
    if row:
        # Check if bytecode changed (re-deploy/upgrade) - if not, skip re-analysis
        stored_hash = row["bytecode_hash"]
        c.close()
        # Fetch current bytecode
        rpc = rpc_mod.RPC(rpc_url or "https://ethereum-rpc.publicnode.com", timeout=20, retries=3)
        current_code = rpc.get_code(address)
        if not current_code or current_code in ("0x", "0x0"):
            return None
        current_hash = hashlib.sha256(
            current_code.encode() if isinstance(current_code, str) else current_code
        ).hexdigest()
        if stored_hash == current_hash:
            # Same bytecode, skip re-analysis unless forced
            return None
    
    # Fetch bytecode
    rpc = rpc_mod.RPC(rpc_url or "https://ethereum-rpc.publicnode.com", timeout=20, retries=3)
    code = rpc.get_code(address)
    if not code or code in ("0x", "0x0"):
        return None
    
    # Compute hash for dedup
    bc_hash = hashlib.sha256(
        code.encode() if isinstance(code, str) else code
    ).hexdigest()
    
    # Check if we've seen this exact bytecode before (global dedup)
    c = conn()
    dup = c.execute(
        "SELECT COUNT(*) FROM targets WHERE bytecode_hash=?", (bc_hash,)
    ).fetchone()[0]
    c.close()
    if dup > 0:
        return None  # already analyzed
    
    # Scan for selectors
    present = scan_code(code)
    
    # Template matching
    tid, sim = match_template(present)
    if sim < config.template_sim_floor:
        return None  # below signal floor
    
    # Read storage slots for config (denom, root, levels, etc.)
    try:
        denom = rpc.eth_call(address, selectors_map()["denom"])
    except Exception:
        denom = None
    try:
        root = rpc.eth_call(address, selectors_map()["getroot"])
    except Exception:
        root = None
    try:
        levels = rpc.eth_call(address, selectors_map()["levels"])
    except Exception:
        levels = None
    try:
        setver = rpc.eth_call(address, selectors_map()["setver"])
    except Exception:
        setver = None
    
    # Convert to integers where possible
    # denom_int = int(denom, 16) if denom and denom != "0x" else None
    # levels_int = int(levels, 16) if levels and levels != "0x" else None
    # Store as hex strings (TEXT) to avoid overflow
    denom_store = denom if denom and denom != "0x" else None
    levels_store = levels if levels and levels != "0x" else None
    
    candidate = {
        "address": address.lower(),
        "chain_id": chain_id,
        "code_size": len(code) - 2 if isinstance(code, str) else len(code),
        "bytecode_hash": bc_hash,
        "template_id": tid,
        "similarity": sim,
        "denom": denom_store,
        "root": root,
        "levels": levels_store,
        "deposit_sel": present.get("deposit", False),
        "withdraw_sel": present.get("withdraw", False),
        "nullif_sel": present.get("nullif", False),
        "setver_sel": present.get("setver", False),
        "updatever_sel": present.get("updatever", False),
        "verified_sel": present.get("verify", False),
        "getroot_sel": present.get("getroot", False),
        "roots_sel": present.get("roots", False),
        "token_sel": present.get("token", False),
        "ecrecover_sel": present.get("ecrecover_like", False),
        "analyzed_ts": now(),
        "status": "t1_complete"
    }
    
    # Persist to targets table
    c = conn()
    put(c, """INSERT OR REPLACE INTO targets 
        (address, chain_id, code_size, bytecode_hash, template_id, similarity, 
         denom, root, levels, deposit_sel, withdraw_sel, nullif_sel, 
         setver_sel, updatever_sel, verified_sel, getroot_sel, roots_sel, 
         token_sel, ecrecover_sel, analyzed_ts, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (candidate["address"], candidate["chain_id"], candidate["code_size"],
         candidate["bytecode_hash"], candidate["template_id"], candidate["similarity"],
         candidate["denom"], candidate["root"], candidate["levels"],
         candidate["deposit_sel"], candidate["withdraw_sel"], candidate["nullif_sel"],
         candidate["setver_sel"], candidate["updatever_sel"], candidate["verified_sel"],
         candidate["getroot_sel"], candidate["roots_sel"], candidate["token_sel"],
         candidate["ecrecover_sel"], candidate["analyzed_ts"], candidate["status"]))
    c.commit()
    c.close()
    
    return candidate

def run_t1_on_emitters(min_deposits=1, max_targets=None, rpc_url=None):
    """Run T1 analysis on emitters from walker that meet minimum activity."""
    c = conn()
    # Get emitters with sufficient activity
    query = """
        SELECT e.chain_id, e.address, e.deposits, e.withdrawals
        FROM emitters e
        WHERE (e.deposits + e.withdrawals) >= ?
        ORDER BY (e.deposits + e.withdrawals) DESC
    """
    params = [min_deposits]
    
    if max_targets:
        query += " LIMIT ?"
        params.append(max_targets)
    
    rows = c.execute(query, params).fetchall()
    c.close()
    
    results = []
    for row in rows:
        chain_id = row["chain_id"]
        address = row["address"]
        # Get RPC URL for this chain from config
        from core.config import config
        rpc_chain_url = None
        for cid, name, url, topics, start in config.chains:
            if cid == chain_id:
                rpc_chain_url = url
                break
        
        candidate = analyze_emitter(chain_id, address, rpc_chain_url)
        if candidate:
            results.append(candidate)
    
    return results

def get_t1_candidates(limit=None):
    """Retrieve T1-analyzed candidates from DB."""
    c = conn()
    query = """
        SELECT * FROM targets 
        WHERE status='t1_complete' 
        ORDER BY similarity DESC, analyzed_ts DESC
    """
    if limit:
        query += f" LIMIT {limit}"
    rows = c.execute(query).fetchall()
    c.close()
    return [dict(row) for row in rows]

if __name__ == "__main__":
    import sys
    print("[T1] Starting structural analysis on emitters...")
    results = run_t1_on_emitters(min_deposits=1, max_targets=50)
    print(f"[T1] Analyzed {len(results)} new candidates")
    candidates = get_t1_candidates(limit=10)
    print(f"[T1] Top {len(candidates)} candidates:")
    for cand in candidates:
        print(f"  {cand['address']} chain={cand['chain_id']} "
              f"sim={cand['similarity']} template={cand['template_id']} "
              f"dep={cand['deposit_sel']} wd={cand['withdraw_sel']} "
              f"nullif={cand['nullif_sel']} setver={cand['setver_sel']}")