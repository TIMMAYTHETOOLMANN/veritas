#!/usr/bin/env python3
"""One-off: check hot wallet ETH balance on Arbitrum, write to _wallet_check.txt."""
import json
import os
import traceback
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_wallet_check.txt")
HOT_WALLET = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
RPC = "https://arb1.arbitrum.io/rpc"

lines = [f"wallet: {HOT_WALLET}", f"cwd: {os.getcwd()}"]
try:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getBalance",
        "params": [HOT_WALLET, "latest"],
    }).encode()
    req = urllib.request.Request(RPC, data=payload,
                                 headers={"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=30).read())
    wei = int(out["result"], 16)
    eth = wei / 1e18
    lines += [
        f"balance_wei: {out['result']}",
        f"balance_eth: {eth:.6f}",
        f"gas_sufficient (>= 0.01 ETH): {eth >= 0.01}",
    ]
except Exception:
    lines += ["ERROR:", traceback.format_exc()]

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")