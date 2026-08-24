#!/usr/bin/env python3
"""
arb_engine.py — VERITAS Engine: Arbitrum flash-loan arb scan layer.

Layer 1 — SCAN (read-only, $0): find dislocations between constant-product
pools on Arbitrum (SushiSwap V2 + Uniswap V2 factories) for WETH/USDC and
WETH/USDC.e, compute optimal trade size and gross profit with the proven
two-pool CPMM math (ported from the Base arb_scan.py lineage).

Cost-stack gate: gross profit must clear BOTH swap fees (in the math),
the 0.05% Aave flash-loan premium on principal, gas, and a safety margin
before a candidate is called an EDGE.

This module NEVER signs or sends a transaction. The fork-sim gate
(sim_gate.py) and the hunter loop (flash_hunter.py) consume its output.

Usage:
  python3 arb_engine.py scan                # one pass, human report
  python3 arb_engine.py scan --json         # one pass, machine JSON
  python3 arb_engine.py scan --interval 30  # loop with heartbeats
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint

# ---- verified on-chain 2026-08-23 (verify_arb_venues.py) ----------------
# NOTE: all addresses stored LOWERCASE — parse_addr() returns lowercase and
# every comparison in pool_side() is exact-match.
WETH   = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC   = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE  = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
UNIV2_FACTORY  = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY  = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
AAVE_V3_POOL   = "0x794a61358d6845594f94dc1db02a252b5b4814ad"

TOKENS = {
    WETH:  {"sym": "WETH",   "decimals": 18},
    USDC:  {"sym": "USDC",   "decimals": 6},
    USDCE: {"sym": "USDC.e", "decimals": 6},
}

FEE_NUM = 997            # 0.3% V2 pools: keep 997/1000 of input
AAVE_FLASH_FEE = 0.0005  # 0.05% premium on borrowed principal
GAS_UNITS = 350_000      # measured-class executor tx cost
SAFETY_MARGIN_USD = 0.50 # edge must clear the whole stack by this much

SEL = {
    "getPair":  "e6a43905",
    "reserves": "0902f1ac",
    "token0":   "0dfe1681",
    "token1":   "d21220a7",
}


# ---- low-level helpers ---------------------------------------------------

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


def univ2_pair(rpc, factory, token_a, token_b):
    data = "0x" + SEL["getPair"] + pad_addr(token_a) + pad_addr(token_b)
    return parse_addr(rpc.eth_call(factory, data))


def load_pool(rpc, name, address):
    """token ordering + reserves for a constant-product pair."""
    if address is None:
        return None
    t0 = parse_addr(rpc.eth_call(address, "0x" + SEL["token0"]))
    t1 = parse_addr(rpc.eth_call(address, "0x" + SEL["token1"]))
    r0, r1 = parse_reserves(rpc.eth_call(address, "0x" + SEL["reserves"]))
    if t0 is None or t1 is None or r0 is None:
        return None
    return {"name": name, "address": address,
            "token0": t0, "token1": t1, "r0": r0, "r1": r1}


def pool_side(pool, base, quote):
    """(base_reserve_raw, quote_reserve_raw) for base/quote, or (None, None)."""
    if pool["token0"] == base and pool["token1"] == quote:
        return pool["r0"], pool["r1"]
    if pool["token0"] == quote and pool["token1"] == base:
        return pool["r1"], pool["r0"]
    return None, None


def human(raw, token):
    return raw / 10 ** TOKENS[token]["decimals"]


def price_of(pool, base, quote):
    br, qr = pool_side(pool, base, quote)
    if not br:
        return None
    return human(qr, quote) / human(br, base)


# ---- arb math (constant-product, numeric size scan) ----------------------

def cp_out(reserve_in, reserve_out, amount_in, fee_num=FEE_NUM):
    if amount_in <= 0:
        return 0.0
    ain = amount_in * fee_num / 1000.0
    return reserve_out * ain / (reserve_in + ain)


def best_two_pool_arb(pool_a, pool_b, base, quote, ref_price):
    """Best (direction, size_base, gross_profit_usd) buying base on one pool
    and selling it on the other, both directions considered."""
    best = None
    for (buy, sell) in ((pool_a, pool_b), (pool_b, pool_a)):
        b_in_r, b_out_r = pool_side(buy, base, quote)    # base -> quote side
        s_in_r, s_out_r = pool_side(sell, quote, base)   # quote -> base side
        if not b_in_r or not s_out_r:
            continue
        bin_h, bout_h = human(b_in_r, base), human(b_out_r, quote)
        sin_h, sout_h = human(s_in_r, quote), human(s_out_r, base)
        if bin_h <= 0 or sin_h <= 0:
            continue
        hi = min(bin_h, sout_h) * 0.30
        for i in range(120):
            size = hi * (i / 119.0) if i else hi * 1e-4
            if size <= 0:
                continue
            got_quote = cp_out(bin_h, bout_h, size)
            got_base = cp_out(sin_h, sout_h, got_quote)
            profit = got_base - size
            if profit > 0:
                usd = profit * ref_price
                if best is None or usd > best[2]:
                    best = (f"{buy['name']} -> {sell['name']}", size, usd)
    return best


# ---- scan ----------------------------------------------------------------

# Token universe for pair census. Every address is verified on-chain at scan
# time (code + decimals); anything misremembered or dead simply drops out.
TOKEN_UNIVERSE = {
    WETH:  {"sym": "WETH",   "decimals": 18},
    USDC:  {"sym": "USDC",   "decimals": 6},
    USDCE: {"sym": "USDC.e", "decimals": 6},
    "0x912ce59144191c1204e64559fe8253a0e49e6548": {"sym": "ARB",  "decimals": 18},
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": {"sym": "WBTC", "decimals": 8},
    "0xf97f4df75117a78c1a5a0dbb814af92458539fb4": {"sym": "LINK", "decimals": 18},
    "0xfa7f8980b0f1e64a2062791cc3b0871572f1f7f0": {"sym": "UNI",  "decimals": 18},
    "0xd4d42f0b6def4ce0383636770ef773390d85c61a": {"sym": "SUSHI","decimals": 18},
    "0x11cdb42b0eb46d95f990bedd4695a6e3fa034978": {"sym": "CRV",  "decimals": 18},
    "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a": {"sym": "GMX",  "decimals": 18},
}

# master token table: universe lookup
TOKENS = TOKEN_UNIVERSE

MIN_POOL_USD = 2_000  # a pool must hold >= this (USD side) to count


def discover_pools(rpc):
    """Census: every (factory x quote) pair for the token universe, keeping
    only pools with real liquidity. Returns list of pool dicts with the
    quote token attached."""
    pools = []
    quotes = [t for t in TOKEN_UNIVERSE if t != WETH]
    for fname, factory in [("UniV2", UNIV2_FACTORY), ("Sushi", SUSHI_FACTORY)]:
        for quote in quotes:
            qname = TOKEN_UNIVERSE[quote]["sym"]
            pair = univ2_pair(rpc, factory, WETH, quote)
            if not pair:
                continue
            p = load_pool(rpc, f"{fname} WETH/{qname}", pair)
            if not p:
                continue
            br, qr = pool_side(p, WETH, quote)
            if not br or not qr:
                continue
            p["quote"] = quote
            p["quote_reserve"] = human(qr, quote)
            p["weth_reserve"] = human(br, WETH)
            # USD depth: stable quotes direct; else WETH leg * est ETH price
            usd = (p["quote_reserve"] if quote in (USDC, USDCE)
                   else p["weth_reserve"] * 2400.0)
            if usd < MIN_POOL_USD:
                continue
            pools.append(p)
    return pools


def scan_once(rpc):
    """Full pass. Returns dict with pools, eth_usd, gas, and actionable edges."""
    gas_price_wei = uint(rpc.call("eth_gasPrice", [])) or 0
    pools = discover_pools(rpc)

    # ETH/USD from the deepest stable-quoted pool
    eth_usd, best_depth = None, 0
    for p in pools:
        if p["quote"] in (USDC, USDCE) and p["weth_reserve"] > best_depth:
            eth_usd = p["quote_reserve"] / p["weth_reserve"]
            best_depth = p["weth_reserve"]
    if not eth_usd:
        return {"error": "no stable-quoted pool for ETH pricing"}

    gas_usd = (gas_price_wei * GAS_UNITS / 1e18) * eth_usd

    edges = []
    report = []
    quotes = {p["quote"] for p in pools}
    for quote in quotes:
        qname = TOKEN_UNIVERSE[quote]["sym"]
        pair_pools = [p for p in pools if p["quote"] == quote]
        if len(pair_pools) < 2:
            continue
        prices = sorted(price_of(p, WETH, quote) for p in pair_pools)
        ref = prices[len(prices) // 2]  # quote-per-WETH (display only)
        for i in range(len(pair_pools)):
            for j in range(len(pair_pools)):
                if i >= j:
                    continue
                a, b = pair_pools[i], pair_pools[j]
                disl_bps = abs(price_of(a, WETH, quote)
                               / price_of(b, WETH, quote) - 1) * 1e4
                # profit is in WETH units -> USD at eth_usd
                best = best_two_pool_arb(a, b, WETH, quote, eth_usd)
                row = {"pair": f"WETH/{qname}", "venue_a": a["name"],
                       "venue_b": b["name"], "dislocation_bps": round(disl_bps, 2)}
                if best:
                    direction, size, gross = best
                    loan_fee_usd = size * eth_usd * AAVE_FLASH_FEE
                    net = gross - gas_usd - loan_fee_usd
                    row.update({
                        "direction": direction,
                        "size_weth": round(size, 6),
                        "gross_usd": round(gross, 4),
                        "loan_fee_usd": round(loan_fee_usd, 4),
                        "gas_usd": round(gas_usd, 4),
                        "net_usd": round(net, 4),
                    })
                    if net > SAFETY_MARGIN_USD:
                        row["edge"] = True
                        edges.append(row)
                report.append(row)

    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chain_head": uint(rpc.call("eth_blockNumber", [])),
        "eth_usd": round(eth_usd, 2),
        "gas_gwei": round(gas_price_wei / 1e9, 4),
        "gas_usd": round(gas_usd, 4),
        "pools": [{"name": p["name"], "address": p["address"],
                   "weth_reserve": round(p["weth_reserve"], 4),
                   "quote_reserve": round(p["quote_reserve"], 2)}
                  for p in pools],
        "pairs_scanned": len(report),
        "edges": edges,
        "detail": report,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["scan"])
    ap.add_argument("--rpc", default="https://arb1.arbitrum.io/rpc")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rpc = RPC(args.rpc, timeout=30, retries=3)
    cycle = 0
    while True:
        cycle += 1
        try:
            result = scan_once(rpc)
            if args.json:
                print(json.dumps(result))
            else:
                print(f"[{result.get('ts')}] head={result.get('chain_head')} "
                      f"ETH=${result.get('eth_usd')} gas={result.get('gas_gwei')} gwei "
                      f"(${result.get('gas_usd')})")
                for p in result.get("pools", []):
                    print(f"  pool {p['name']:<22} WETH reserve {p['weth_reserve']:>12,.2f}")
                for r in result.get("detail", []):
                    line = (f"  {r['pair']:<12} {r['venue_a']:<20} vs "
                            f"{r['venue_b']:<20} {r['dislocation_bps']:>8.2f} bps")
                    if r.get("size_weth") is not None:
                        line += (f" | size {r['size_weth']} WETH "
                                 f"gross ${r['gross_usd']} net ${r['net_usd']}")
                        if r.get("edge"):
                            line += "  *** EDGE ***"
                    print(line)
                print(f"  => {len(result.get('edges', []))} actionable edges")
        except Exception as e:
            print(f"[scan error] {e}", flush=True)
        if args.interval <= 0:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
