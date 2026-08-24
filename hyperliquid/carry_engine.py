#!/usr/bin/env python3
"""
carry_engine.py — VERITAS Full-Auto Funding Carry Engine
Single-file, no passive, continuous execution.

Strategy:
- Every 60s: pull funding rates for all perps
- Pick the coin with most extreme persistent negative funding (for LONG)
  or positive funding (for SHORT)
- If no position: enter immediately with 3x leverage, hard stop at 15%
- If position exists: check if funding is still favourable; if not, close
- Hard stop is exchange-enforced (reduce-only trigger SL)
- Emergency halt if SESSION realized loss > $7 (baseline captured at start)
- Log everything to carry_engine.log

Deployment notes (2026-08-24, verified live):
- Lives in hyperliquid/ per repo rules; .hot_secret is read from repo root
  (parent dir). Run with python3.
- HL account equity is $0.008 (drained to Arbitrum 2026-08-23); $15.70 USDC
  sits on-chain at the hot wallet. Engine runs and skips entries until the
  account is funded; enter_position() gates on equity >= $1.
"""

import os
import sys
import time
import signal
from datetime import datetime
from typing import Optional, Dict, List, Tuple

# -------- configuration --------
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

HOT_WALLET = "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"
SECRET_FILE = os.path.join(os.path.dirname(HERE), ".hot_secret")

TAKER_FEE = 0.00045
MAX_LEVERAGE = 3          # 3x for alts (ACE, MOVE, PURR etc.)
STOP_DISTANCE = 0.15      # 15% stop – aggressive but keeps risk per position ~$1.75
EMERGENCY_STOP_LOSS = 7.0 # halt if session realized loss > $7
MIN_NOTIONAL = 10.0       # Hyperliquid min order size
REBALANCE_INTERVAL = 60   # seconds – scan every minute
HEARTBEAT_INTERVAL = 300  # print summary every 5 min
FUNDING_ENTER_THRESHOLD = 0.0001   # |funding| must exceed this to enter
FUNDING_EXIT_THRESHOLD  = 0.00005  # close when |funding| decays below this

# -------- logging --------
LOG_FILE = os.path.join(HERE, "carry_engine.log")
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# -------- signal handling --------
emergency_halt = False
def signal_handler(sig, frame):
    global emergency_halt
    log(f"Signal {sig} received – halting")
    emergency_halt = True
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# -------- Hyperliquid SDK (bare minimum, no external deps) --------
try:
    from eth_account import Account
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
except ImportError:
    log("ERROR: missing hyperliquid-python-sdk. Run: pip install hyperliquid-python-sdk")
    sys.exit(1)

def load_account():
    if not os.path.isfile(SECRET_FILE):
        log(f"ERROR: secret file not found: {SECRET_FILE}")
        sys.exit(1)
    with open(SECRET_FILE) as f:
        key = f.read().strip()
    acct = Account.from_key(key)
    if acct.address.lower() != HOT_WALLET.lower():
        log(f"ERROR: key derives {acct.address}, expected {HOT_WALLET}")
        sys.exit(1)
    return acct

acct = load_account()
info = Info(skip_ws=True)
ex = Exchange(acct)

# -------- baseline for session-scoped emergency stop --------
# get_realized_pnl() is LIFETIME (all fills ever). The emergency stop must
# be session-scoped or a long-dead losing history would halt the engine on
# launch: baseline at start, halt when (current - baseline) < -$7.
def get_realized_pnl() -> float:
    """Cumulative realized PnL from fills (lifetime)."""
    try:
        fills = info.user_fills(HOT_WALLET)
        total = 0.0
        for f in fills:
            cpnl = f.get("closedPnl")
            if cpnl:
                total += float(cpnl)
        return total
    except Exception:
        return 0.0

BASELINE_REALIZED_PNL = get_realized_pnl()
log(f"session baseline realized PnL: ${BASELINE_REALIZED_PNL:.4f} "
    f"(emergency stop at -${EMERGENCY_STOP_LOSS:.2f} from this point)")

def session_realized_pnl() -> float:
    return get_realized_pnl() - BASELINE_REALIZED_PNL

# -------- funding scanner --------
def fetch_all_funding() -> List[Tuple[str, float, float, float]]:
    """Returns list of (coin, funding_rate, open_interest, mark_price)"""
    try:
        markets = info.meta_and_asset_ctxs()
        universe = markets[0]["universe"]
        ctxs = markets[1]
        out = []
        for name, ctx in zip(universe, ctxs):
            if name.get("isDelisted"):
                continue
            coin = name["name"]
            funding = float(ctx.get("funding", 0.0))
            oi = float(ctx.get("openInterest", 0.0))
            mark = float(ctx.get("markPx", 0.0))
            if mark > 0 and oi >= 1_000_000:  # at least $1M OI
                out.append((coin, funding, oi, mark))
        return out
    except Exception as e:
        log(f"funding scan error: {e}")
        return []

