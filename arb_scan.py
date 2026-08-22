# arb_scan.py — T0 edge-discovery layer: live cross-DEX dislocation scanner
# Built directly on VERITAS core/rpc.py (fleet-hardened, Cloudflare-UA cured).
# READ-ONLY: eth_call only. Never signs, never sends a transaction. $0 cost.
#
# Purpose: measure, per block, whether a flash-loan arb between two
# constant-product pools would clear more than gas. This is the instrument
# that decides whether a bundle is worth submitting at all.
#
# Usage: python arb_scan.py [--chain base] [--rpc URL]
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint  # VERITAS asset reuse: hardened RPC client

# ---- verified token addresses (Base, chain 8453) -----------------------
WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
CBBTC = "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"

TOKENS = {
    WETH: {"sym": "WETH", "decimals": 18},
    USDC: {"sym": "USDC", "decimals": 6},
    CBBTC: {"sym": "cbBTC", "decimals": 8},
}

# ---- verified factory addresses (Base) ---------------------------------
# Uniswap V2 factory: developers.uniswap.org deployment docs
UNIV2_FACTORY = "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6"
# Aerodrome (Solidly-fork) factory: aerodrome.finance / docs.base.org
AERO_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"

# ---- function selectors -------------------------------------------------
SEL = {
    "getPair":  "e6a43905",  # getPair(address,address) -> address   (UniV2)
    "getPool":  "1698ee82",  # getPool(address,address,bool) -> addr (Solidly)
    "reserves": "0902f1ac",  # getReserves() -> (uint112,uint112,uint32)
    "token0":   "0dfe1681",
    "token1":   "d21220a7",
}

FEE_NUM = 997  # 0.3% pools: keep 997/1000 of input


