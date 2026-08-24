# hl_deposit.py — deposit the wallet's USDC on Arbitrum into Hyperliquid perps
# via the official Deposit Bridge: approve(bridge, amount) + transfer(bridge).
# Verified bridge: 0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7
# ("Hyperliquid: Deposit Bridge 2" on Arbiscan).
# DRY-RUN default; --execute broadcasts.
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (core/)
from core.rpc import RPC
from core.selectors import kec256

RPC_URL = "https://arbitrum-one-rpc.publicnode.com"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
BRIDGE = "0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7"
CHAIN_ID = 42161
MIN_DEPOSIT = 5.0  # Hyperliquid minimum


def pad(a):
    return a[2:].lower().rjust(64, "0")


def u256(v):
    return f"{int(v):064x}"


def load_acct():
    from eth_account import Account
    # script lives in hyperliquid/; secret is at repo root (parent dir)
    here = os.path.dirname(os.path.abspath(__file__))
    # .hot_secret = the carry-engine trading account (holds the on-chain USDC)
    for cand in (os.path.join(here, "..", ".hot_secret"),
                 os.path.join(here, "..", ".hl_secret"),
                 os.path.join(here, ".hl_secret")):
        cand = os.path.normpath(cand)
        if os.path.isfile(cand):
            with open(cand) as f:
                return Account.from_key(f.read().strip())
    raise SystemExit("ERROR: .hl_secret not found (repo root or hyperliquid/)")


def send_and_wait(rpc, acct, tx, label):
    signed = acct.sign_transaction(tx)
    # eth_account 0.11.x exposes .rawTransaction; newer versions .raw_transaction
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    raw = raw.hex()
    if raw.startswith("0x"):
        raw = raw[2:]
    txh = rpc.call("eth_sendRawTransaction", ["0x" + raw])
    print(f"  {label}: {txh}")
    for _ in range(90):
        time.sleep(2)
        try:
            rec = rpc.call("eth_getTransactionReceipt", [txh])
        except Exception:
            rec = None
        if rec is not None:
            ok = rec.get("status") == "0x1"
            print(f"  {label}: status {'SUCCESS' if ok else 'REVERTED'} "
                  f"(gas {int(rec.get('gasUsed','0x0'),16):,})")
            return ok, txh
    print(f"  {label}: receipt timeout")
    return False, txh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--amount", type=float, default=None,
                    help="USDC to deposit (default: all minus nothing)")
    args = ap.parse_args()

    rpc = RPC(RPC_URL, timeout=30, retries=5)
    acct = load_acct()
    addr = acct.address

    usdc_bal = int(rpc.eth_call(USDC, "0x70a08231" + pad(addr)), 16)
    eth_bal = int(rpc.call("eth_getBalance", [addr, "latest"]), 16)
    print(f"wallet      : {addr}")
    print(f"USDC        : {usdc_bal/1e6:.4f}")
    print(f"ETH (gas)   : {eth_bal/1e18:.6f}")

    amount = args.amount or usdc_bal / 1e6
    if amount < MIN_DEPOSIT:
        print(f"amount {amount} below Hyperliquid minimum {MIN_DEPOSIT} USDC")
        return 1
    amt_raw = int(amount * 1e6)
    if amt_raw > usdc_bal:
        print("amount exceeds balance")
        return 1

    # current allowance
    sel_allow = "0x" + kec256(b"allowance(address,address)").hex()[:8]
    cur = int(rpc.eth_call(
        USDC, sel_allow + pad(addr) + pad(BRIDGE)), 16)
    print(f"allowance   : {cur/1e6:.2f} USDC")

    gas_price = int(rpc.call("eth_gasPrice", []), 16)
    nonce = int(rpc.call("eth_getTransactionCount", [addr, "latest"]), 16)

    print(f"\n[{'EXECUTE' if args.execute else 'DRY-RUN'}] deposit "
          f"{amount:.4f} USDC to Hyperliquid perps via bridge")

    if not args.execute:
        print("  plan: 1) approve bridge for exact amount  2) transfer to bridge")
        print("  dry run only — re-run with --execute")
        return 0

    # 1) approve exact amount
    if cur < amt_raw:
        sel_approve = "0x" + kec256(b"approve(address,uint256)").hex()[:8]
        tx = {
            "type": 2, "chainId": CHAIN_ID, "nonce": nonce,
            "to": USDC, "value": 0,
            "data": sel_approve + pad(BRIDGE) + u256(amt_raw),
            "maxFeePerGas": max(gas_price * 3, 100_000_000),
            "maxPriorityFeePerGas": max(gas_price // 2, 10_000_000),
            "gas": 80_000,
        }
        ok, _ = send_and_wait(rpc, acct, tx, "approve")
        if not ok:
            return 1
        nonce += 1
    else:
        print("  allowance sufficient — skipping approve")

    # 2) transfer USDC to the bridge
    sel_transfer = "0x" + kec256(b"transfer(address,uint256)").hex()[:8]
    tx = {
        "type": 2, "chainId": CHAIN_ID, "nonce": nonce,
        "to": USDC, "value": 0,
        "data": sel_transfer + pad(BRIDGE) + u256(amt_raw),
        "maxFeePerGas": max(gas_price * 3, 100_000_000),
        "maxPriorityFeePerGas": max(gas_price // 2, 10_000_000),
        "gas": 80_000,
    }
    ok, _ = send_and_wait(rpc, acct, tx, "transfer-to-bridge")
    if not ok:
        return 1

    print("\ndeposit sent — Hyperliquid credits perps after Arbitrum "
          "finality (~1 min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
