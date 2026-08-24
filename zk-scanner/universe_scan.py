# universe_scan.py — full-pool universe discovery + cross-venue dislocation scan
# Walks factory PairCreated/PoolCreated logs (recent blocks), enumerates every
# live pool, reads reserves via 1 eth_call per pool, groups by token pair,
# and computes cross-venue dislocation + optimal two-pool arb on same-curve
# pools with real TVL. READ-ONLY, $0. Built on core/rpc.py.
#
# Usage: python universe_scan.py [--rpc URL] [--lookback 20000] [--min-tvl 5000]
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint
from core.selectors import kec256

WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
CBBTC = "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"

UNIV2_FACTORY = "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6"
AERO_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"

DECIMALS_CACHE = {
    WETH.lower(): 18, USDC.lower(): 6, CBBTC.lower(): 8,
}

SEL_RESERVES = "0x0902f1ac"
SEL_DECIMALS = "0x313ce567"  # decimals()
FEE_NUM = 997


def topic0(sig: str) -> str:
    return "0x" + kec256(sig.encode()).hex()


TOPIC_UNIV2_PAIR = topic0("PairCreated(address,address,address,uint256)")
# Velodrome/Aerodrome family: 3 indexed (token0, token1, stable) + pool & fee in data
TOPIC_AERO_POOL = topic0("PoolCreated(address,address,bool,address,uint256)")


def addr_from_topic(t: str) -> str:
    return "0x" + t[-40:]


def addr_from_data_word(w: str) -> str:
    return "0x" + w[24:64]


def chunked_logs(rpc, address, topics, from_block, to_block, chunk=2000):
    """eth_getLogs with chunk halving on error (walker.py pattern, simplified)."""
    out = []
    lo = from_block
    while lo <= to_block:
        size = chunk
        while size >= 50:
            hi = min(lo + size - 1, to_block)
            try:
                logs = rpc.call("eth_getLogs", [{
                    "address": address,
                    "topics": topics,
                    "fromBlock": hex(lo), "toBlock": hex(hi),
                }])
                out.extend(logs)
                break
            except Exception:
                size //= 2
                if size < 50:
                    raise
        lo += size
    return out


def get_decimals(rpc, token, cache):
    t = token.lower()
    if t in cache:
        return cache[t]
    raw = rpc.eth_call(token, SEL_DECIMALS)
    d = int(raw, 16) if raw and raw != "0x" else 18
    cache[t] = d
    return d


class FleetRPC:
    """Round-robins eth_call across endpoints with 429 backoff — the
    core/config.py fleet pattern, inline. getLogs stays pinned to the
    endpoint that serves it."""

    def __init__(self, urls, timeout=30, retries=4, per_call_delay=0.12):
        from core.rpc import RPC as _R
        self.rpcs = [_R(u, timeout=timeout, retries=1) for u in urls]
        self.i = 0
        self.retries = retries
        self.delay = per_call_delay
        import time as _t
        self._t = _t

    def eth_call(self, to, data, block="latest"):
        import time
        last = None
        for attempt in range(self.retries * len(self.rpcs)):
            rpc = self.rpcs[self.i % len(self.rpcs)]
            self.i += 1
            try:
                out = rpc.eth_call(to, data, block)
                time.sleep(self.delay)
                return out
            except Exception as e:
                last = e
                time.sleep(min(2.0, 0.5 * (1 + attempt)))  # backoff
        raise last

    def call(self, method, params):
        return self.rpcs[self.i % len(self.rpcs)].call(method, params)


def load_reserves(rpc, pool):
    raw = rpc.eth_call(pool, SEL_RESERVES)
    if not raw or raw == "0x" or len(raw) < 2 + 128:
        return None
    h = raw[2:]
    return int(h[0:64], 16), int(h[64:128], 16)


def cp_out(r_in, r_out, a_in, fee=FEE_NUM):
    ain = a_in * fee / 1000.0
    return r_out * ain / (r_in + ain)


