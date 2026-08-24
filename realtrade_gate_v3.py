#!/usr/bin/env python3
"""
realtrade_gate_v3.py — VERITAS full-auto engine on the HOT WALLET account.

Account: 0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f (key in .hot_secret)
Capital: $17.76 (live, verified)

Every lesson from today baked in:
  - $10 minimum order size (Hyperliquid hard floor)
  - integer sizing when szDecimals == 0
  - IOC entries that actually cross: buy at ask*1.002, sell at bid*0.998
  - hard stop placed with EVERY entry (exchange-enforced, reduce-only)
  - emergency stop keyed to REALIZED losses only (not risk budget)
  - 40% max risk envelope ($7.10 -> capped $7.00 by emergency stop)
  - dynamic multi-position: as many lures as quality + minimums allow
  - rebalance scan every 3 minutes
"""
import json
import os
import signal
import sys
import time
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from funding_scout import analyze

from risk_manager import (
    discover_opportunities, allocate_portfolio, build_position_plan,
    PortfolioState, PositionPlan,
    TOTAL_POOL_USD, MAX_RISK_USD, EMERGENCY_STOP_USD, EFFECTIVE_MAX_RISK_USD,
    MIN_POSITION_USD, MAX_POSITIONS,
)

HOT_WALLET = "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"
SECRET_FILE = os.path.join(HERE, ".hot_secret")
TAKER_FEE = 0.00045
PROFITABILITY_CHECK_INTERVAL_SEC = 180
HEARTBEAT_EVERY = 5  # cycles

state = {
    "emergency_halt": False,
    "cycle_count": 0,
    "last_check": time.time(),
    "realized_pnl_session": 0.0,
    "meta_cache": None,
    "meta_ts": 0.0,
}

signal.signal(signal.SIGINT, lambda s, f: state.update(emergency_halt=True))
signal.signal(signal.SIGTERM, lambda s, f: state.update(emergency_halt=True))


def load_account():
    with open(SECRET_FILE) as f:
        key = f.read().strip()
    acct = Account.from_key(key)
    assert acct.address.lower() == HOT_WALLET.lower(), "key/account mismatch"
    return acct


def get_exchange():
    acct = load_account()
    info = Info(skip_ws=True)
    ex = Exchange(acct)
    return info, ex, acct


def meta(info):
    # cache universe metadata for 10 minutes
    if state["meta_cache"] is None or time.time() - state["meta_ts"] > 600:
        state["meta_cache"] = info.meta()
        state["meta_ts"] = time.time()
    return state["meta_cache"]


def best_prices(info, coin):
    try:
        l2 = info.l2_snapshot(coin)
        bid = float(l2["levels"][0][0]["px"]) if l2.get("levels") and l2["levels"][0] else None
        ask = float(l2["levels"][1][0]["px"]) if l2.get("levels") and l2["levels"][1] else None
        return bid, ask
    except Exception:
        return None, None


def asset_decimals(info, coin):
    for name in meta(info)["universe"]:
        if name["name"] == coin.upper():
            return name.get("szDecimals", 4), name.get("pxDecimals", 6)
    return None, None


def execute_position_plan(plan: PositionPlan, ex, info) -> bool:
    """Enter a position with aggressive IOC crossing + hard stop. LIVE."""
    coin = plan.coin.upper()
    is_buy = plan.direction == "LONG"

    sz_dec, px_dec = asset_decimals(info, coin)
    if sz_dec is None:
        print(f"  [SKIP] {coin}: not in universe")
        return False

    bid, ask = best_prices(info, coin)
    mark = float(info.all_mids().get(coin, 0) or 0)
    if mark <= 0:
        print(f"  [SKIP] {coin}: no mark")
        return False

    # aggressive crossing: pay up / hit down to guarantee IOC fill
    px = (ask * 1.002 if ask else mark * 1.005) if is_buy else \
         (bid * 0.998 if bid else mark * 0.995)
    px = round(px, px_dec)
    sz = int(round(plan.notional_usd / px)) if sz_dec == 0 \
        else round(plan.notional_usd / px, sz_dec)
    if sz <= 0 or sz * px < 10.0:
        print(f"  [SKIP] {coin}: size {sz} below $10 minimum")
        return False

    print(f"[TRADE] {plan.direction} {coin} ${sz*px:.2f} @ {px} "
          f"(bid={bid} ask={ask}) stop={plan.hard_stop_price}")

    try:
        # entry: IOC limit crossing the book
        r = ex.order(coin, is_buy, sz, px, {"limit": {"tif": "Ioc"}}, reduce_only=False)
        status = r.get("status") if isinstance(r, dict) else None
        filled = None
        if status == "ok":
            try:
                st0 = r["response"]["data"]["statuses"][0]
                if "filled" in st0:
                    filled = float(st0["filled"]["avgPx"])
                elif "error" in st0:
                    print(f"  [ORDER ERROR] {st0['error']}")
                    return False
            except Exception:
                pass
        if filled is None:
            print(f"  [NO FILL] status={status}")
            return False
        print(f"  [FILLED] @ {filled}")

        # hard stop: reduce-only trigger SL at the planned stop
        stop_buy = plan.direction == "SHORT"
        stop_px = round(plan.hard_stop_price, px_dec)
        r2 = ex.order(coin, stop_buy, sz, stop_px,
                      {"trigger": {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}},
                      reduce_only=True)
        print(f"  [STOP] {'ok' if (isinstance(r2, dict) and r2.get('status')=='ok') else 'FAILED'} @ {stop_px}")
        return True
    except Exception as e:
        print(f"  [TRADE ERROR] {e}")
        return False


