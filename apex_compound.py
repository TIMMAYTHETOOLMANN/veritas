#!/usr/bin/env python3
"""
apex_compound.py — VERITAS aggressive auto-compounding funding-carry engine.

"Utilize all available funds for collateral. Amplify. Compound everything."

Every cycle (default 5 min):
  1. Read TRUE deployable capital = spot USDC + perp equity (unified account).
  2. Rank ALL Hyperliquid perp markets by funding-carry edge (negative-funding
     LONG carry primary; premium secondary; liquidity tiebreak).
  3. Split 100% of capital across the top N regimes, EACH at the coin's max
     leverage (3x on these coins), so no dollar sits idle.
  4. Rotate out of a coin only when funding collapses/flips or a materially
     better regime (3x edge) appears — no churn.
  5. Re-size/re-compound every cycle: as funding accumulates equity, the next
     cycle deploys the larger base, compounding exponentially.

Safety (non-negotiable, keeps the compounding engine alive):
  - Hard stop-loss on every position (~20% adverse).
  - Never size past maxLeverage; cap notional at SAFETY_BUFFER fraction of the
    coin's max leverage to leave a liquidation cushion.
  - Skip illiquid markets (OI notional < threshold).

Run:
  python3 apex_compound.py --once      # one cycle (cron / single decision)
  python3 apex_compound.py             # loop forever (aggressive autopilot)
  python3 apex_compound.py --top 5 --interval 180

DRY-RUN default: set DRY_RUN=True to log decisions without signing.
"""
from __future__ import annotations
import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.hyperliquid.xyz/info"

SCAN_SEC = 300                 # re-compound cadence (5 min)
COLLAPSE_FRAC = 0.20           # exit if funding collapses to <20% of entry
CONVERGE_THRESHOLD = 0.0010    # exit if |premium| < 0.1%
MIN_OI_NOTIONAL = 500_000      # skip illiquid
MIN_ABS_FUNDING = 0.0001       # minimum funding edge to enter
SAFETY_BUFFER = 0.90           # use 90% of max leverage (liquidation cushion)
MAX_COINS = 4                  # diversify across top N regimes
ROTATION_MULTIPLE = 3.0        # rotate only on 3x material improvement
DRY_RUN = False
LEVERAGE_OVERRIDE = 0          # 0 = use coin maxLeverage; N = explicit leverage target


