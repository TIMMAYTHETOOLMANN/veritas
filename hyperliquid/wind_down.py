#!/usr/bin/env python3
"""
wind_down.py — VERITAS controlled wind-down: flatten positions, cancel
resting orders, withdraw ALL perp USD on-chain (Arbitrum USDC) to the
hot wallet.

Usage:
  python3 wind_down.py status     # read-only: positions, orders, withdrawable
  python3 wind_down.py flatten    # market-close every open position (reduce-only IOC)
  python3 wind_down.py withdraw   # withdraw_from_bridge full withdrawable -> hot wallet

No secrets in this file — key read from .hot_secret at runtime.
Verification of the on-chain effect is done separately (balance delta on
Arbitrum), never from the API response alone.
"""
import json
import sys

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

HOT_WALLET = "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"
SECRET_FILE = ".hot_secret"


def get_exchange():
    with open(SECRET_FILE) as f:
        key = f.read().strip()
    acct = Account.from_key(key)
    assert acct.address.lower() == HOT_WALLET.lower(), "key/account mismatch"
    info = Info(skip_ws=True)
    return info, Exchange(acct), acct


def live_positions(info):
    st = info.user_state(HOT_WALLET)
    out = {}
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        if float(p.get("szi", 0) or 0) != 0:
            out[p["coin"].upper()] = p
    return out


def cmd_status(info, ex):
    st = info.user_state(HOT_WALLET)
    ms = st["marginSummary"]
    print("equity      :", ms["accountValue"])
    print("withdrawable:", st.get("withdrawable"))
    poss = live_positions(info)
    if poss:
        for c, p in poss.items():
            print("POSITION    :", c, p["szi"], "@", p["entryPx"], "uPnL", p["unrealizedPnl"])
    else:
        print("POSITIONS   : none (flat)")
    oo = info.open_orders(HOT_WALLET)
    if oo:
        for o in oo:
            print("OPEN ORDER  :", o.get("coin"), o.get("side"), o.get("limitPx"),
                  o.get("orderType"), "reduceOnly" if o.get("reduceOnly") else "")
    else:
        print("OPEN ORDERS : none")


def cmd_flatten(info, ex):
    poss = live_positions(info)
    if not poss:
        print("already flat — nothing to close")
        return
    for coin, p in poss.items():
        szi = float(p["szi"])
        is_buy_close = szi < 0          # long -> sell to close
        sz = abs(szi)
        l2 = info.l2_snapshot(coin)
        bid = float(l2["levels"][0][0]["px"]) if l2.get("levels") and l2["levels"][0] else None
        ask = float(l2["levels"][1][0]["px"]) if l2.get("levels") and l2["levels"][1] else None
        if is_buy_close:
            px = round((ask or 0) * 1.002, 6)
        else:
            px = round((bid or 0) * 0.998, 6)
        if px <= 0:
            print("no price for", coin, "— SKIP (manual intervention)")
            continue
        print(f"[CLOSE] {coin} sz={sz} px={px} (reduce-only IOC)")
        r = ex.order(coin, is_buy_close, sz, px,
                     {"limit": {"tif": "Ioc"}}, reduce_only=True)
        print("  response:", json.dumps(r)[:400])
    # cancel any orphaned reduce-only stops
    for o in info.open_orders(HOT_WALLET):
        if o.get("reduceOnly"):
            print("[CANCEL STOP]", o.get("coin"), o.get("oid"))
            ex.cancel(o["coin"], o["oid"])
    print("--- post-flatten state ---")
    cmd_status(info, ex)


def cmd_withdraw(info, ex):
    st = info.user_state(HOT_WALLET)
    wd = float(st["withdrawable"])
    if wd < 1.0:
        print(f"withdrawable ${wd:.4f} below $1 bridge minimum — nothing to withdraw")
        return
    amount = int(wd * 100) / 100.0  # round down to cents
    print(f"[WITHDRAW] ${amount:.2f} -> {HOT_WALLET} ( Arbitrum USDC, withdraw3 )")
    r = ex.withdraw_from_bridge(amount, HOT_WALLET)
    print("  response:", json.dumps(r)[:400])
    print("  NOTE: verify on-chain USDC balance on Arbitrum — not this response.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("status", "flatten", "withdraw"):
        print(__doc__)
        sys.exit(1)
    info, ex, _ = get_exchange()
    {"status": cmd_status, "flatten": cmd_flatten, "withdraw": cmd_withdraw}[sys.argv[1]](info, ex)


if __name__ == "__main__":
    main()
