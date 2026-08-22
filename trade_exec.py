# trade_exec.py — Hyperliquid order execution via the official Python SDK.
#
# SECURITY MODEL:
#   - Key lives ONLY in .hl_secret (repo root), hex string, chmod 600,
#     auto-added to .gitignore. NEVER paste keys into chat or CLI args.
#   - DRY-RUN is the default. --execute is a separate explicit flag.
#   - Everything printed before and after submission.
#
# Commands:
#   python trade_exec.py secret-setup          # generate a fresh wallet
#   python trade_exec.py address               # print wallet address
#   python trade_exec.py balance               # account value + positions
#   python trade_exec.py order --coin PURR --side short --notional 100   # dry
#   python trade_exec.py order --coin PURR --side short --notional 100 --execute
#   python trade_exec.py close --coin PURR                          # dry
#   python trade_exec.py close --coin PURR --execute
#   python trade_exec.py watch --coin PURR [--interval 60]         # monitor
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.path.join(HERE, ".hl_secret")

TAKER_FEE = 0.00045  # 0.045%


def load_secret():
    if not os.path.isfile(SECRET_FILE):
        return None
    with open(SECRET_FILE) as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                return s
    return None


def require_sdk():
    from eth_account import Account
    secret = load_secret()
    if not secret:
        print(f"no secret at {SECRET_FILE} — run: python trade_exec.py secret-setup")
        sys.exit(1)
    acct = Account.from_key(secret)
    return acct


def get_exchange():
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    secret = load_secret()
    acct = Account.from_key(secret)
    info = Info(skip_ws=True)
    ex = Exchange(acct)          # SDK 0.24: base_url optional, not Info
    return info, ex, acct


def cmd_secret_setup(args=None):
    from eth_account import Account
    if load_secret() is not None:
        print("secret already exists — refusing to overwrite")
        return 1
    acct = Account.create()
    with open(SECRET_FILE, "w") as f:
        f.write(acct.key.hex() + "\n")
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    gi = os.path.join(HERE, ".gitignore")
    if os.path.isfile(gi):
        with open(gi) as f:
            cur = f.read()
        if ".hl_secret" not in cur:
            with open(gi, "a") as f:
                f.write("\n.hl_secret\n")
    print("NEW TRADING WALLET CREATED")
    print(f"  address : {acct.address}")
    print(f"  secret  : {SECRET_FILE}")
    print("  fund it : send USDC on Arbitrum to this address, then")
    print("             https://app.hyperliquid.xyz → Deposit")
    return 0


def cmd_address(args=None):
    acct = require_sdk()
    print(acct.address)
    return 0


def cmd_balance(args=None):
    info, ex, acct = get_exchange()
    st = info.user_state(acct.address)
    ms = st.get("marginSummary") or {}
    print(f"address        : {acct.address}")
    print(f"account value  : {ms.get('accountValue')}")
    print(f"withdrawable   : {ms.get('withdrawable')}")
    positions = []
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        if float(p.get("szi", 0) or 0) != 0:
            positions.append(p)
    if positions:
        print(f"\n{'coin':<10}{'size':>14}{'entry':>14}{'value':>12}{'uPnL':>12}")
        for p in positions:
            print(f"{p['coin']:<10}{float(p['szi']):>14.6g}"
                  f"{float(p['entryPx'] or 0):>14.6g}"
                  f"{float(p['positionValue'] or 0):>12.2f}"
                  f"{float(p['unrealizedPnl'] or 0):>12.4f}")
    else:
        print("no open positions")
    # frontend tiers for margin usage context
    return 0


def _get_sz_decimals(info, coin):
    meta = info.meta()
    for name in meta["universe"]:
        if name["name"] == coin.upper():
            return name.get("szDecimals", 4)
    return None


def _get_mark(info, coin):
    mids = info.all_mids()
    return float(mids.get(coin.upper(), 0) or 0)