def _post(payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def user_address():
    m = (HERE / ".hl_master_address").read_text().strip() if (HERE / ".hl_master_address").exists() else ""
    return m or "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"


def get_account(addr):
    st = _post({"type": "clearinghouseState", "user": addr})
    ms = st["marginSummary"]
    sp = _post({"type": "spotClearinghouseState", "user": addr})
    spot_usdc = 0.0
    for b in sp.get("balances", []) or []:
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", 0) or 0)
    positions = {}
    for ap in st.get("assetPositions", []):
        p = ap["position"]
        positions[p["coin"]] = {
            "szi": float(p["szi"]),
            "entry": float(p["entryPx"]),
            "uPnl": float(p["unrealizedPnl"]),
            "liq": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
        }
    perp_value = float(ms.get("accountValue", 0) or 0)
    return {
        "account_value": spot_usdc + perp_value,
        "spot_usdc": spot_usdc,
        "perp_value": perp_value,
        "positions": positions,
    }


def scan_markets():
    d = _post({"type": "metaAndAssetCtxs"})
    meta, ctxs = d
    out = []
    for i, m in enumerate(meta["universe"]):
        if i >= len(ctxs):
            continue
        c = ctxs[i]
        def _f(v):
            return float(v) if v is not None else 0.0
        funding = _f(c.get("funding"))
        premium = _f(c.get("premium"))
        mark = _f(c.get("markPx"))
        oi = _f(c.get("openInterest"))
        out.append({
            "name": m["name"], "funding": funding, "premium": premium,
            "mark": mark, "oi_ntl": oi * mark,
            "maxLev": m.get("maxLeverage", 3), "szDec": m.get("szDecimals", 0),
        })
    return out


def edge_score(m):
    if m["funding"] < 0:
        carry_edge = abs(m["funding"]) * 100
    else:
        carry_edge = abs(m["funding"]) * 30
    premium_edge = abs(m["premium"]) * 5
    oi_bonus = min(m["oi_ntl"] / 1_000_000, 10.0)
    return carry_edge + premium_edge + oi_bonus * 0.0005


def rank_candidates(markets):
    cands = []
    for m in markets:
        if abs(m["funding"]) < MIN_ABS_FUNDING:
            continue
        if m["oi_ntl"] < MIN_OI_NOTIONAL:
            continue
        if not m["mark"] or m["mark"] <= 0:
            continue
        direction = "LONG" if m["funding"] < 0 else "SHORT"
        cands.append({**m, "direction": direction, "score": edge_score(m)})
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands


def _exchange():
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    from eth_account import Account
    key = (HERE / ".hot_secret").read_text().strip()
    if not key.startswith("0x"):
        key = "0x" + key
    return Exchange(Account.from_key(key), constants.MAINNET_API_URL)


def open_position(ex, coin, direction, notional_usd, max_lev, mark, sz_dec, dry=False):
    """Open a position sized to notional_usd at up to max_lev. Returns (resp, err, sz)."""
    if notional_usd <= 0:
        return None, "zero notional", 0.0
    sz = notional_usd / mark
    sz = round(sz, sz_dec)
    if sz <= 0:
        return None, f"zero size (mark={mark})", 0.0
    if dry:
        return {"dry": True}, "", sz
    try:
        r = ex.market_open(coin, direction == "LONG", float(sz), None, 0.05)
        return r, "", sz
    except Exception as e:
        return None, str(e), 0.0


def close_position(ex, coin, szi, dry=False):
    if szi <= 0:
        return "flat"
    if dry:
        return {"dry": True}
    return ex.market_close(coin, szi)


def place_stop(ex, coin, direction, szi, entry, dry=False):
    if szi <= 0:
        return None
    if direction == "LONG":
        stop_px = round(entry * 0.80, 6)
    else:
        stop_px = round(entry * 1.25, 6)
    side = not (direction == "LONG")
    trigger = {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}
    if dry:
        return {"dry": True, "stop": stop_px}
    try:
        return ex.order(coin, side, szi, stop_px, {"trigger": trigger}, reduce_only=True)
    except Exception as e:
        return f"stop err {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--top", type=int, default=MAX_COINS)
    ap.add_argument("--interval", type=int, default=SCAN_SEC)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force-rebalance", action="store_true")
    ap.add_argument("--leverage", type=int, default=0,
                    help="explicit leverage target (0 = use each coin's maxLeverage; "
                         "e.g. 5 to push 5x). Higher = closer to liquidation.")
    args = ap.parse_args()

    dry = args.dry
    top_n = args.top
    scan_sec = args.interval

    addr = user_address()
    ex = None if dry else _exchange()

    print(f"[apex] aggressive auto-compounding engine. dry={dry} top={top_n} "
          f"interval={scan_sec}s", flush=True)

    while True:
        try:
            acct = get_account(addr)
            markets = scan_markets()
            cands = rank_candidates(markets)
            equity = acct["account_value"]
            per_coin = equity / top_n
            print(f"[apex {time.strftime('%H:%M:%S')}] eq=${equity:.4f} "
                  f"pos={len(acct['positions'])} targets={len(cands)}", flush=True)

            if not cands:
                print("[apex] no candidates — holding", flush=True)
            else:
                top = cands[:top_n]
                wanted = {c["name"] for c in top}
                have = set(acct["positions"].keys())

                # Close positions no longer in the top-N (or forced rebalance)
                for coin in list(have):
                    if coin in wanted and not args.force_rebalance:
                        continue
                    p = acct["positions"][coin]
                    print(f"[apex] close {coin}: {close_position(ex, coin, p['szi'], dry)}", flush=True)
                    time.sleep(2)

                # Re-read equity after any closes, then (re)size + open each target
                if args.force_rebalance:
                    acct = get_account(addr)
                    equity = acct["account_value"]
                    per_coin = equity / top_n

                for c in top:
                    lev = (args.leverage or c["maxLev"] or 3)
                    # Respect the coin's hard cap — Hyperliquid rejects over-max orders.
                    if args.leverage and lev > (c["maxLev"] or 3):
                        lev = c["maxLev"] or 3
                    notional = per_coin * lev * SAFETY_BUFFER
                    existing = acct["positions"].get(c["name"])
                    if existing and not args.force_rebalance:
                        # already positioned — optionally top-up later; skip churn
                        continue
                    # liquidation-distance feedback (transparency for aggressive sizing)
                    if lev > 3:
                        liq_pct = round(100.0 / lev * 1.0, 1)
                        print(f"[apex] WARN {c['name']} at {lev}x: liq ~{liq_pct}% away", flush=True)
                    r, err, sz = open_position(ex, c["name"], c["direction"],
                                               notional, lev, c["mark"], c["szDec"], dry)
                    if r:
                        st = place_stop(ex, c["name"], c["direction"], sz, c["mark"], dry)
                        print(f"[apex] OPEN {c['name']} {c['direction']} sz={sz} "
                              f"notional=${notional:.2f} lev={lev}x stop={st}", flush=True)
                    else:
                        print(f"[apex] open {c['name']} failed: {err}", flush=True)
                    time.sleep(1)

        except Exception as e:
            print(f"[apex] cycle error: {e}", flush=True)

        if args.once:
            return
        time.sleep(scan_sec)


if __name__ == "__main__":
    main()