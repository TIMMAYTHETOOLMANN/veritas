#!/usr/bin/env python3
"""
arb_sentinel.py — VERITAS Engine 1: continuous cross-DEX arbitrage watchman.

ZERO RISK, ZERO COST: read-only eth_call scans, forever. Never signs, never
sends, never spends. It exists to answer ONE question continuously:

    "Is there, RIGHT NOW, a dislocation between two real pools that clears
     the entire cost stack — both swap fees + gas + flash-loan premium?"

Only when the answer is YES does the flash-loan executor become relevant
(borrow -> swap -> swap -> repay in one atomic tx; reverts cost ~gas only).

Engine design:
  - scans UniV2 + Aerodrome pools on Base for WETH/USDC and WETH/cbBTC
  - numeric optimum trade size per pair-direction (same math arb_scan uses)
  - fires an ALERT (stdout + JSONL log) only when:
        gross_profit_usd > gas_floor + loan_fee + safety_margin
  - logs every scan cycle to arb_sentinel.log (JSONL) for forensic review
  - --once for single-shot mode; default loops every --interval seconds

Usage:
  python arb_sentinel.py                # loop forever, 30s cycles
  python arb_sentinel.py --once         # single scan
  python arb_sentinel.py --interval 10  # faster loops
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# reuse the verified scanner components — same math, same addresses
from arb_scan import (
    WETH, USDC, CBBTC, TOKENS,
    univ2_pair, aero_pool, load_pool, pool_side,
    cp_out, best_two_pool_arb, price_of,
)
from core.rpc import RPC, uint

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "arb_sentinel.log")

# cost stack for the go/no-go gate (USD):
GAS_UNITS = 350_000
AAVE_FLASH_FEE = 0.0005        # 0.05% premium on borrowed principal
SAFETY_MARGIN_USD = 0.50       # profit must clear everything by this much
PAIRS = [(WETH, USDC), (WETH, CBBTC)]


def scan_once(rpc, gas_price_wei, eth_usd, verbose=True):
    """One full pass. Returns list of actionable edges (possibly empty)."""
    pools = []
    for base, quote in PAIRS:
        tag = f"{TOKENS[base]['sym']}/{TOKENS[quote]['sym']}"
        p = univ2_pair(rpc, base, quote)
        if p:
            pools.append(load_pool(rpc, f"UniV2 {tag}", p))
        for stable in (False, True):
            p = aero_pool(rpc, base, quote, stable)
            kind = "sAMM" if stable else "vAMM"
            if p:
                pools.append(load_pool(rpc, f"Aero {kind} {tag}", p))
    pools = [p for p in pools if p]
    if not pools:
        return []

    # per-pair analysis across constant-product pools
    edges = []
    for base, quote in PAIRS:
        bs, qs = TOKENS[base]["sym"], TOKENS[quote]["sym"]
        pair_pools = []
        for pool in pools:
            br, qr = pool_side(pool, base, quote)
            if br is not None:
                pair_pools.append(pool)
        cp = [p for p in pair_pools if "UniV2" in p["name"] or "vAMM" in p["name"]]
        # reference price: median of vAMM/UniV2 venues (sAMM excluded — wrong curve)
        prices = sorted(price_of(p, base, quote) for p in cp if price_of(p, base, quote))
        if len(prices) < 2:
            continue
        ref = prices[len(prices) // 2]
        for i in range(len(cp)):
            for j in range(len(cp)):
                if i >= j:
                    continue
                a, b = cp[i], cp[j]
                best = best_two_pool_arb(a, b, base, quote, ref)
                if not best:
                    continue
                direction, size_base, gross_usd = best
                # cost stack: gas + flash premium on borrowed principal
                gas_usd = (gas_price_wei * GAS_UNITS / 1e18) * eth_usd
                loan_principal_usd = size_base * eth_usd if base == WETH else size_base * ref
                loan_fee_usd = loan_principal_usd * AAVE_FLASH_FEE
                net_usd = gross_usd - gas_usd - loan_fee_usd
                if net_usd > SAFETY_MARGIN_USD:
                    edges.append({
                        "pair": f"{bs}/{qs}", "direction": direction,
                        "size_base": round(size_base, 6),
                        "gross_usd": round(gross_usd, 4),
                        "gas_usd": round(gas_usd, 4),
                        "loan_fee_usd": round(loan_fee_usd, 4),
                        "net_usd": round(net_usd, 4),
                    })
                    if verbose:
                        print(f"[EDGE] {bs}/{qs} {direction} size={size_base:,.4f} "
                              f"gross=${gross_usd:.2f} net=${net_usd:.2f} — CLEARS FLOOR")
    return edges


def log_event(evt):
    evt["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(evt) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default="https://base-rpc.publicnode.com")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    rpc = RPC(args.rpc, timeout=30, retries=3)
    print(f"[sentinel] armed — rpc={args.rpc} interval={args.interval}s")
    print(f"[sentinel] gate: profit > gas + 0.05% loan fee + ${SAFETY_MARGIN_USD}")
    print(f"[sentinel] log : {LOG_FILE}")

    cycle = 0
    while True:
        cycle += 1
        try:
            gas_price_wei = uint(rpc.call("eth_gasPrice", [])) or 0
            mids = rpc.call("eth_call", [{
                "to": "0x4200000000000000000000000000000000000006",
                "data": "0x" + "d0e30db0"}, "latest"])  # placeholder no-op
            # ETH/USD reference from the deepest pool we scan
            pools_usdc = load_pool(rpc, "ref",
                                   aero_pool(rpc, WETH, USDC, False))
            if pools_usdc:
                br, qr = pool_side(pools_usdc, WETH, USDC)
                eth_usd = (qr / 1e6) / (br / 1e18) if br else 0
            else:
                eth_usd = 0
            edges = scan_once(rpc, gas_price_wei, eth_usd)
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] cycle {cycle}: {len(edges)} actionable edges", flush=True)
            log_event({"cycle": cycle, "edges": edges,
                       "gas_gwei": gas_price_wei / 1e9, "eth_usd": eth_usd})
        except Exception as e:
            print(f"[sentinel] cycle {cycle} error: {e}", flush=True)
            log_event({"cycle": cycle, "error": str(e)})
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