def cmd_order(args):
    info, ex, acct = get_exchange()
    coin = args.coin.upper()
    sz_dec = _get_sz_decimals(info, coin)
    if sz_dec is None:
        print(f"unknown coin: {coin}")
        return 1
    mark = _get_mark(info, coin)
    if mark <= 0:
        print("no mark price")
        return 1
    # taker crossing: pay the spread
    px = args.price or (mark * (1.001 if args.side == "short" else 0.999))
    sz = round(args.notional / px, sz_dec)
    if sz <= 0:
        print("size rounds to zero at this notional/price")
        return 1
    is_buy = args.side == "long"

    est_fee = args.notional * TAKER_FEE
    print(f"[{'EXECUTE' if args.execute else 'DRY-RUN'}] "
          f"{args.side.upper()} {coin}")
    print(f"  size  : {sz} {coin} ({args.notional:.2f} USD notional)")
    print(f"  price : {px:.10g} (mark {mark:.10g})")
    print(f"  est. taker fee : ${est_fee:.4f}")

    if not args.execute:
        print("\n  dry run only — re-run with --execute to sign and submit")
        return 0

    # IOC limit that crosses the book = taker fill, no resting order
    result = ex.order(coin, is_buy, sz, px, {"limit": {"tif": "Ioc"}},
                      reduce_only=False)
    print("\n  submit result:")
    print(json.dumps(result, indent=2, default=str))
    status = (result.get("status") if isinstance(result, dict) else None)
    filled_px = None
    if status == "ok":
        try:
            st0 = result["response"]["data"]["statuses"][0]
            if "filled" in st0:
                filled_px = float(st0["filled"]["avgPx"])
                print(f"  FILLED @ {filled_px} "
                      f"(size {st0['filled']['totalSz']})")
            elif "error" in st0:
                print(f"  order error: {st0['error']}")
                return 1
        except Exception:
            pass

    # ---- hard stop: reduce-only trigger SL at the 48h-extreme-beyond price
    if args.stop:
        entry = filled_px or px
        stop_px = float(args.stop)
        # for a LONG, the stop SELLs when price falls to triggerPx;
        # for a SHORT, the stop BUYs back when price rises
        stop_buy = args.side == "short"
        ot = {"trigger": {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}}
        try:
            r2 = ex.order(coin, stop_buy, sz, stop_px, ot, reduce_only=True)
            print(f"  hard stop placed @ {stop_px}: "
                  f"{json.dumps(r2.get('status'))}")
        except Exception as e:
            print(f"  WARNING: stop placement failed: {e} — SET IT MANUALLY")
    return 0


def cmd_close(args):
    info, ex, acct = get_exchange()
    coin = args.coin.upper()
    st = info.user_state(acct.address)
    pos = None
    for ap in st.get("assetPositions", []):
        p = ap.get("position", {})
        if p.get("coin", "").upper() == coin and float(p.get("szi", 0) or 0) != 0:
            pos = p
            break
    if pos is None:
        print(f"no open position in {coin}")
        return 1
    szi = float(pos["szi"])
    is_buy = szi < 0          # close short -> buy back
    sz = abs(szi)
    mark = _get_mark(info, coin)
    px = args.price or (mark * (1.001 if is_buy else 0.999))
    print(f"[{'EXECUTE' if args.execute else 'DRY-RUN'}] CLOSE {coin}")
    print(f"  position : {szi:+g} {coin} @ entry {pos.get('entryPx')}")
    print(f"  uPnL     : {pos.get('unrealizedPnl')}")
    print(f"  close    : {'BUY' if is_buy else 'SELL'} {sz} @ {px:.10g} (reduce-only)")
    if not args.execute:
        print("\n  dry run only — re-run with --execute")
        return 0
    from hyperliquid.utils.types import OrderType
    result = ex.order(coin, is_buy, sz, px, OrderType.ORDER_LIMIT,
                      reduce_only=True)
    print(json.dumps(result, indent=2, default=str))
    return 0


def spot_mark(info, coin):
    try:
        sd = info.spot_meta_and_asset_ctxs()
        for row in sd[1]:
            if row.get("coin", "").split("/")[0].upper() == coin.upper():
                px = row.get("markPx")
                return float(px) if px else None
    except Exception:
        return None
    return None


