#!/usr/bin/env python3
"""verify_arb_venues.py — on-chain verification of every address the new
Arbitrum flashloan-arb engine will touch. READ-ONLY eth_call/eth_getCode.

Confirms: factory code presence, pair/pool existence for target pairs,
Aave V3 reserves (WETH, USDC, USDC.e), and pool reserves for the scanner.
"""
import json
import sys
import urllib.request

UA = {"Content-Type": "application/json",
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]

WETH   = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC   = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDCE  = "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"
UNIV2  = "0xF8a17ADDD7d53328b05ddAfBF45fD77bb07Af46B"   # Uniswap V2 factory
SUSHI  = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"   # SushiSwap V2 factory
AAVE   = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"   # Aave V3 Pool

SEL = {
    "getPair": "e6a43905",
    "getReservesList": "d1946dbc",
    "reserves": "0902f1ac",
    "token0": "0dfe1681",
}


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def rpc(idx, method, params):
    return post(RPCS[idx % len(RPCS)], {"jsonrpc": "2.0", "id": 1,
                                        "method": method, "params": params})


def call(to, data, i=0):
    r = rpc(i, "eth_call", [{"to": to, "data": data}, "latest"])
    return r.get("result")


def pad(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def addr_of(res):
    if not res or len(res) < 66:
        return None
    tail = res[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail


def main():
    i = 0
    print("== factory / pool code presence ==")
    for name, addr in [("univ2-factory", UNIV2), ("sushi-factory", SUSHI),
                       ("aave-v3-pool", AAVE)]:
        c = rpc(i, "eth_getCode", [addr, "latest"]); i += 1
        print(f"  {name:16s} {len(c.get('result', '0x')) // 2 - 1:>6} bytes of code")

    print("== getPair for target pairs ==")
    pairs = {}
    for fname, factory in [("UniV2", UNIV2), ("Sushi", SUSHI)]:
        for qname, quote in [("USDC", USDC), ("USDC.e", USDCE)]:
            r = call(factory, "0x" + SEL["getPair"] + pad(WETH) + pad(quote), i); i += 1
            pair = addr_of(r)
            key = f"WETH/{qname}"
            if pair:
                pairs[(fname, key)] = pair
            print(f"  {fname} {key:12s} -> {pair}")

    print("== Aave V3 reserves list ==")
    r = call(AAVE, "0x" + SEL["getReservesList"], i); i += 1
    if r and len(r) > 2:
        h = r[2:]
        n = len(h) // 64
        addrs = {("0x" + h[j * 64 + 24:(j + 1) * 64]).lower() for j in range(n)}
        print(f"  reserves count: {n}")
        for t, label in [(WETH, "WETH"), (USDC, "USDC"), (USDCE, "USDC.e")]:
            print(f"  {label:6s} in Aave: {t.lower() in addrs}")
    else:
        print("  getReservesList FAILED:", r)

    print("== pool reserves (for scanner sanity) ==")
    for (fname, key), pair in pairs.items():
        t0 = addr_of(call(pair, "0x" + SEL["token0"], i)); i += 1
        res = call(pair, "0x" + SEL["reserves"], i); i += 1
        if res and len(res) >= 130:
            h = res[2:]
            r0 = int(h[0:64], 16)
            r1 = int(h[64:128], 16)
            d0 = 18 if t0 and t0.lower() == WETH.lower() else 6
            d1 = 6 if d0 == 18 else 18
            weth_res = r0 / 10 ** d0 if d0 == 18 else r1 / 10 ** d1
            usd_res = r1 / 10 ** d1 if d0 == 18 else r0 / 10 ** d0
            print(f"  {fname} {key:12s} WETH={weth_res:,.2f}  USD-side={usd_res:,.2f}")
        else:
            print(f"  {fname} {key:12s} reserves FAILED")

    print("OK")


if __name__ == "__main__":
    sys.exit(main())