def pad_addr(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def pad_uint(v: int) -> str:
    return f"{v:064x}"


def parse_addr(result: str):
    if not result or result == "0x":
        return None
    a = result[2:]
    if len(a) < 40 or set(a[-40:]) == {"0"}:
        return None
    return "0x" + a[-40:]


def parse_reserves(result: str):
    """getReserves() -> (reserve0, reserve1) as ints."""
    if not result or result == "0x" or len(result) < 2 + 64:
        return None, None
    h = result[2:]
    r0 = int(h[0:64], 16)
    r1 = int(h[64:128], 16)
    return r0, r1


def q(amount: int, decimals: int) -> float:
    return amount / (10 ** decimals)


# ---- pool discovery ------------------------------------------------------

def univ2_pair(rpc: RPC, token_a: str, token_b: str):
    data = "0x" + SEL["getPair"] + pad_addr(token_a) + pad_addr(token_b)
    return parse_addr(rpc.eth_call(UNIV2_FACTORY, data))


def aero_pool(rpc: RPC, token_a: str, token_b: str, stable: bool):
    data = ("0x" + SEL["getPool"] + pad_addr(token_a) + pad_addr(token_b)
            + pad_uint(1 if stable else 0))
    return parse_addr(rpc.eth_call(AERO_FACTORY, data))


def load_pool(rpc: RPC, name: str, address: str):
    """Fetch token ordering + reserves for a constant-product pool."""
    if address is None:
        return None
    t0 = parse_addr(rpc.eth_call(address, "0x" + SEL["token0"]))
    t1 = parse_addr(rpc.eth_call(address, "0x" + SEL["token1"]))
    r0, r1 = parse_reserves(rpc.eth_call(address, "0x" + SEL["reserves"]))
    if t0 is None or r0 is None:
        return None
    return {
        "name": name, "address": address,
        "token0": t0, "token1": t1, "r0": r0, "r1": r1,
    }


def pool_side(pool, base, quote):
    """Return (base_reserve_raw, quote_reserve_raw) for base->quote pricing."""
    if pool["token0"] == base and pool["token1"] == quote:
        return pool["r0"], pool["r1"]
    if pool["token0"] == quote and pool["token1"] == base:
        return pool["r1"], pool["r0"]
    return None, None


def price_of(pool, base, quote):
    """Effective mid price: quote per 1.0 base, human units."""
    br, qr = pool_side(pool, base, quote)
    if br is None or br == 0:
        return None
    db = TOKENS[base]["decimals"]
    dq = TOKENS[quote]["decimals"]
    return q(qr, dq) / q(br, db)


# ---- arbitrage math (constant-product pairs only, same fee model) -------

def cp_out(reserve_in: float, reserve_out: float, amount_in: float,
           fee_num: int = FEE_NUM) -> float:
    """Constant-product swap out amount (human-unit floats, fee included)."""
    if amount_in <= 0:
        return 0.0
    ain = amount_in * fee_num / 1000.0
    return reserve_out * ain / (reserve_in + ain)


def best_two_pool_arb(pool_a, pool_b, base, quote, ref_price):
    """Numeric scan: buy base->quote on A, sell quote->base on B (and reverse).
    Returns best (direction, size_base, gross_profit_usd)."""
    best = None
    for (buy, sell) in ((pool_a, pool_b), (pool_b, pool_a)):
        # buy pool: base -> quote ; sell pool: quote -> base
        b_in_r, b_out_r = pool_side(buy, base, quote)
        s_in_r, s_out_r = pool_side(sell, quote, base)  # quote->base sides
        if not b_in_r or not s_out_r:
            continue
        db = TOKENS[base]["decimals"]
        dq = TOKENS[quote]["decimals"]
        # human-unit reserves
        bin_h, bout_h = q(b_in_r, db), q(b_out_r, dq)
        sin_h, sout_h = q(s_in_r, dq), q(s_out_r, db)
        if bin_h <= 0 or sin_h <= 0:
            continue
        # scan trade sizes (log-spaced)
        import math
        hi = min(bin_h, sout_h) * 0.30
        for i in range(120):
            size = hi * (i / 119.0) if i else hi * 1e-4
            if size <= 0:
                continue
            got_quote = cp_out(bin_h, bout_h, size)
            got_base = cp_out(sin_h, sout_h, got_quote)
            profit = got_base - size
            if profit > 0:
                usd = profit * ref_price
                if best is None or usd > best[2]:
                    best = (f"{buy['name']} -> {sell['name']}", size, usd)
    return best


# ---- main ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="VERITAS arb edge-discovery scan (read-only)")
    ap.add_argument("--rpc", default="https://base-rpc.publicnode.com")
    ap.add_argument("--eth-usd", type=float, default=None,
                    help="ETH price for USD conversion (default: derived from pools)")
    ap.add_argument("--gas-units", type=int, default=350_000,
                    help="gas for a flash-loan arb executor tx")
    args = ap.parse_args()

    rpc = RPC(args.rpc, timeout=30, retries=3)
    chain_id = uint(rpc.call("eth_chainId", []))
    head = uint(rpc.call("eth_blockNumber", []))
    gas_price_wei = uint(rpc.call("eth_gasPrice", [])) or 0
    print(f"[scan] chain={chain_id} head={head} gas={gas_price_wei/1e9:.4f} gwei")

    pairs = [(WETH, USDC), (WETH, CBBTC)]

    pools = []
    for base, quote in pairs:
        tag = f"{TOKENS[base]['sym']}/{TOKENS[quote]['sym']}"
        p = univ2_pair(rpc, base, quote)
        if p:
            pools.append(load_pool(rpc, f"UniV2 {tag}", p))
        for stable in (False, True):
            p = aero_pool(rpc, base, quote, stable)
            kind = "sAMM" if stable else "vAMM"
            if p:
                pools.append(load_pool(rpc, f"Aero {kind} {tag}", p))

    pools = [p for p in pools if p]
    print(f"[scan] {len(pools)} live pools discovered:\n")

    # ---- price table ----
    print(f"{'pool':<28}{'base reserve':>16}{'quote reserve':>16}{'price':>14}")
    prices = {}
    for pool in pools:
        for base, quote in pairs:
            br, qr = pool_side(pool, base, quote)
            if br is None or br == 0:
                continue
            pr = price_of(pool, base, quote)
            key = (base, quote)
            prices.setdefault(key, []).append((pool, pr))
            bs, qs = TOKENS[base]["sym"], TOKENS[quote]["sym"]
            print(f"{pool['name']:<28}{q(br, TOKENS[base]['decimals']):>16,.2f}"
                  f"{q(qr, TOKENS[quote]['decimals']):>16,.2f}{pr:>14,.2f}")

    # ---- dislocation + arb scan ----
    print("\n[scan] dislocations & optimal arb (0.3% fee both legs, numeric opt):")
    eth_usd = args.eth_usd
    for (base, quote), entries in prices.items():
        bs, qs = TOKENS[base]["sym"], TOKENS[quote]["sym"]
        # reference price: median across venues (rough consensus)
        vals = sorted(pr for _, pr in entries if pr)
        if len(vals) < 2:
            continue
        ref = vals[len(vals) // 2]
        if bs == "WETH" and eth_usd is None:
            eth_usd = ref if qs == "USDC" else None
        # per-pair arb only among constant-product pools (UniV2 + Aero vAMM)
        cp_pools = [p for p, _ in entries if "UniV2" in p["name"] or "vAMM" in p["name"]]
        for i in range(len(cp_pools)):
            for j in range(len(cp_pools)):
                if i >= j:
                    continue
                a, b = cp_pools[i], cp_pools[j]
                disl_bps = abs(price_of(a, base, quote) / price_of(b, base, quote) - 1) * 1e4
                best = best_two_pool_arb(a, b, base, quote, ref)
                line = f"  {bs}/{qs}  {a['name']} vs {b['name']}: {disl_bps:7.1f} bps"
                if best:
                    d, size, usd = best
                    line += f"  | arb: {size:,.4f} {bs} via {d} -> ${usd:,.2f}"
                else:
                    line += "  | arb: none (fees eat it)"
                print(line)

    # ---- gas-adjusted floor ----
    if eth_usd:
        gas_eth = gas_price_wei * args.gas_units / 1e18
        gas_usd = gas_eth * eth_usd
        print(f"\n[scan] executor tx gas floor: {args.gas_units:,} gas @ "
              f"{gas_price_wei/1e9:.4f} gwei = {gas_eth:.8f} ETH ≈ ${gas_usd:.4f}")
        print("[scan] rule: submit a bundle only when scanned profit > gas floor + loan fee.")
    else:
        print("\n[scan] (no WETH/USDC pool found; gas floor not computed)")


if __name__ == "__main__":
    main()