def main():
    ap = argparse.ArgumentParser(description="VERITAS universe scan (read-only)")
    ap.add_argument("--rpc", default="https://base.drpc.org")
    ap.add_argument("--fleet", default="https://base.drpc.org,https://mainnet.base.org",
                    help="comma-separated endpoints for eth_call rotation")
    ap.add_argument("--lookback", type=int, default=20000,
                    help="blocks of creation events to walk (~11h on Base)")
    ap.add_argument("--min-tvl", type=float, default=5000.0)
    ap.add_argument("--max-pools", type=int, default=400)
    ap.add_argument("--weth-usd", type=float, default=2517.0)
    args = ap.parse_args()

    rpc = RPC(args.rpc, timeout=30, retries=3)          # log walk (pinned)
    fleet = FleetRPC([u.strip() for u in args.fleet.split(",") if u.strip()])
    dec_cache = dict(DECIMALS_CACHE)
    head = uint(rpc.call("eth_blockNumber", []))
    gas_price = uint(rpc.call("eth_gasPrice", [])) or 0
    from_block = max(0, head - args.lookback)
    print(f"[uni] chain head={head} walking {from_block}..{head} "
          f"gas={gas_price/1e9:.4f} gwei")

    # ---- 1. discovery: factory creation events -------------------------
    pools = []  # {venue, addr, t0, t1, stable}
    try:
        logs = chunked_logs(rpc, UNIV2_FACTORY, [TOPIC_UNIV2_PAIR],
                            from_block, head)
        for lg in logs:
            topics, data = lg.get("topics", []), lg.get("data", "0x")
            if len(topics) >= 3 and len(data) >= 2 + 64:
                pools.append({
                    "venue": "UniV2",
                    "addr": addr_from_data_word(data[2:]),
                    "t0": addr_from_topic(topics[1]),
                    "t1": addr_from_topic(topics[2]),
                    "stable": False,
                })
        print(f"[uni] UniV2 PairCreated: {len(pools)} pools")
    except Exception as e:
        print(f"[uni] UniV2 log walk failed: {e}")

    aero_before = len(pools)
    try:
        logs = chunked_logs(rpc, AERO_FACTORY, [TOPIC_AERO_POOL],
                            from_block, head)
        n = 0
        for lg in logs:
            topics, data = lg.get("topics", []), lg.get("data", "0x")
            if len(topics) == 5:  # all-indexed: token0,token1,pool,stable
                pool_addr = addr_from_topic(topics[3])
                stable = int(topics[4], 16) == 1
            elif len(topics) == 4 and len(data) >= 2 + 64:  # pool in data
                pool_addr = addr_from_data_word(data[2:])
                stable = False
            else:
                continue
            pools.append({
                "venue": "Aero",
                "addr": pool_addr,
                "t0": addr_from_topic(topics[1]),
                "t1": addr_from_topic(topics[2]),
                "stable": stable,
            })
            n += 1
        print(f"[uni] Aero PoolCreated: {n} pools "
              f"({sum(1 for p in pools[aero_before:] if p['stable'])} stable)")
    except Exception as e:
        print(f"[uni] Aero log walk failed: {e}")

    pools = pools[:args.max_pools]
    print(f"[uni] total (capped {args.max_pools}): {len(pools)}")

    # ---- 2. reserves + TVL --------------------------------------------
    weth_usd = args.weth_usd
    rows = []
    for p in pools:
        res = load_reserves(fleet, p["addr"])
        if not res:
            continue
        r0, r1 = res
        d0 = get_decimals(fleet, p["t0"], dec_cache)
        d1 = get_decimals(fleet, p["t1"], dec_cache)
        q0, q1 = r0 / 10**d0, r1 / 10**d1
        tvl_usd = None
        if p["t0"] == WETH:
            tvl_usd = 2 * q0 * weth_usd
        elif p["t1"] == WETH:
            tvl_usd = 2 * q1 * weth_usd
        elif p["t0"] == USDC:
            tvl_usd = 2 * q0
        elif p["t1"] == USDC:
            tvl_usd = 2 * q1
        elif p["t0"] == CBBTC:
            tvl_usd = 2 * q0 * weth_usd / 32.0  # rough BTC proxy
        elif p["t1"] == CBBTC:
            tvl_usd = 2 * q1 * weth_usd / 32.0
        rows.append({**p, "r0": r0, "r1": r1, "d0": d0, "d1": d1,
                     "q0": q0, "q1": q1, "tvl": tvl_usd})

    priced = [r for r in rows if r["tvl"] and r["tvl"] >= args.min_tvl]
    print(f"[uni] pools with reserves: {len(rows)}; "
          f"TVL>= ${args.min_tvl:,.0f}: {len(priced)}")

    # ---- 3. group by token pair, cross-venue same-curve only ----------
    groups = {}
    for r in priced:
        key = tuple(sorted([r["t0"], r["t1"]]))
        groups.setdefault(key, []).append(r)

    gas_usd = gas_price * 350_000 / 1e18 * weth_usd
    loan_fee_bps = 5  # 0.05%
    print(f"\n[uni] gas floor ${gas_usd:.4f} | loan fee {loan_fee_bps} bps | "
          f"submit gate: profit_bps > fees_bps\n")

    top = 0
    for key, grp in groups.items():
        cp = [g for g in grp if not g["stable"]]  # same-curve only
        if len(cp) < 2:
            continue
        # dislocation between each venue pair (mid price of the common base)
        base = key[1] if key[0] in (WETH, USDC, CBBTC) else key[0]
        quote = key[0] if base == key[1] else key[1]
        vals = []
        for g in cp:
            # price = quote per base
            if g["t0"] == base:
                pr = (g["q1"]) / (g["q0"]) if g["q0"] else 0
            else:
                pr = (g["q0"]) / (g["q1"]) if g["q1"] else 0
            vals.append((g, pr))
        vals = [(g, p) for g, p in vals if p > 0]
        if len(vals) < 2:
            continue
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                ga, pa = vals[i]
                gb, pb = vals[j]
                if ga["venue"] == gb["venue"] and ga["addr"] == gb["addr"]:
                    continue
                disl_bps = abs(pa / pb - 1) * 1e4
                if disl_bps <= 61:  # below 2x30bps fees + 5bps loan — skip
                    continue
                # numeric arb optimization on the two pools
                best = None
                for (buy, sell) in ((ga, gb), (gb, ga)):
                    # buy base on `buy` (base->quote), sell on `sell` (quote->base)
                    if buy["t0"] == base:
                        bin_h, bout_h = buy["q0"], buy["q1"]
                    else:
                        bin_h, bout_h = buy["q1"], buy["q0"]
                    if sell["t0"] == base:
                        sin_h, sout_h = sell["q1"], sell["q0"]
                    else:
                        sin_h, sout_h = sell["q0"], sell["q1"]
                    hi = min(bin_h, sout_h) * 0.30
                    for k in range(80):
                        size = hi * ((k + 1) / 80.0)
                        got_q = cp_out(bin_h, bout_h, size)
                        got_b = cp_out(sin_h, sout_h, got_q)
                        profit = got_b - size
                        if profit > 0:
                            if best is None or profit > best[1]:
                                best = (size, profit)
                base_usd = weth_usd if base == WETH else (1.0 if base == USDC else weth_usd / 32.0)
                pair = f"{key[0][:6]}..{key[0][-4:]}/{key[1][:6]}..{key[1][-4:]}"
                if best:
                    size, profit = best
                    p_usd = profit * base_usd
                    print(f"  {pair}  {ga['venue']} vs {gb['venue']}: "
                          f"{disl_bps:8.1f} bps dislocation | "
                          f"optimal {size:,.4f} base -> profit ${p_usd:,.2f}")
                    top += 1
                else:
                    print(f"  {pair}  {ga['venue']} vs {gb['venue']}: "
                          f"{disl_bps:8.1f} bps dislocation | no profitable size")

    if top == 0:
        print("  (no cross-venue dislocation above the fee wall right now)")
    print(f"\n[uni] done. candidates above fee wall: {top}")


if __name__ == "__main__":
    main()