def pick_best_opportunity(coins):
    """
    Returns (coin, direction, entry_price, funding_rate)
    direction = 'LONG' if funding < -FUNDING_ENTER_THRESHOLD (shorts pay longs)
    direction = 'SHORT' if funding > +FUNDING_ENTER_THRESHOLD
    """
    best = None
    best_score = 0.0
    for coin, funding, oi, mark in coins:
        abs_f = abs(funding)
        if abs_f < FUNDING_ENTER_THRESHOLD:
            continue
        # Score: persistence indicator (we want sustained extremes)
        # We don't have 72h history here, but we can use funding as proxy
        score = abs_f * (oi / 1e6) ** 0.3  # weight OI sub-linearly
        if score > best_score:
            best_score = score
            direction = "LONG" if funding < 0 else "SHORT"
            best = (coin, direction, mark, funding)
    return best

# -------- position management --------
def get_positions() -> Dict[str, Dict]:
    """Returns dict of coin -> position dict"""
    try:
        st = info.user_state(HOT_WALLET)
        pos = {}
        for ap in st.get("assetPositions", []):
            p = ap.get("position", {})
            sz = float(p.get("szi", 0.0))
            if sz != 0.0:
                pos[p["coin"]] = {
                    "sz": sz,
                    "entry": float(p.get("entryPx", 0.0)),
                    "uPnL": float(p.get("unrealizedPnl", 0.0)),
                    "liq": float(p.get("liquidationPx", 0.0) or 0.0),
                }
        return pos
    except Exception as e:
        log(f"get_positions error: {e}")
        return {}

def get_equity() -> float:
    try:
        st = info.user_state(HOT_WALLET)
        return float(st["marginSummary"]["accountValue"])
    except Exception:
        return 0.0

def asset_decimals(coin: str) -> Tuple[int, int]:
    try:
        meta = info.meta()
        for name in meta["universe"]:
            if name["name"] == coin:
                return name.get("szDecimals", 4), name.get("pxDecimals", 6)
    except Exception:
        pass
    return 4, 6

def best_prices(coin: str):
    try:
        l2 = info.l2_snapshot(coin)
        levels = l2.get("levels", [[], []])
        bid = float(levels[0][0]["px"]) if levels[0] else None
        ask = float(levels[1][0]["px"]) if levels[1] else None
        return bid, ask
    except Exception:
        return None, None

def place_order(coin: str, is_buy: bool, sz: float, px: float, reduce_only: bool = False):
    try:
        order_type = {"limit": {"tif": "Ioc"}}
        return ex.order(coin, is_buy, sz, px, order_type, reduce_only=reduce_only)
    except Exception as e:
        log(f"order error: {e}")
        return None

def place_stop(coin: str, is_buy: bool, sz: float, stop_px: float):
    try:
        return ex.order(coin, is_buy, sz, stop_px,
                        {"trigger": {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}},
                        reduce_only=True)
    except Exception as e:
        log(f"stop error: {e}")
        return None

def close_position(coin: str) -> bool:
    pos = get_positions().get(coin)
    if not pos:
        return True
    sz = abs(pos["sz"])
    is_buy = pos["sz"] < 0  # short -> buy back
    sz_dec, px_dec = asset_decimals(coin)
    bid, ask = best_prices(coin)
    px = (ask * 1.002 if ask else None) if is_buy else (bid * 0.998 if bid else None)
    if px is None:
        return False
    px = round(px, px_dec)
    if sz_dec == 0:
        sz = int(round(sz))
    r = place_order(coin, is_buy, sz, px, reduce_only=True)
    ok = isinstance(r, dict) and r.get("status") == "ok"
    if ok:
        log(f"CLOSE {coin}: ok")
    else:
        log(f"CLOSE {coin}: failed ({r})")
    return ok