def cmd_withdraw(args):
    """Withdraw USDC from the perps account back to our own wallet (or --to).
    Default destination is OUR address — funds can only leave to a
    non-controlled address if you explicitly pass --to."""
    info, ex, acct = get_exchange()
    dest = args.to or acct.address
    st = info.user_state(acct.address)
    ms = st.get("marginSummary") or {}
    avail = float(ms.get("withdrawable") or 0)
    print(f"[{'EXECUTE' if args.execute else 'DRY-RUN'}] WITHDRAW")
    print(f"  withdrawable : {avail} USDC")
    print(f"  amount       : {args.amount}")
    print(f"  destination  : {dest}"
          + ("  (OUR WALLET)" if dest == acct.address else "  (!! EXTERNAL — you typed this)"))
    if args.amount > avail:
        print("  amount exceeds withdrawable — aborting")
        return 1
    if not args.execute:
        print("\n  dry run only — re-run with --execute")
        return 0
    # SDK usd transfer: sign2 with perps destination
    from hyperliquid.utils.types import SpotSymbol, SpotMeta
    is_perp = True
    result = ex.usdc_transfer(dest, f"{args.amount}", is_perp)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_watch(args):
    """Exit-rule monitor for the funding-short thesis:
      1. funding collapsed toward zero -> carry gone
      2. |premium| < 0.3%              -> convergence collected
      3. prints every tick; RUNS FOREVER until Ctrl-C.
    Combine with a hard stop set in the UI at entry.
    """
    info, ex, acct = get_exchange()
    coin = args.coin.upper()
    print(f"[watch] {coin} — funding/premium monitor, {args.interval}s ticks")
    print("[watch] exit signals: funding < 20% of entry avg, or |premium| < 0.3%")
    while True:
        try:
            mids = info.all_mids()
            mark = float(mids.get(coin, 0) or 0)
            meta_ctx = info.meta_and_asset_ctxs()
            fund = None
            for name, c in zip(meta_ctx[0]["universe"], meta_ctx[1]):
                if name["name"] == coin:
                    fund = float(c.get("funding") or 0)
            spot = spot_mark(info, coin)
            prem = (mark / spot - 1) if (mark and spot) else None
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            line = (f"[{ts}] mark={mark:.8g} "
                    f"funding={fund*100:.4f}%/hr" if fund is not None
                    else f"[{ts}] mark={mark:.8g} funding=n/a")
            if prem is not None:
                line += f"  premium={prem*100:+.2f}%"
                if abs(prem) < 0.003:
                    line += "  <== PREMIUM EXIT SIGNAL"
            if fund is not None and fund < 0.0001:
                line += "  <== FUNDING EXIT SIGNAL (collapsed)"
            print(line, flush=True)
        except Exception as e:
            print(f"[watch] error: {e}", flush=True)
        time.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser(description="Hyperliquid trade execution")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("secret-setup")
    sub.add_parser("address")
    sub.add_parser("balance")
    op = sub.add_parser("order")
    op.add_argument("--coin", required=True)
    op.add_argument("--side", required=True, choices=["short", "long"])
    op.add_argument("--notional", type=float, required=True)
    op.add_argument("--price", type=float, default=None)
    op.add_argument("--stop", type=float, default=None,
                    help="hard stop price (reduce-only trigger SL, placed with entry)")
    op.add_argument("--execute", action="store_true")
    cp = sub.add_parser("close")
    cp.add_argument("--coin", required=True)
    cp.add_argument("--price", type=float, default=None)
    cp.add_argument("--execute", action="store_true")
    wp = sub.add_parser("watch")
    wp.add_argument("--coin", required=True)
    wp.add_argument("--interval", type=int, default=60)
    wd = sub.add_parser("withdraw")
    wd.add_argument("--amount", type=float, required=True,
                    help="USDC amount (perps account) to withdraw")
    wd.add_argument("--to", default=None,
                    help="destination address (default: our own wallet)")
    wd.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    cmds = {
        "secret-setup": cmd_secret_setup,
        "address": cmd_address,
        "balance": cmd_balance,
        "order": cmd_order,
        "close": cmd_close,
        "watch": cmd_watch,
        "withdraw": cmd_withdraw,
    }
    return cmds[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
