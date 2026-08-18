# core/value.py — L0/L1/L2/L3 value census (measured, never assumed)
from core.db import conn
from core import rpc as rpc_mod
from core.rpc import uint
import time

def get_eth_balance(address, rpc_url=None):
    """Return ETH balance in wei (integer)."""
    if rpc_url is None:
        rpc_url = "https://ethereum-rpc.publicnode.com"
    rpc = rpc_mod.RPC(rpc_url, timeout=20, retries=3)
    try:
        return rpc.get_balance(address)
    except Exception:
        return 0

def get_denomination(address, rpc_url=None):
    """Return denomination from targets table (hex string), convert to int wei."""
    c = conn()
    row = c.execute(
        "SELECT denom FROM targets WHERE address=?", (address.lower(),)
    ).fetchone()
    c.close()
    if row and row["denom"]:
        try:
            return int(row["denom"], 16)
        except ValueError:
            return 0
    return 0

def get_event_counts(address):
    """Return (deposits, withdrawals) counts from emitters table."""
    c = conn()
    row = c.execute(
        "SELECT deposits, withdrawals FROM emitters WHERE address=?", (address.lower(),)
    ).fetchone()
    c.close()
    if row:
        return int(row["deposits"] or 0), int(row["withdrawals"] or 0)
    return 0, 0

def compute_inventory(address, rpc_url=None):
    """Return dict with L0_wei (ETH balance), L1_wei (pool size from event counts * denomination)."""
    eth_bal = get_eth_balance(address, rpc_url)
    denom = get_denomination(address, rpc_url)
    deposits, withdrawals = get_event_counts(address)
    # L1: assume each event moves exactly 1 denomination unit (true for fixed-denomination pools)
    l1 = (deposits - withdrawals) * denom if denom > 0 else 0
    # L0 is ETH balance (the contract's own ether)
    # For now, we ignore L2/L3 (future inflow, systemic ceiling) – can be added later
    return {
        "address": address.lower(),
        "L0_wei": eth_bal,
        "L1_wei": l1,
        "denom": denom,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "fetched_at": int(time.time())
    }

def get_inventory_from_db(address):
    """Try to fetch a cached inventory from inventory table (if we decide to store it)."""
    # For now, we don't store inventory; we compute on demand.
    return None

def cache_inventory(inventory_dict):
    """Optionally store inventory in inventory table."""
    c = conn()
    c.execute("""INSERT OR REPLACE INTO inventory
        (address, layer, asset, amount_wei, block, source, ts)
        VALUES (?,?,?,?,?,?,?)""",
        (inventory_dict["address"], "L0", "ETH", str(inventory_dict["L0_wei"]), 0, "value", inventory_dict["fetched_at"]))
    c.execute("""INSERT OR REPLACE INTO inventory
        (address, layer, asset, amount_wei, block, source, ts)
        VALUES (?,?,?,?,?,?,?)""",
        (inventory_dict["address"], "L1", "POOL", str(inventory_dict["L1_wei"]), 0, "value", inventory_dict["fetched_at"]))
    c.commit()
    c.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m core.value <address> [rpc_url]")
        sys.exit(1)
    addr = sys.argv[1]
    rpc = sys.argv[2] if len(sys.argv) > 2 else None
    inv = compute_inventory(addr, rpc)
    print(f"Address: {inv['address']}")
    print(f"  L0 (ETH balance): {inv['L0_wei']} wei ({inv['L0_wei'] / 1e18:.5f} ETH)")
    print(f"  Denomination: {inv['denom']} wei")
    print(f"  Event counts: deposits={inv['deposits']}, withdrawals={inv['withdrawals']}")
    print(f"  L1 (pool size): {inv['L1_wei']} wei ({inv['L1_wei'] / 1e18:.5f} ETH)")


# ----------------------------------------------------------------------------
# Legacy API (pre-2026-08-18 surface) — scan.py / validate.py / scoring.py
# still call these. Kept as measured-value shims over the same RPC; removing
# them broke the T0->T2 pipeline at runtime.
# ----------------------------------------------------------------------------

ZERO32 = "0x" + "00" * 32

def erc20_balance(rpc, token, holder):
    # balanceOf(address) = 0x70a08231
    data = "0x70a08231" + holder[2:].lower().rjust(64, "0")
    return uint(rpc.eth_call(token, data))

def scan_L0(rpc, address):
    """Direct custody: native ETH balance. (ERC20 sweep needs token list — L0e.)"""
    return {"layer": "L0", "eth_wei": rpc.get_balance(address)}

def scan_L1_pool(rpc, address, denom):
    """Claimable pool proxy: balance // denomination = approx note count."""
    out = {"layer": "L1", "denomination_wei": denom, "approx_notes": None}
    bal = rpc.get_balance(address)
    if denom and denom > 0 and bal >= denom:
        out["approx_notes"] = bal // denom
    out["balance_wei"] = bal
    return out

def scan_L2_rate(events_back, seconds, avg_note_wei):
    """Future-inflow projection: deposit events observed over a window."""
    if seconds == 0:
        return {"layer": "L2", "rate_wei_per_day": 0}
    rate = (events_back * avg_note_wei) / (seconds / 86400)
    return {"layer": "L2", "deposits_in_window": events_back,
            "rate_wei_per_day": int(rate)}

def inventory(address, rpc, denom=None, l2=None):
    """Full measured inventory (legacy list form). V = max reachable layer."""
    inv = [scan_L0(rpc, address)]
    if denom:
        inv.append(scan_L1_pool(rpc, address, denom))
    if l2:
        inv.append(l2)
    return inv