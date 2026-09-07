#!/usr/bin/env python3
"""
funding_monitor.py — autonomous funding-carry position monitor.

Watches the live Hyperliquid funding rate + premium for a named coin/position and
auto-closes on the exit rules, logging every cycle. Designed to run as a background
process (or via cron `--once`).

Exit rules (all thresholds configurable):
  R1 — funding flips positive (or collapses below COLLAPSE_FRAC of entry avg)
  R2 — premium converges (|premium| below CONVERGE_THRESHOLD)
  R3 — unrealized PnL hit (discretionary manual close; stop is exchange-enforced)

Usage:
  python3 funding_monitor.py --coin SKR --entry-funding -0.001 --once        # single check
  python3 funding_monitor.py --coin SKR                                       # loop every 120s
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

API = "https://api.hyperliquid.xyz/info"
COLLAPSE_FRAC = 0.20          # close if funding collapses to <20% of entry
CONVERGE_THRESHOLD = 0.0010   # close if |premium| < 0.1%
POLL_SEC = 120
MAX_POSITIVE_FUNDING = 0.0    # close if funding goes positive at all


def _post(payload: dict) -> dict:
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def get_funding_and_premium(coin: str):
    ctxs = _post({"type": "metaAndAssetCtxs"})
    meta, c = ctxs
    for i, m in enumerate(meta["universe"]):
        if m["name"] == coin and i < len(c):
            ctx = c[i]
            return float(ctx.get("funding", 0)), float(ctx.get("premium", 0)), float(ctx.get("markPx", 0))
    return None, None, None


def get_position(user: str):
    st = _post({"type": "clearinghouseState", "user": user})
    pos = None
    for ap in st.get("assetPositions", []):
        pos = ap["position"]
    return {
        "account_value": float(st["marginSummary"]["accountValue"]),
        "withdrawable": float(st.get("withdrawable", 0) or 0),
        "coin": pos["coin"] if pos else None,
        "szi": pos["szi"] if pos else "0",
        "entry": pos["entryPx"] if pos else None,
        "uPnl": float(pos["unrealizedPnl"]) if pos else 0.0,
        "liq": pos["liquidationPx"] if pos else None,
    }


def decide(funding: float, premium: float, entry_funding: float) -> tuple[bool, str]:
    """Return (should_close, reason)."""
    if funding >= MAX_POSITIVE_FUNDING and entry_funding < 0:
        return True, "funding flipped positive"
    if entry_funding != 0 and abs(funding) < abs(entry_funding) * COLLAPSE_FRAC:
        return True, f"funding collapsed to {abs(funding)/abs(entry_funding):.0%} of entry"
    if premium is not None and abs(premium) < CONVERGE_THRESHOLD:
        return True, f"premium converged ({premium:.6f})"
    return False, ""


def close_position(coin: str, szi: str):
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    from eth_account import Account
    hot_key = (HERE / ".hot_secret").read_text().strip()
    if not hot_key.startswith("0x"):
        hot_key = "0x" + hot_key
    wallet = Account.from_key(hot_key)
    ex = Exchange(wallet, constants.MAINNET_API_URL)
    sz = float(szi)
    if sz <= 0:
        return "no position"
    return ex.market_close(coin, sz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", required=True)
    ap.add_argument("--entry-funding", type=float, required=True)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    user = (HERE / ".hl_master_address").read_text().strip() or "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"

    while True:
        funding, premium, mark = get_funding_and_premium(args.coin)
        pos = get_position(user)
        if funding is None:
            print(f"[monitor] {time.strftime('%H:%M:%S')} no funding data for {args.coin}", flush=True)
        else:
            close, reason = decide(funding, premium, args.entry_funding)
            print(
                f"[monitor] {time.strftime('%H:%M:%S')} {args.coin} funding={funding:.5f} "
                f"premium={premium:.5f} mark={mark:.5f} uPnL={pos['uPnl']:.2f} "
                f"acct=${pos['account_value']:.2f} liq={pos['liq']}",
                flush=True,
            )
            if close and pos["coin"]:
                print(f"[monitor] EXIT SIGNAL: {reason}. Closing {pos['szi']} {args.coin}...", flush=True)
                r = close_position(args.coin, pos["szi"])
                print(f"[monitor] CLOSE RESULT: {r}", flush=True)
                return
        if args.once:
            return
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()