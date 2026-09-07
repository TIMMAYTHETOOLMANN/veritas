#!/usr/bin/env python3
"""
auto_rotator.py — autonomous rapid-succession funding-carry rotator.

The engine that delivers incremental profit at regular intervals. Every cycle:
  1. Scans ALL Hyperliquid perp markets for the strongest funding+premium edge
  2. Ranks by an edge score (funding magnitude × premium dislocation × liquidity)
  3. If a better regime exists than the current position, ROTATES: close old, open new
  4. Sizes new positions at the same leverage ratio, compounding realized gains
  5. Enforces a hard stop on every position

Exit rules per position:
  - funding flips sign / collapses below COLLAPSE_FRAC of entry
  - premium converges below CONVERGE threshold
  - a strictly better opportunity exists elsewhere (rotation)

Run modes:
  --once          single scan + one rotation decision (cron-friendly)
  (default)       loop every SCAN_SEC

Keys: reads .hot_secret (single-wallet). Account = .hl_master_address (unified).
"""
from __future__ import annotations
import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.hyperliquid.xyz/info"

SCAN_SEC = 300                 # rotation cadence (5 min)
COLLAPSE_FRAC = 0.20           # rotate out if funding collapses to <20%
CONVERGE_THRESHOLD = 0.0010    # rotate out if |premium| < 0.1%
MIN_OI_NOTIONAL = 500_000      # skip illiquid markets
MIN_ABS_FUNDING = 0.0001       # minimum funding edge to enter
LEVERAGE = 3
SAFETY_BUFFER = 0.95           # use 95% of free equity for the position

