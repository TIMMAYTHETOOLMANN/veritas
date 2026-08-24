# swap_eth_usdc.py — one-shot: swap the wallet's ETH to USDC on Arbitrum via
# Uniswap V3 SwapRouter02 (0.05% pool), then verify balances. Signs with the
# .hl_secret key. DRY-RUN by default; --execute signs and broadcasts.
#
# HARD-WON LESSONS BAKED IN:
#   - SwapRouter02 on Arbitrum is 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
#     (NOT 0x4752ba... — that's the Base router).
#   - exactInputSingle params struct has NO deadline field (7 fields).
#   - A static struct arg is DIRECT CONCATENATION — no 0x20 offset word.
#   - Get the real quote by binary-searching min_out via eth_call simulation
#     against the router ("Too little received" = encoding is correct).
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC
from core.selectors import kec256

RPC_URL = "https://arbitrum.publicnode.com"
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"   # SwapRouter02 (Arbitrum)
FEE = 500                                                # 0.05% tier
CHAIN_ID = 42161


def pad(a):
    return a[2:].lower().rjust(64, "0")


def u256(v):
    return f"{int(v):064x}"


def load_acct():
    from eth_account import Account
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".hl_secret")) as f:
        secret = f.read().strip()
    return Account.from_key(secret)


def build_calldata(to, amount_in, min_out):
    sel = "0x" + kec256(
        b"exactInputSingle((address,address,uint24,address,uint256,"
        b"uint256,uint160))").hex()[:8]
    # static struct: direct concatenation, NO offset word
    return (sel + pad(WETH) + pad(USDC) + u256(FEE) + pad(to)
            + u256(amount_in) + u256(min_out) + u256(0))


def router_quote(rpc, addr, amount_in):
    """Binary-search min_out against the router via eth_call.
    The largest accepted min_out IS the exact quote."""
    sel_ok = build_calldata(addr, amount_in, 0)
    # quick sanity: zero min must simulate OK
    try:
        rpc.call("eth_call", [{"from": addr, "to": ROUTER,
                               "value": hex(amount_in), "data": sel_ok},
                              "latest"])
    except Exception:
        return None
    lo, hi, best = 0, int(amount_in * 3500 * 1e6), 0  # upper bound ~3500/ETH
    while lo <= hi:
        mid = (lo + hi) // 2
        data = build_calldata(addr, amount_in, mid)
        try:
            rpc.call("eth_call", [{"from": addr, "to": ROUTER,
                                   "value": hex(amount_in), "data": data},
                                  "latest"])
            best = mid
            lo = mid + 1
        except Exception:
            hi = mid - 1
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--slippage-bps", type=int, default=100)  # 1%
    ap.add_argument("--keep-eth", type=float, default=0.0005,
                    help="ETH to keep for gas")
    args = ap.parse_args()

    rpc = RPC(RPC_URL, timeout=30, retries=3)
    acct = load_acct()
    addr = acct.address

    eth_bal = int(rpc.call("eth_getBalance", [addr, "latest"]), 16)
    gas_price = int(rpc.call("eth_gasPrice", []), 16)
    gas_cost = gas_price * 400_000
    print(f"wallet      : {addr}")
    print(f"ETH balance : {eth_bal/1e18:.6f} "
          f"(gas price {gas_price/1e9:.3f} gwei)")

    swap_amount = eth_bal - int(args.keep_eth * 1e18) - gas_cost
    if swap_amount <= 0:
        print("insufficient ETH after gas reserve")
        return 1

    quote = router_quote(rpc, addr, swap_amount)
    if not quote:
        print("router simulation failed at zero min_out — aborting")
        return 1
    min_out = int(quote * (1 - args.slippage_bps / 10_000))
    print(f"swap amount : {swap_amount/1e18:.6f} ETH")
    print(f"router quote: {quote/1e6:.4f} USDC "
          f"(effective px {(quote/1e6)/(swap_amount/1e18):,.1f})")
    print(f"min out     : {min_out/1e6:.4f} USDC "
          f"({args.slippage_bps/100:.1f}% slippage buffer)")

    calldata = build_calldata(addr, swap_amount, min_out)
    nonce = int(rpc.call("eth_getTransactionCount", [addr, "latest"]), 16)
    tx = {
        "type": 2, "chainId": CHAIN_ID, "nonce": nonce, "to": ROUTER,
        "value": swap_amount, "data": calldata,
        "maxFeePerGas": max(gas_price * 3, 100_000_000),       # 0.1 gwei floor
        "maxPriorityFeePerGas": max(gas_price // 2, 10_000_000),
        "gas": 400_000,
    }
    print(f"\n[{'EXECUTE' if args.execute else 'DRY-RUN'}] "
          f"swap ETH -> USDC via SwapRouter02")

    if not args.execute:
        print("  dry run only — re-run with --execute to broadcast")
        return 0

    signed = acct.sign_transaction(tx)
    raw = signed.raw_transaction.hex()
    if raw.startswith("0x"):
        raw = raw[2:]
    txh = rpc.call("eth_sendRawTransaction", ["0x" + raw])
    print(f"  broadcast: {txh}")
    for _ in range(60):
        time.sleep(2)
        rec = rpc.call("eth_getTransactionReceipt", [txh])
        if rec is not None:
            status = rec.get("status")
            print(f"  status: {status} "
                  f"({'SUCCESS' if status == '0x1' else 'REVERTED'})")
            if status == "0x1":
                ub = int(rpc.eth_call(USDC, "0x70a08231" + pad(addr)), 16)
                eb = int(rpc.call("eth_getBalance", [addr, "latest"]), 16)
                print(f"  USDC balance: {ub/1e6:.4f}")
                print(f"  ETH balance : {eb/1e18:.6f}")
            return 0 if status == "0x1" else 1
    print("  receipt timeout — check explorer")
    return 1


if __name__ == "__main__":
    sys.exit(main())
