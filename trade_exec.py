#!/usr/bin/env python3
"""trade_exec.py - Hyperliquid trade execution via official SDK."""
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent

def _load(path: str) -> str:
    p = ROOT / path
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""

API_SECRET = _load(".hl_api_secret")
API_WALLET = _load(".hl_api_key")
MASTER_ADDRESS = _load(".hl_master_address") or "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"

def _exchange():
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    if not API_SECRET:
        raise RuntimeError("Missing .hl_api_secret")
    wallet = Account.from_key(API_SECRET)
    return Exchange(wallet, constants.MAINNET_API_URL, account_address=MASTER_ADDRESS)

def _info():
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    return Info(constants.MAINNET_API_URL, skip_ws=True)

def get_balances(user: Optional[str] = None) -> Dict[str, Any]:
    user = user or MASTER_ADDRESS
    info = _info()
    perp = info.user_state(user)
    spot = info.spot_user_state(user)
    account_value = float(perp.get("marginSummary", {}).get("accountValue", 0) or 0)
    withdrawable = float(perp.get("withdrawable", 0) or 0)
    spot_usdc = 0.0
    for b in spot.get("balances", []) or []:
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", 0) or 0)
            break
    return {
        "user": user,
        "account_value": account_value,
        "withdrawable": withdrawable,
        "spot_usdc": spot_usdc,
        "total_usdc": account_value + spot_usdc,
        "perp": perp,
        "spot": spot,
    }

def usd_class_transfer(amount: float, to_perp: bool = True):
    return _exchange().usd_class_transfer(amount, to_perp)

def market_order(coin: str, is_buy: bool, size: float, slippage: float = 0.01):
    try:
        ex = _exchange()
        print(f"[trade_exec] MARKET {'BUY' if is_buy else 'SELL'} {size} {coin}")
        result = ex.market_open(coin, is_buy, float(size), None, slippage)
        print(f"[trade_exec] Result: {result}")
        return result
    except Exception as e:
        print(f"[trade_exec] Market order failed: {e}")
        return None

def get_mark_price(coin: str):
    info = _info()
    meta, ctxs = info.meta_and_asset_ctxs()
    for i, m in enumerate(meta.get("universe", [])):
        if m.get("name") == coin and i < len(ctxs):
            return float(ctxs[i].get("markPx") or ctxs[i].get("midPx") or 0)
    return None

def get_sz_decimals(coin: str) -> int:
    meta = _info().meta()
    for m in meta.get("universe", []):
        if m.get("name") == coin:
            return int(m.get("szDecimals", 2))
    return 2

def open_funding_position(coin: str, direction: str, notional_usd: float, leverage: int = 3):
    px = get_mark_price(coin)
    if not px or px <= 0:
        print(f"[trade_exec] No price for {coin}")
        return None
    bal = get_balances()
    print(f"[trade_exec] Balances: perp=${bal['account_value']:.4f} spot=${bal['spot_usdc']:.4f}")
    if bal["account_value"] < 1.0 and bal["spot_usdc"] >= 1.0:
        try:
            amt = round(bal["spot_usdc"] - 0.01, 4)
            if amt > 0:
                print(f"[trade_exec] Transferring ${amt} spot -> perp...")
                print(usd_class_transfer(amt, True))
                time.sleep(1)
                bal = get_balances()
        except Exception as e:
            print(f"[trade_exec] usd_class_transfer failed: {e}")
    if bal["account_value"] < 1.0 and bal["total_usdc"] < 1.0:
        print("[trade_exec] INSUFFICIENT BALANCE on HyperCore.")
        print("[trade_exec] Move USDC: HyperEVM -> Spot, then Spot -> Perps if needed.")
        return None
    max_notional = max(bal["account_value"], bal["total_usdc"]) * leverage * 0.9
    notional = min(notional_usd, max_notional)
    if notional < 1.0:
        print(f"[trade_exec] Notional too small: ${notional:.4f}")
        return None
    size = round(notional / px, get_sz_decimals(coin))
    if size <= 0:
        print("[trade_exec] Size rounds to 0")
        return None
    is_buy = direction.upper() == "LONG"
    print(f"[trade_exec] Opening {direction} {coin}: size={size} (~${size*px:.2f})")
    try:
        print(_exchange().update_leverage(leverage, coin, is_cross=True))
    except Exception as e:
        print(f"[trade_exec] leverage: {e}")
    return market_order(coin, is_buy, size)

def execute_best_opportunity(notional_usd: float = 15.0, leverage: int = 3):
    from funding_scout import scan_opportunities
    opps = scan_opportunities(min_rate=0.00001, min_oi=500_000, top_n=5)
    if not opps:
        print("[trade_exec] No opportunities")
        return None
    best = opps[0]
    print(f"[trade_exec] Best: {best['coin']} {best['direction']} {best['funding_rate']*100:.4f}%/hr")
    return open_funding_position(best["coin"], best["direction"], notional_usd, leverage)

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--status", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--coin", type=str, default="")
    p.add_argument("--direction", type=str, default="LONG")
    p.add_argument("--notional", type=float, default=15.0)
    p.add_argument("--leverage", type=int, default=3)
    args = p.parse_args()
    print(f"API wallet: {API_WALLET}")
    print(f"Master:     {MASTER_ADDRESS}")
    if API_SECRET:
        from eth_account import Account
        print(f"Key addr:   {Account.from_key(API_SECRET).address}")
    if args.status or not (args.execute or args.coin):
        b = get_balances()
        print(json.dumps({"account_value": b["account_value"], "spot_usdc": b["spot_usdc"], "total_usdc": b["total_usdc"]}, indent=2))
    if args.coin:
        open_funding_position(args.coin, args.direction, args.notional, args.leverage)
    elif args.execute:
        execute_best_opportunity(args.notional, args.leverage)
