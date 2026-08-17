# core/discovery.py — T0 event-graph walker: eth_getLogs topic sweep, $0, no API keys
# Finds every contract emitting Tornado-family Deposit/Withdrawal events,
# including forks with modified bytecode (event shapes survive forking).
from core.selectors import kec256

TOPICS = {
    "deposit":    "0x" + kec256(b"Deposit(bytes32,uint32,uint256)").hex(),
    "withdrawal": "0x" + kec256(b"Withdrawal(address,bytes32,address,uint256)").hex(),
}

def walk(rpc, topic, from_block, to_block, chunk=9000, hard_floor=500, log_cap=1800):
    """Adaptive-range eth_getLogs sweep. Yields raw logs; halves on rate/cap
    errors, grows back on success. Never stalls: floor chunks are accepted."""
    logs, lo = [], from_block
    while lo <= to_block:
        hi = min(lo + chunk - 1, to_block)
        try:
            batch = rpc.call("eth_getLogs", [{
                "fromBlock": hex(lo), "toBlock": hex(hi),
                "topics": [topic]}])
        except Exception:
            if chunk > hard_floor:
                chunk = max(hard_floor, chunk // 2)
                continue
            lo = hi + 1  # unrecoverable window — skip, keep moving
            continue
        if len(batch) >= log_cap and chunk > hard_floor:
            chunk = max(hard_floor, chunk // 2)
            continue     # likely truncated — refetch narrower
        logs.extend(batch)
        lo = hi + 1
        chunk = min(9000, int(chunk * 1.25) + 1)
    return logs

def aggregate(logs):
    """logs -> {address: {events, first_block, last_block}}"""
    out = {}
    for lg in logs:
        a = lg["address"].lower()
        b = int(lg["blockNumber"], 16)
        e = out.setdefault(a, {"events": 0, "first_block": b, "last_block": b})
        e["events"] += 1
        e["first_block"] = min(e["first_block"], b)
        e["last_block"] = max(e["last_block"], b)
    return out

def discover(rpc, latest, lookback_blocks=300_000):
    """Sweep both event shapes over trailing window. Returns aggregates."""
    from_block = max(0, latest - lookback_blocks)
    found = {}
    for name, topic in TOPICS.items():
        logs = walk(rpc, topic, from_block, latest)
        for addr, e in aggregate(logs).items():
            f = found.setdefault(addr, {"deposits": 0, "withdrawals": 0,
                                        "first_block": e["first_block"],
                                        "last_block": e["last_block"]})
            f[name + "s" if name == "deposit" else "withdrawals"] = e["events"]
            f["first_block"] = min(f["first_block"], e["first_block"])
            f["last_block"] = max(f["last_block"], e["last_block"])
    return found, from_block
