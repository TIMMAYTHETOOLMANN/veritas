#!/usr/bin/env python3
"""
arb_engine_registry.py — VERITAS Phase-2.5: cross-venue arb scanner over the
full pool registry (veritas.db).

Uses live pool_registry data plus on-the-fly token decimals to scan every
WETH-paired token across all registered venues (V2 + V3 fee tiers). All
read-only. Produces edges ready for FlashloanArbV2.execute().
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint
import v3_layer

WETH   = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC   = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE  = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
HUBS   = {WETH, USDC, USDCE}

AAVE_FLASH_FEE = 0.0005
GAS_UNITS = 450_000
SAFETY_MARGIN_USD = 0.10  # lower than majors-only; long-tail needs tighter gate

SEL = {
    "token0": "0x0dfe1681",
    "token1": "0xd21220a7",
    "reserves": "0x0902f1ac",
    "decimals": "0x313ce567",
}

_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veritas.db")
_dec_cache = {}


def pad_addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def parse_addr(result):
    if not result or len(result) < 66:
        return None
    tail = result[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def parse_reserves(result):
    if not result or result == "0x" or len(result) < 130:
        return None, None
    h = result[2:]
    return int(h[0:64], 16), int(h[64:128], 16)


def token_decimals(rpc, addr):
    key = addr.lower()
    if key in _dec_cache:
        return _dec_cache[key]
    try:
        r = rpc.eth_call(addr, SEL["decimals"])
        d = int(r[2:66], 16) if r and len(r) >= 66 else 18
    except Exception:
        d = 18
    _dec_cache[key] = d
    return d


def load_pools(rpc, min_depth=500.0):
    """Load enriched registry pools; enrich any missing reserve/depth data."""
    conn = sqlite3.connect(_db_path, timeout=30)
    rows = conn.execute(
        "SELECT pair_addr, venue, kind, token0, token1, fee_tier, "
        "reserve0, reserve1, usd_depth FROM pools"
    ).fetchall()
    conn.close()

    pools = []
    for pair, venue, kind, t0, t1, fee, r0, r1, depth in rows:
        t0l, t1l = t0.lower(), t1.lower()
        # we only trade paths that start/end in WETH
        if WETH not in (t0l, t1l):
            continue
        # enrich if needed (V2 with no reserves)
        if kind == "v2" and (r0 is None or r1 is None or depth is None or depth == 0):
            try:
                res = rpc.eth_call(pair, SEL["reserves"])
                r0_raw, r1_raw = parse_reserves(res)
                if r0_raw is None:
                    continue
                dec0 = token_decimals(rpc, t0l)
                dec1 = token_decimals(rpc, t1l)
                r0_f = r0_raw / 10 ** dec0
                r1_f = r1_raw / 10 ** dec1
                # depth in USD: hub side doubled
                if t1l == WETH:
                    depth = 2 * r1_f * eth_usd_approx(rpc)
                elif t0l == WETH:
                    depth = 2 * r0_f * eth_usd_approx(rpc)
                elif t1l in (USDC, USDCE):
                    depth = 2 * r1_f
                elif t0l in (USDC, USDCE):
                    depth = 2 * r0_f
                else:
                    depth = 0.0
                # write back
                conn = sqlite3.connect(_db_path, timeout=30)
                conn.execute("UPDATE pools SET reserve0=?, reserve1=?, usd_depth=?, last_checked=? WHERE pair_addr=?",
                             (r0_f, r1_f, depth, time.strftime("%Y-%m-%d %H:%M:%S"), pair))
                conn.commit()
                conn.close()
            except Exception:
                continue
        else:
            r0_f, r1_f = r0, r1

        if kind == "v2" and (depth is None or depth < min_depth):
            continue
        pools.append({
            "pair": pair.lower(),
            "venue": venue,
            "kind": kind,
            "token0": t0l,
            "token1": t1l,
            "fee": fee or 0,
            "reserve0": r0_f,
            "reserve1": r1_f,
            "depth": depth or 0.0,
        })
    return pools


def eth_usd_approx(rpc):
    """Best ETH/USDC price from QuoterV2; fallback 2400."""
    try:
        out = v3_layer.quote_v3(rpc, WETH, USDC, 10**18, 500,
                                "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f")
        return out / 1e6 if out else 2400.0
    except Exception:
        return 2400.0


def v2_quote_out(rpc, pool, token_in, token_out, amount_in_raw):
    """Quote exact out for a V2 pair using live reserves."""
    try:
        t0 = parse_addr(rpc.eth_call(pool["pair"], SEL["token0"]))
        r0_raw, r1_raw = parse_reserves(rpc.eth_call(pool["pair"], SEL["reserves"]))
        if t0 is None or r0_raw is None:
            return None
        dec_in = token_decimals(rpc, token_in)
        dec_out = token_decimals(rpc, token_out)
        in_h = amount_in_raw / 10 ** dec_in
        if t0.lower() == token_in.lower():
            r_in, r_out = r0_raw, r1_raw
        else:
            r_in, r_out = r1_raw, r0_raw
        ain = in_h * 997 / 1000.0
        out_h = (r_out / 10 ** dec_out) * ain / ((r_in / 10 ** dec_in) + ain)
        return int(out_h * 10 ** dec_out)
    except Exception:
        return None


def group_pools_by_quote(pools):
    """Group WETH-paired pools by quote token."""
    groups = {}
    for p in pools:
        quote = p["token1"] if p["token0"] == WETH else p["token0"]
        groups.setdefault(quote, []).append(p)
    return groups


def scan_registry_cross_venue(rpc, gas_usd=None, eth_usd=None, size_steps=10,
                              min_net_usd=0.05, max_pools=None):
    """Scan the registry for WETH/quote cross-venue edges.

    Returns (edges, report).
    """
    if eth_usd is None:
        eth_usd = eth_usd_approx(rpc)
    if gas_usd is None:
        gas_price = uint(rpc.call("eth_gasPrice", [])) or 0
        gas_usd = (gas_price * GAS_UNITS / 1e18) * eth_usd

    pools = load_pools(rpc)
    groups = group_pools_by_quote(pools)
    from_addr = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"

    edges = []
    report = []
    scanned_pairs = 0

    for quote, venue_list in groups.items():
        if len(venue_list) < 2:
            continue
        qdec = token_decimals(rpc, quote)
        # build venue descriptors (name, kind, address/fee)
        venues = []
        for p in venue_list:
            if p["kind"] == "v3":
                venues.append((f"V3 {p['fee']/10000:.2f}% {p['venue'][:10]}", 1, p["venue"], p["fee"], p))
            else:
                venues.append((f"V2 {p['venue']} {p['venue'][:10]}", 0, p["venue"], 0, p))

        for i, buy in enumerate(venues):
            for j, sell in enumerate(venues):
                if i == j:
                    continue
                scanned_pairs += 1
                if max_pools and scanned_pairs > max_pools:
                    break
                best = None
                # size grid in WETH, up to 3 WETH (flash loan sizing)
                hi = 2.0
                for k in range(1, size_steps + 1):
                    size = hi * k / size_steps
                    amt = int(size * 1e18)
                    # buy leg: WETH -> quote
                    if buy[1] == 1:
                        mid = v3_layer.quote_v3(rpc, WETH, quote, amt, buy[3], from_addr)
                    else:
                        mid = v2_quote_out(rpc, buy[4], WETH, quote, amt)
                    if not mid:
                        continue
                    # sell leg: quote -> WETH
                    if sell[1] == 1:
                        back = v3_layer.quote_v3(rpc, quote, WETH, mid, sell[3], from_addr)
                    else:
                        back = v2_quote_out(rpc, sell[4], quote, WETH, mid)
                    if not back:
                        continue
                    profit_weth = (back - amt) / 1e18
                    if profit_weth > 0 and (best is None or profit_weth > best[2]):
                        best = (size, mid, profit_weth)
                row = {"pair": f"WETH/{quote[:10]}..", "quote": quote,
                       "venue_buy": buy[0], "venue_sell": sell[0]}
                if best:
                    size, mid, profit = best
                    loan_fee_usd = size * eth_usd * AAVE_FLASH_FEE
                    net = profit * eth_usd - gas_usd - loan_fee_usd
                    row.update({
                        "size_weth": round(size, 6),
                        "gross_usd": round(profit * eth_usd, 4),
                        "loan_fee_usd": round(loan_fee_usd, 4),
                        "gas_usd": round(gas_usd, 4),
                        "net_usd": round(net, 4),
                        "buy_kind": buy[1], "buy_venue": buy[2], "buy_fee": buy[3],
                        "sell_kind": sell[1], "sell_venue": sell[2], "sell_fee": sell[3],
                        "eth_usd": eth_usd,
                    })
                    if net > min_net_usd:
                        row["edge"] = True
                        edges.append(row)
                report.append(row)
        if max_pools and scanned_pairs > max_pools:
            break

    return edges, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default="https://gateway.tenderly.co/public/arbitrum")
    ap.add_argument("--max-pairs", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rpc = RPC(args.rpc, timeout=30, retries=3)
    edges, report = scan_registry_cross_venue(rpc, max_pools=args.max_pairs or None)
    print(f"[registry-scan] {len(report)} venue-pairs scanned, {len(edges)} edges")
    if edges:
        print("EDGES:")
        for e in edges:
            print(json.dumps(e))


if __name__ == "__main__":
    import argparse
    main()