def close_position(coin: str, ex, info) -> bool:
    """Market-close any open position in `coin` (reduce-only, IOC cross)."""
    st = info.user_state(HOT_WALLET)
    pos = None
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        if p.get("coin", "").upper() == coin.upper() and float(p.get("szi", 0) or 0) != 0:
            pos = p
            break
    if pos is None:
        return True
    szi = float(pos["szi"])
    is_buy = szi < 0
    sz = abs(szi)
    sz_dec, px_dec = asset_decimals(info, coin)
    bid, ask = best_prices(info, coin)
    px = (ask * 1.002 if ask else None) if is_buy else (bid * 0.998 if bid else None)
    if px is None:
        return False
    px = round(px, px_dec)
    if sz_dec == 0:
        sz = int(round(sz))
    try:
        r = ex.order(coin.upper(), is_buy, sz, px,
                     {"limit": {"tif": "Ioc"}}, reduce_only=True)
        ok = isinstance(r, dict) and r.get("status") == "ok"
        print(f"  [CLOSE] {coin}: {'ok' if ok else 'FAILED'}")
        return ok
    except Exception as e:
        print(f"  [CLOSE ERROR] {e}")
        return False


def live_positions(info):
    st = info.user_state(HOT_WALLET)
    out = {}
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        if float(p.get("szi", 0) or 0) != 0:
            out[p["coin"].upper()] = p
    return out


def session_realized_pnl(info):
    """All-time realized PnL from fills (session started fresh at $0 profit)."""
    fills = info.user_fills(HOT_WALLET)
    total = 0.0
    for f in fills:
        cpnl = f.get("closedPnl")
        if cpnl:
            total += float(cpnl)
    return total


def cancel_all_stale_stops(ex, info, keep_coins):
    """Cancel reduce-only orders for coins we no longer hold."""
    try:
        for o in info.open_orders(HOT_WALLET):
            if o.get("reduceOnly") and o["coin"].upper() not in keep_coins:
                ex.cancel(o["coin"], o["oid"])
    except Exception:
        pass


def main():
    print("=" * 66)
    print("VERITAS Realtrade Gate v3 — HOT WALLET FULL-AUTO")
    print("=" * 66)
    print(f"account  : {HOT_WALLET}")
    print(f"capital  : ${TOTAL_POOL_USD:.2f}")
    print(f"risk cap : ${EFFECTIVE_MAX_RISK_USD:.2f} (40% envelope / $7 stop)")
    print(f"min pos  : ${MIN_POSITION_USD:.2f} | max positions: {MAX_POSITIONS}")
    print(f"rebalance: every {PROFITABILITY_CHECK_INTERVAL_SEC//60} min")
    print("=" * 66)

    info, ex, acct = get_exchange()

    while not state["emergency_halt"]:
        state["cycle_count"] += 1

        try:
            # --- live account state ---
            st = info.user_state(HOT_WALLET)
            equity = float(st["marginSummary"]["accountValue"])
            positions = live_positions(info)
            realized = session_realized_pnl(info)

            # --- emergency stop: REALIZED loss only ---
            if realized < -EMERGENCY_STOP_USD:
                print(f"\n[EMERGENCY STOP] realized loss ${realized:.2f} "
                      f"exceeds ${EMERGENCY_STOP_USD:.2f} — closing all, halting.")
                for coin in list(positions):
                    close_position(coin, ex, info)
                state["emergency_halt"] = True
                break

            # --- heartbeat ---
            if state["cycle_count"] % HEARTBEAT_EVERY == 1:
                upnl = sum(float(p.get("unrealizedPnl") or 0) for p in positions.values())
                print(f"\n[HEARTBEAT #{state['cycle_count']}] {time.strftime('%H:%M:%S')} "
                      f"equity=${equity:.2f} pos={len(positions)} "
                      f"realized=${realized:.2f} uPnL=${upnl:.2f}")

            # --- rebalance window ---
            if time.time() - state["last_check"] >= PROFITABILITY_CHECK_INTERVAL_SEC:
                state["last_check"] = time.time()
                print(f"\n[REBALANCE #{state['cycle_count']}] scanning...")
                opps = discover_opportunities(equity=equity, top=15)
                plans = allocate_portfolio(opps, max_positions=MAX_POSITIONS)
                want = {p.coin.upper(): p for p in plans}
                have = set(positions.keys())

                # enter new/additional positions
                for coin, plan in want.items():
                    if coin not in have:
                        execute_position_plan(plan, ex, info)

                # exit positions the allocator no longer wants
                for coin in have - set(want.keys()):
                    print(f"  [EXIT] {coin} no longer in allocation")
                    close_position(coin, ex, info)

                cancel_all_stale_stops(ex, info, set(live_positions(info).keys()))

        except Exception as e:
            print(f"[cycle error] {e}")

        time.sleep(30)

    print("\n[SESSION END] halted — awaiting commander return.")


if __name__ == "__main__":
    main()