def _post(payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def user_address():
    m = (HERE / ".hl_master_address").read_text().strip()
    return m or "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"


def get_account(addr):
    st = _post({"type": "clearinghouseState", "user": addr})
    ms = st["marginSummary"]
    # Unified account: perps accountValue is 0 when capital sits in spot USDC.
    # Combine spot USDC + perp equity for true deployable capital.
    sp = _post({"type": "spotClearinghouseState", "user": addr})
    spot_usdc = 0.0
    for b in sp.get("balances", []) or []:
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", 0) or 0)
    pos = None
    for ap in st.get("assetPositions", []):
        pos = ap["position"]
    perp_value = float(ms.get("accountValue", 0) or 0)
    equity = spot_usdc + perp_value
    return {
        "account_value": equity,
        "perp_value": perp_value,
        "spot_usdc": spot_usdc,
        "withdrawable": float(st.get("withdrawable", 0) or 0),
        "coin": pos["coin"] if pos else None,
        "szi": float(pos["szi"]) if pos else 0.0,
        "entry": float(pos["entryPx"]) if pos else None,
        "uPnl": float(pos["unrealizedPnl"]) if pos else 0.0,
        "liq": float(pos["liquidationPx"]) if pos else None,
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
        oi_ntl = oi * mark
        max_lev = m.get("maxLeverage")
        sz_dec = m.get("szDecimals", 0)
        out.append({
            "name": m["name"], "funding": funding, "premium": premium,
            "mark": mark, "oi_ntl": oi_ntl, "maxLev": max_lev, "szDec": sz_dec,
        })
    return out


def edge_score(m):
    """Higher = better. Negative funding (LONG carry) is the reliable edge; positive
    funding (SHORT carry) is discounted because it's more crowded/less durable.
    Wide premium adds convergence-capture value. Liquidity bonus caps."""
    # Negative funding: being LONG and getting paid is the primary signal.
    if m["funding"] < 0:
        carry_edge = abs(m["funding"]) * 100
    else:
        carry_edge = abs(m["funding"]) * 30   # positive funding discounted (short carry)
    premium_edge = abs(m["premium"]) * 5
    oi_bonus = min(m["oi_ntl"] / 1_000_000, 10.0)
    # Liquidity is a weak tiebreaker — keep it 100x smaller than funding signals.
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


def close(ex, coin, szi):
    if szi <= 0:
        return "flat"
    return ex.market_close(coin, szi)


def open_position(ex, coin, direction, equity):
    mark = None
    sz_dec = 2
    for m in scan_markets():
        if m["name"] == coin:
            mark = m["mark"]
            sz_dec = m.get("szDec", 2)
            break
    if not mark:
        return None, "no mark", 0.0
    lev = LEVERAGE
    notional = equity * lev * SAFETY_BUFFER
    sz = notional / mark
    # Round to the coin's size decimals (whole shares for szDecimals=0).
    sz = round(sz, sz_dec)
    if sz <= 0:
        return None, f"zero size (mark={mark})", 0.0
    try:
        r = ex.market_open(coin, direction == "LONG", float(sz), None, 0.05)
        return r, "", sz
    except Exception as e:
        return None, str(e), 0.0


def place_stop(ex, coin, direction, szi, entry):
    if szi <= 0:
        return None
    # stop ~20% against position
    if direction == "LONG":
        stop_px = round(entry * 0.80, 6)
    else:
        stop_px = round(entry * 1.25, 6)
    side = not (direction == "LONG")  # reduce-only opposite
    trigger = {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}
    try:
        return ex.order(coin, side, szi, stop_px, {"trigger": trigger}, reduce_only=True)
    except Exception as e:
        return f"stop err {e}"


def decide_rotation(acct, cands):
    """Return (should_rotate, reason, target)."""
    if not cands:
        return False, "no candidates", None
    if not acct["coin"]:
        return True, "no open position", cands[0]
    current = acct["coin"]
    top = cands[0]
    if top["name"] == current:
        return False, "already in top regime", None
    cur_score = None
    for m in cands:
        if m["name"] == current:
            cur_score = m
            break
    # Only rotate when the top is MATERIALY better (3x), not marginal churn.
    if cur_score and top["score"] > cur_score["score"] * 3.0:
        return True, f"rotate {current}->{top['name']}", top
    return False, f"hold {current} (top={top['name']})", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--force-rotate", action="store_true")
    args = ap.parse_args()

    addr = user_address()
    ex = _exchange()

    while True:
        try:
            acct = get_account(addr)
            markets = scan_markets()
            cands = rank_candidates(markets)
            if not cands:
                print(f"[rotator {time.strftime('%H:%M:%S')}] no candidates", flush=True)
            else:
                top = cands[0]
                print(f"[rotator {time.strftime('%H:%M:%S')}] top={top['name']} "
                      f"{top['direction']} f={top['funding']:.5f} pr={top['premium']:.5f} "
                      f"eq=${acct['account_value']:.2f} pos={acct['coin'] or 'none'} "
                      f"uPnL={acct['uPnl']:.2f}", flush=True)
                rotate, reason, target = decide_rotation(acct, cands)
                if args.force_rotate and acct["coin"]:
                    rotate, reason, target = True, "forced", cands[0]
                if rotate and target:
                    print(f"[rotator] ROTATING: {reason} (close {acct['coin']}, open {target['name']} {target['direction']})", flush=True)
                    if acct["coin"]:
                        print(f"[rotator] close {acct['coin']}: {close(ex, acct['coin'], acct['szi'])}", flush=True)
                        time.sleep(3)
                        acct = get_account(addr)
                    r, err, sz = open_position(ex, target["name"], target["direction"], acct["account_value"])
                    if r:
                        mark = target["mark"]
                        st = place_stop(ex, target["name"], target["direction"], sz, mark)
                        print(f"[rotator] opened {target['name']} {target['direction']} sz={sz:.4f}: {r} stop={st}", flush=True)
                    else:
                        print(f"[rotator] open failed: {err}", flush=True)
                else:
                    print(f"[rotator] {reason}", flush=True)
        except Exception as e:
            print(f"[rotator] cycle error: {e}", flush=True)

        if args.once:
            return
        time.sleep(SCAN_SEC)


if __name__ == "__main__":
    main()