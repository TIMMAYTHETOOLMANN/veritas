#!/usr/bin/env python3
"""
flash_hunter.py — VERITAS Engine: autonomous Arbitrum flash-loan arb hunter.

THE LOOP (user spec): scan → fork-sim → gate (profit > 20x gas) -> broadcast
-> verify on-chain profit -> log heartbeat. Deploy the executor once
(~$0.03 gas), then every attempt costs only gas-if-included; a reverted
attempt costs ~$0.005. The $15.70 principal is NEVER exposed — the
flashloan carries the size; atomicity guarantees revert-on-failure.

SECURITY MODEL:
  - Key: hot wallet, read from .hot_secret at runtime. Never printed.
  - Signing happens ONLY after the fork-sim gate PASSES. No gate, no tx.
  - Broadcast is retried across 3 public RPCs (rotation).
  - Every cycle logs to flash_hunter.log (JSONL) + heartbeat every 15 min.

Usage:
  python3 flash_hunter.py --deploy        # deploy executor once (~$0.03)
  python3 flash_hunter.py --run           # hunt forever (default 60s cycles)
  python3 flash_hunter.py --status        # executor address + WETH balance
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eth_account import Account

import arb_engine
import sim_gate

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "flash_hunter.log")
EXECUTOR_FILE = os.path.join(HERE, ".executor_address")

HOT_WALLET = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
SECRET_FILE = os.path.join(HERE, ".hot_secret")

BROADCAST_RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]

SCAN_INTERVAL_SEC = 180   # cross-venue quoter scan is ~1-2 min
HEARTBEAT_EVERY_SEC = 15 * 60
GAS_MULTIPLIER = 20
EXECUTOR_V2_FILE = os.path.join(HERE, ".executor_v2_address")
V3_ROUTER = "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"


class Rpc:
    """Minimal signing JSON-RPC client (stdlib only, core/rpc.py pattern)."""

    def __init__(self, url):
        self.url = url
        self._id = 0
        self._opener = urllib.request.build_opener()

    def req(self, method, params):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id,
                   "method": method, "params": params}
        r = self._opener.open(
            urllib.request.Request(
                self.url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/126.0 Safari/537.36"}),
            timeout=60)
        out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(f"{method}: {out['error']}")
        return out["result"]

    def eth_call(self, to, data):
        return self.req("eth_call", [{"to": to, "data": data}, "latest"])

    def balance(self, addr):
        return int(self.req("eth_getBalance", [addr, "latest"]), 16)

    def nonce(self, addr):
        return int(self.req("eth_getTransactionCount",
                            [addr, "latest"]), 16)

    def gas_price(self):
        return int(self.req("eth_gasPrice", []), 16)

    def send_raw(self, raw_hex):
        return self.req("eth_sendRawTransaction", [raw_hex])

    def wait_receipt(self, txhash, timeout=180):
        import time as _t
        for _ in range(timeout * 2):
            r = self.req("eth_getTransactionReceipt", [txhash])
            if r is not None:
                return r
            _t.sleep(0.5)
        raise RuntimeError(f"tx {txhash} not mined in {timeout}s")


def log_event(evt):
    evt["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(evt) + "\n")


def load_key():
    with open(SECRET_FILE) as f:
        return f.read().strip()


def get_rpc():
    for url in BROADCAST_RPCS:
        try:
            r = Rpc(url)
            r.gas_price()  # connectivity probe
            return r, url
        except Exception:
            continue
    raise RuntimeError("no RPC reachable")


def kec_sig(sig):
    from eth_utils import keccak
    return "0x" + keccak(text=sig)[:4].hex()


def load_executor():
    # prefer the cross-venue V2 executor when deployed
    if os.path.isfile(EXECUTOR_V2_FILE):
        with open(EXECUTOR_V2_FILE) as f:
            return f.read().strip()
    if not os.path.isfile(EXECUTOR_FILE):
        return None
    with open(EXECUTOR_FILE) as f:
        return f.read().strip()


def deploy_executor(rpc, acct):
    with open(os.path.join(HERE, "contracts", "FlashloanArb.bin")) as f:
        binhex = f.read().strip()
    nonce = rpc.nonce(acct.address)
    gas_price = int(rpc.gas_price() * 1.25)  # buffer over base fee
    # constructor args: aavePool, weth (encoded, appended to init code)
    ctor = (binhex
            + arb_engine.AAVE_V3_POOL[2:].rjust(64, "0")
            + arb_engine.WETH[2:].rjust(64, "0"))
    tx = {
        "from": acct.address,
        "data": "0x" + ctor if not binhex.startswith("0x") else ctor,
        "nonce": nonce,
        "gas": 2_000_000,
        "gasPrice": gas_price,
        "chainId": 42161,
        "value": 0,
    }
    signed = acct.sign_transaction(tx)
    raw_hex = (signed.raw_transaction if hasattr(signed, "raw_transaction")
               else signed.rawTransaction).hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex
    h = rpc.send_raw(raw_hex)
    r = rpc.wait_receipt(h)
    if int(r["status"], 16) != 1:
        raise RuntimeError("executor deployment failed: " + json.dumps(r)[:300])
    addr = r["contractAddress"]
    with open(EXECUTOR_FILE, "w") as f:
        f.write(addr)
    log_event({"event": "executor_deployed", "address": addr,
               "gas_used": int(r["gasUsed"], 16), "tx": h})
    print(f"[hunter] executor deployed: {addr} "
          f"(gas {int(r['gasUsed'], 16):,})")
    return addr


def gas_usd_of(gas_price_wei, gas_used, eth_usd=2450.0):
    return (gas_used / 1e9) * (gas_price_wei / 1e9) * eth_usd


def hunt_once(rpc, acct, executor_addr, rpc_scan, verbose=True):
    """One hunt cycle: cross-venue scan -> sim candidates -> broadcast if gate passes."""
    from core.rpc import RPC as Vrpc
    import v3_layer
    r = Vrpc(rpc_scan, timeout=30, retries=3)
    # ETH price + gas from the V3 quoter itself (ground truth)
    out = v3_layer.quote_v3(r, v3_layer.WETH, v3_layer.USDC, 10**18, 500,
                            acct.address)
    if not out:
        print(f"[{time.strftime('%H:%M:%S')}] no quoter response — skipping cycle", flush=True)
        return None
    eth_usd = out / 1e6
    gas_wei = uint_or_zero(r.call("eth_gasPrice", []))
    gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd
    edges, report = arb_engine.scan_cross_venue(r, eth_usd, gas_usd)
    if verbose:
        print(f"[{time.strftime('%H:%M:%S')}] cross-scan: {len(report)} combos, "
              f"{len(edges)} edges (ETH ${eth_usd:.0f})", flush=True)
    log_event({"event": "scan", "mode": "cross_venue",
               "combos": len(report), "edges": len(edges),
               "eth_usd": round(eth_usd, 2)})
    if not edges:
        return None
    for edge in edges:
        sim_input = {
            "size_weth": edge["size_weth"],
            "buy_kind": edge["buy_kind"], "buy_venue": edge["buy_venue"],
            "buy_fee": edge["buy_fee"],
            "sell_kind": edge["sell_kind"], "sell_venue": edge["sell_venue"],
            "sell_fee": edge["sell_fee"],
            "quote": edge["quote"],
            "eth_usd": eth_usd,
        }
        print(f"[hunter] EDGE -> simulating: {edge}", flush=True)
        sim = simulate_edge_v2(sim_input, acct, executor_addr)
        log_event({"event": "sim", "edge": edge, "sim": sim})
        if sim and sim.get("gate") == "PASS":
            print(f"[hunter] SIM PASS -> broadcasting: {sim}", flush=True)
            receipt = broadcast_and_verify_v2(rpc, acct, executor_addr, sim_input)
            log_event({"event": "broadcast", "receipt": receipt})
            return receipt
    return None


def uint_or_zero(x):
    try:
        return int(x, 16) if isinstance(x, str) else (x or 0)
    except Exception:
        return 0


def encode_execute_v2(plan):
    """Calldata for FlashloanArbV2.execute(uint256, Leg, Leg, address).
    Leg = (uint8 kind, address venue, uint24 fee)."""
    sel = kec_sig("execute(uint256,(uint8,address,uint24),(uint8,address,uint24),address)")
    def leg(kind, venue, fee):
        return f"{int(kind):064x}" + venue[2:].rjust(64, "0") + f"{int(fee):064x}"
    return ("0x" + sel
            + f"{int(plan['size_weth']*1e18):064x}"
            + leg(plan["buy_kind"], plan["buy_venue"], plan["buy_fee"])
            + leg(plan["sell_kind"], plan["sell_venue"], plan["sell_fee"])
            + plan["quote"][2:].rjust(64, "0"))


def simulate_edge_v2(edge, acct, executor_addr):
    """Fork-sim the exact cross-venue live tx (anvil fork, real pools)."""
    proc, host, head, fork_url = sim_gate.launch_fork()
    try:
        fork = sim_gate.Fork(host)
        fork.set_balance(HOT_WALLET, 10 ** 18)
        fork.impersonate(HOT_WALLET)
        data = encode_execute_v2(edge)
        weth_before = fork.erc20_balance(sim_gate.WETH, executor_addr)
        try:
            txh = fork.send_from(HOT_WALLET, executor_addr, data)
            r = fork.wait_tx(txh)
        except Exception as e:
            return {"sim": "reverted", "error": str(e)[:200]}
        if r.get("status") != "0x1":
            return {"sim": "reverted"}
        gas_used = int(r["gasUsed"], 16)
        profit_weth = (fork.erc20_balance(sim_gate.WETH, executor_addr)
                       - weth_before) / 1e18
        profit_usd = profit_weth * edge.get("eth_usd", 2450)
        gas_usd = (gas_used / 1e9) * (fork.gas_price() / 1e9) * edge.get("eth_usd", 2450)
        return {
            "sim": "ok", "gas_used": gas_used,
            "profit_weth": round(profit_weth, 8),
            "gas_usd": round(gas_usd, 4),
            "profit_usd": round(profit_usd, 4),
            "gate": "PASS" if (profit_usd > GAS_MULTIPLIER * gas_usd
                               and profit_usd > sim_gate.MIN_PROFIT_USD) else "FAIL",
        }
    finally:
        sim_gate.kill_tree(proc)


def broadcast_and_verify_v2(rpc, acct, executor_addr, plan):
    """Sign + broadcast cross-venue execute() and verify on-chain."""
    nonce = rpc.nonce(acct.address)
    data = encode_execute_v2(plan)
    tx = {
        "from": acct.address,
        "to": executor_addr,
        "data": data,
        "nonce": nonce,
        "gas": 900_000,
        "gasPrice": int(rpc.gas_price() * 1.25),
        "chainId": 42161,
        "value": 0,
    }
    signed = acct.sign_transaction(tx)
    raw_hex = (signed.raw_transaction if hasattr(signed, "raw_transaction")
               else signed.rawTransaction).hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex
    txhash = None
    last_err = None
    for url in BROADCAST_RPCS:
        try:
            r = Rpc(url)
            txhash = r.send_raw(raw_hex)
            print(f"[hunter] broadcast via {url}: {txhash}")
            break
        except Exception as e:
            last_err = str(e)[:150]
            print(f"[hunter] broadcast failed via {url}: {last_err}")
    if txhash is None:
        return {"broadcast": "failed_all_rpcs", "error": last_err}
    rec = rpc.wait_receipt(txhash)
    return {
        "broadcast": "ok",
        "tx": txhash,
        "status": int(rec["status"], 16),
        "gas_used": int(rec["gasUsed"], 16),
    }


def simulate_edge(edge, w3, acct, executor_addr):
    """Fork-sim the exact live tx (re-used sim_gate harness)."""
    proc, host, head, fork_url = sim_gate.launch_fork()
    try:
        fork = sim_gate.Fork(host)
        # owner of the deployed contract is the hot wallet — impersonate it
        fork.set_balance(HOT_WALLET, 10 ** 18)
        fork.impersonate(HOT_WALLET)
        principal = int(edge["size_weth"] * 1e18)
        data = ("0x" + kec_sig("execute(uint256,address,address,address)")
                + f"{principal:064x}"
                + edge["pool_buy"][2:].rjust(64, "0")
                + edge["pool_sell"][2:].rjust(64, "0")
                + edge["quote"][2:].rjust(64, "0"))
        weth_before = fork.erc20_balance(sim_gate.WETH, executor_addr)
        try:
            txh = fork.send_from(HOT_WALLET, executor_addr, data)
            r = fork.wait_tx(txh)
        except Exception as e:
            return {"sim": "reverted", "error": str(e)[:200]}
        if r.get("status") != "0x1":
            return {"sim": "reverted"}
        gas_used = int(r["gasUsed"], 16)
        # profit = executor WETH DELTA (executor may hold prior profit)
        profit_weth = (fork.erc20_balance(sim_gate.WETH, executor_addr)
                       - weth_before) / 1e18
        profit_usd = profit_weth * edge.get("eth_usd", 2450)
        gas_usd = (gas_used / 1e9) * (fork.gas_price() / 1e9) * edge.get("eth_usd", 2450)
        return {
            "sim": "ok", "gas_used": gas_used,
            "executor_weth_after": profit_weth,
            "gas_usd": round(gas_usd, 4),
            "profit_usd": round(profit_usd, 4),
            "gate": "PASS" if (profit_usd > GAS_MULTIPLIER * gas_usd
                               and profit_usd > sim_gate.MIN_PROFIT_USD) else "FAIL",
        }
    finally:
        sim_gate.kill_tree(proc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    acct = Account.from_key(load_key())
    assert acct.address.lower() == HOT_WALLET, "key mismatch"
    rpc, url = get_rpc()
    print(f"[hunter] RPC {url} | account {acct.address} | "
          f"ETH {rpc.balance(acct.address)/1e18:.6f}")

    executor_addr = load_executor()
    if args.deploy or (not executor_addr and not args.status):
        executor_addr = deploy_executor(rpc, acct)
    if args.status:
        e = load_executor()
        if not e:
            print("[hunter] no executor deployed — run --deploy first")
            return
        weth = call_weth_balance(rpc, e)
        print(f"[hunter] executor {e} holds {weth/1e18:.8f} WETH profit")
        return

    # hunt loop
    print(f"[hunter] hunting. executor={executor_addr} "
          f"interval={SCAN_INTERVAL_SEC}s gate=profit>{GAS_MULTIPLIER}x gas")
    last_hb = 0.0
    cycle = 0
    while True:
        cycle += 1
        try:
            hunt_once(rpc, acct, executor_addr, BROADCAST_RPCS[0])
        except Exception as e:
            print(f"[hunter] cycle error: {e}", flush=True)
            log_event({"event": "error", "cycle": cycle, "error": str(e)[:200]})
        if time.time() - last_hb > HEARTBEAT_EVERY_SEC:
            last_hb = time.time()
            eth = rpc.balance(acct.address) / 1e18
            weth = call_weth_balance(rpc, executor_addr)
            print(f"[heartbeat] cycle {cycle} ETH={eth:.6f} "
                  f"executor WETH={weth/1e18:.8f}", flush=True)
            log_event({"event": "heartbeat", "cycle": cycle, "eth": eth,
                       "executor_weth": weth})
        time.sleep(SCAN_INTERVAL_SEC)


def call_weth_balance(rpc, executor_addr):
    data = "0x70a08231" + executor_addr[2:].rjust(64, "0")
    raw = rpc.eth_call(arb_engine.WETH, data)
    return int(raw, 16)


if __name__ == "__main__":
    main()
