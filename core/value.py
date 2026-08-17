# core/value.py — L0/L1/L2 value census: measured, never assumed
import json
from core.rpc import uint

ZERO32 = "0x" + "00" * 32

def erc20_balance(rpc, token, holder):
    # balanceOf(address) = 0x70a08231
    data = "0x70a08231" + holder[2:].lower().rjust(64, "0")
    return uint(rpc.eth_call(token, data))

def scan_L0(rpc, address):
    """Direct custody: native ETH balance. (ERC20 sweep needs token list — L0e.)"""
    return {"layer": "L0", "eth_wei": rpc.get_balance(address)}

def scan_L1_pool(rpc, address, denom):
    """Claimable pool proxy: if the contract holds ~denomination × N in ETH,
    deposits-minus-withdrawals ≈ balance. Denomination read where available."""
    out = {"layer": "L1", "denomination_wei": denom, "approx_notes": None}
    bal = rpc.get_balance(address)
    if denom and denom > 0 and bal >= denom:
        out["approx_notes"] = bal // denom
    out["balance_wei"] = bal
    return out

def scan_L2_rate(events_back, seconds, avg_note_wei):
    """Future-inflow projection: deposit events observed over a window.
    events_back = deposit count in trailing window (from event fetcher)."""
    if seconds == 0:
        return {"layer": "L2", "rate_wei_per_day": 0}
    rate = (events_back * avg_note_wei) / (seconds / 86400)
    return {"layer": "L2", "deposits_in_window": events_back,
            "rate_wei_per_day": int(rate)}

def inventory(address, rpc, denom=None, l2=None):
    """Full measured inventory. V = max reachable layer per class ceiling."""
    inv = [scan_L0(rpc, address)]
    if denom:
        inv.append(scan_L1_pool(rpc, address, denom))
    if l2:
        inv.append(l2)
    return inv