def enter_position(coin: str, direction: str, entry_price: float, funding_rate: float) -> bool:
    equity = get_equity()
    if equity < 1.0:
        log(f"equity too low (${equity:.2f}) – skipping entry")
        return False

    leverage = MAX_LEVERAGE

    # Risk per position: we want to use nearly all $7 risk budget, so notional = 7 / STOP_DISTANCE
    # With stop at 15%, notional = 7 / 0.15 ≈ 46.67, margin = 46.67/3 ≈ 15.56
    # This is > equity, so we cap notional to equity * leverage * 0.9 (leave buffer)
    max_notional = equity * leverage * 0.9
    risk_budget = min(7.0, max_notional * STOP_DISTANCE)
    notional = risk_budget / STOP_DISTANCE
    notional = min(notional, max_notional)
    if notional < MIN_NOTIONAL:
        log(f"notional ${notional:.2f} below ${MIN_NOTIONAL:.2f} – skipping")
        return False

    sz_dec, px_dec = asset_decimals(coin)
    bid, ask = best_prices(coin)
    if direction == "LONG":
        is_buy = True
        px = ask * 1.002 if ask else entry_price * 1.005
    else:
        is_buy = False
        px = bid * 0.998 if bid else entry_price * 0.995
    px = round(px, px_dec)

    if sz_dec == 0:
        sz = int(round(notional / px))
    else:
        sz = round(notional / px, sz_dec)
    actual_notional = sz * px
    if actual_notional < MIN_NOTIONAL:
        log(f"size {sz} gives ${actual_notional:.2f} below minimum")
        return False

    # place entry
    log(f"ENTER {direction} {coin} ${actual_notional:.2f} @ {px} (bid={bid} ask={ask})")
    r = place_order(coin, is_buy, sz, px, reduce_only=False)
    if not (isinstance(r, dict) and r.get("status") == "ok"):
        log(f"entry failed: {r}")
        return False

    # parse fill price
    filled_px = None
    try:
        st0 = r["response"]["data"]["statuses"][0]
        if "filled" in st0:
            filled_px = float(st0["filled"]["avgPx"])
    except Exception:
        filled_px = None
    if filled_px is None:
        log("entry accepted but no fill price? — relying on exchange stop next cycle")
        # still place a protective stop at the intended entry reference
        filled_px = px
    # place hard stop
    if direction == "LONG":
        stop_px = filled_px * (1 - STOP_DISTANCE)
        stop_buy = False
    else:
        stop_px = filled_px * (1 + STOP_DISTANCE)
        stop_buy = True
    stop_px = round(stop_px, px_dec)
    r2 = place_stop(coin, stop_buy, sz, stop_px)
    ok_stop = isinstance(r2, dict) and r2.get("status") == "ok"
    log(f"STOP {'ok' if ok_stop else 'failed'} @ {stop_px}")
    return True

# -------- main loop --------
def main():
    log("=" * 60)
    log("CARRY ENGINE STARTED – FULL AUTO, NO PASSIVE")
    log(f"account: {HOT_WALLET}")
    log(f"emergency stop: ${EMERGENCY_STOP_LOSS:.2f} session realized loss "
        f"(baseline ${BASELINE_REALIZED_PNL:.4f})")
    log(f"rebalance: every {REBALANCE_INTERVAL}s, leverage: {MAX_LEVERAGE}x, "
        f"stop: {STOP_DISTANCE*100:.0f}%")
    log("=" * 60)

    cycle = 0
    last_heartbeat = 0.0
    active_coin = None

    while not emergency_halt:
        cycle += 1
        try:
            equity = get_equity()
            realized = session_realized_pnl()
            if realized < -EMERGENCY_STOP_LOSS:
                log(f"EMERGENCY STOP: session realized loss ${realized:.2f} < -${EMERGENCY_STOP_LOSS:.2f}")
                # close all positions
                for coin in list(get_positions().keys()):
                    close_position(coin)
                break

            # funding scan
            coins = fetch_all_funding()
            if not coins:
                log("no funding data")
                time.sleep(REBALANCE_INTERVAL)
                continue

            # pick best
            best = pick_best_opportunity(coins)
            current_positions = get_positions()
            active_coin = next(iter(current_positions.keys())) if current_positions else None

            if best is None:
                # funding regime weak everywhere — close if our position's
                # funding has decayed below the exit threshold
                if active_coin:
                    ours = next(((c, f, oi, m) for c, f, oi, m in coins if c == active_coin), None)
                    if ours is None or abs(ours[1]) < FUNDING_EXIT_THRESHOLD:
                        log(f"funding regime gone – closing {active_coin}")
                        close_position(active_coin)
                        active_coin = None
                time.sleep(REBALANCE_INTERVAL)
                continue

            coin, direction, entry, funding = best
            log(f"TOP: {direction} {coin} funding={funding*100:.4f}%/hr")

            # if we have a position on a different coin, close it first
            if active_coin and active_coin != coin:
                close_position(active_coin)
                active_coin = None
                time.sleep(2)

            # if no position, enter
            if not active_coin:
                enter_position(coin, direction, entry, funding)
                # wait a beat for fill
                time.sleep(3)
            else:
                # we have a position on this coin – check if funding still favourable
                pos = current_positions.get(coin)
                if pos:
                    # check if we are on the right side
                    is_long = pos["sz"] > 0
                    if (direction == "LONG" and not is_long) or (direction == "SHORT" and is_long):
                        log(f"funding flipped – closing {coin}")
                        close_position(coin)
                        # then re-enter with new direction next cycle
                    elif abs(funding) < FUNDING_EXIT_THRESHOLD:
                        log(f"funding weakened below exit threshold – closing {coin}")
                        close_position(coin)
                    else:
                        # funding still favourable – hold
                        pass

            # heartbeat
            now = time.time()
            if now - last_heartbeat > HEARTBEAT_INTERVAL:
                last_heartbeat = now
                pos = get_positions()
                upnl = sum(p["uPnL"] for p in pos.values())
                log(f"HEARTBEAT: equity=${equity:.2f} session_realized=${realized:.2f} "
                    f"uPnL=${upnl:.2f} | positions: {len(pos)}")

            time.sleep(REBALANCE_INTERVAL)

        except Exception as e:
            log(f"cycle error: {e}")
            time.sleep(REBALANCE_INTERVAL)

    log("ENGINE HALTED")
    log("=" * 60)

if __name__ == "__main__":
    main()
