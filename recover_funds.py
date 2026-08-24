#!/usr/bin/env python3
"""
recover_funds.py — recover the USDC stuck in Hyperliquid's INTERNAL ledger
under the hot-wallet address (0x1a0d...) and land it as real USDC on Arbitrum.

CONTEXT: the earlier "withdrawals" used usdSend — Hyperliquid's INTERNAL
account-to-account transfer. That moved money into an internal ledger entry
Hyperliquid created for the hot wallet's address. It never touched the chain,
which is why the wallet app shows nothing. The only way out is a withdraw3
("withdraw from bridge") action SIGNED BY THE HOT WALLET'S OWN KEY.

SAFETY MODEL:
  - Key is read ONLY from .hot_secret in this folder. Never from chat/argv.
  - The script VERIFIES the key derives 0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f
    and REFUSES to run on any mismatch.
  - Destination is hardcoded to that same address — funds can only go to the
    wallet the key itself controls.
  - --dry-run verifies the key and prints the plan without touching anything.

Usage:
  python recover_funds.py --dry-run     # verify key + show plan, no action
  python recover_funds.py               # execute withdrawal + confirm on-chain
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOT_WALLET = "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"
SECRET_FILE = os.path.join(HERE, ".hot_secret")

ARBITRUM_FLEET = ["https://arb1.arbitrum.io/rpc",
                  "https://arbitrum.drpc.org",
                  "https://arbitrum.publicnode.com"]
USDC_ARBITRUM = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDC_E_ARBITRUM = "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"
UA = {"Content-Type": "application/json",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def load_hot_account():
    if not os.path.isfile(SECRET_FILE):
        print("[abort] no key at %s" % SECRET_FILE)
        print("        create it per the instructions given in chat (key never touches chat)")
        sys.exit(1)
    key = None
    with open(SECRET_FILE) as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                key = s
                break
    if not key:
        print("[abort] key file is empty")
        sys.exit(1)
    from eth_account import Account
    acct = Account.from_key(key)
    if acct.address.lower() != HOT_WALLET.lower():
        print("[abort] KEY MISMATCH: derived %s, expected %s" % (acct.address, HOT_WALLET))
        sys.exit(2)
    print("[ok] key verified — controls %s" % acct.address)
    return acct


def onchain_usdc(addr):
    import urllib.request

    def rpc(method, params):
        last = None
        for url in ARBITRUM_FLEET:
            try:
                req = urllib.request.Request(url, data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method,
                     "params": params}).encode(), headers=UA)
                with urllib.request.urlopen(req, timeout=20) as r:
                    out = json.loads(r.read())
                if "result" in out:
                    return out["result"]
                last = out.get("error")
            except Exception as e:
                last = e
        raise RuntimeError("RPCs failed: %s" % last)

    def pad(a):
        return a[2:].lower().rjust(64, "0")

    def bal(token):
        raw = rpc("eth_call", [{"to": token, "data": "0x70a08231" + pad(addr)}, "latest"])
        return int(raw, 16) / 1e6

    return bal(USDC_ARBITRUM), bal(USDC_E_ARBITRUM)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    acct = load_hot_account()

    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    info = Info(skip_ws=True)
    st = info.user_state(acct.address)
    wd = float(st.get("withdrawable") or 0)
    print("internal Hyperliquid balance at %s : $%.4f (withdrawable)" % (acct.address, wd))

    n_usdc, e_usdc = onchain_usdc(acct.address)
    print("on-chain right now : USDC %.6f | USDC.e %.6f" % (n_usdc, e_usdc))

    amount = float("%.2f" % min(wd, 18.76))
    if amount <= 0:
        print("nothing to withdraw — balance already on-chain or empty")
        return 0
    print("PLAN: withdraw3 $%.2f -> %s (lands as USDC on Arbitrum)" % (amount, acct.address))
    if args.dry_run:
        print("dry run — stopping here, nothing submitted")
        return 0

    ex = Exchange(acct)
    r = ex.withdraw_from_bridge(amount, acct.address)
    print("withdraw3 response:", json.dumps(r, default=str))
    if not (isinstance(r, dict) and r.get("status") == "ok"):
        print("[fail] withdrawal rejected — see raw response above "
              "(minimum-amount or access error)")
        return 1

    print("polling on-chain for arrival (up to 10 min)...")
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(15)
        try:
            n_usdc, e_usdc = onchain_usdc(acct.address)
        except Exception as e:
            print("  poll error: %s" % e)
            continue
        print("  [%3ds] USDC %.6f | USDC.e %.6f" % (time.time() - t0, n_usdc, e_usdc))
        if n_usdc > 0 or e_usdc > 0:
            print("[DONE] FUNDS ARE ON-CHAIN — your wallet app will now see them.")
            print("       Arbiscan: https://arbiscan.io/address/%s" % acct.address)
            print("       You can safely delete .hot_secret now — its job is done.")
            return 0
    print("[pending] not landed within 10 min — Hyperliquid's bridge batches "
          "withdrawals; re-run this script later to re-check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())