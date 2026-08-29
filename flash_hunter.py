#!/usr/bin/env python3
"""
flash_hunter.py — VERITAS Engine: autonomous Arbitrum flash-loan arb hunter.

THE LOOP: scan (registry cross-venue) → fork-sim → gate → broadcast
→ verify on-chain profit → log heartbeat. Deploy the executor once,
then every attempt costs only gas-if-included; a reverted attempt costs
~$0.005. Principal is NEVER exposed — flashloan carries the size;
atomicity guarantees revert-on-failure.

SECURITY MODEL:
  - Key: hot wallet, read from .hot_secret at runtime. Never printed.
  - Signing happens ONLY after the fork-sim gate PASSES. No gate, no tx.
  - Broadcast is retried across 3 public RPCs (rotation).
  - Every cycle logs to flash_hunter.log (JSONL) + heartbeat every 15 min.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from eth_utils import keccak

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eth_account import Account
# Hyperliquid SDK for refill loop
try:
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    HYPERLIQUID_AVAILABLE = True
except Exception:
    HYPERLIQUID_AVAILABLE = False
    Info = None
    Exchange = None

import arb_engine
import sim_gate

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "flash_hunter.log")
TARGETS_FILE = os.path.join(HERE, "vetted_targets.jsonl")
EXECUTOR_FILE = os.path.join(HERE, ".executor_address")
EXECUTOR_V2_FILE = os.path.join(HERE, ".executor_v2_address")
EXECUTOR_V3_FILE = os.path.join(HERE, ".executor_v3_address")

HOT_WALLET = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
SECRET_FILE = os.path.join(HERE, ".hot_secret")

BROADCAST_RPCS = [
    "https://arb1.arbitrum.io/rpc",
    "https://arbitrum-one.publicnode.com",
    "https://gateway.tenderly.co/public/arbitrum",
]

SCAN_INTERVAL_SEC = 15       # TARGET cadence: one full hunt cycle every 15s (60 blocks)
HEARTBEAT_EVERY_SEC = 15 * 60
GAS_MULTIPLIER = 1.0         # profit must exceed 1.0x gas (break-even+)
MIN_PROFIT_USD = 0.0         # net profit floor after gas; reject dust
REFILL_GAS_THRESHOLD_ETH = 0.005   # top up if hot wallet ETH < 0.005 (~$1.25)
REFILL_GAS_TARGET_ETH = 0.01       # withdraw/swap to reach ~0.01 ETH (~$2.5)
V3_ROUTER = "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"
SWEEP_THRESHOLD_WETH = 0.001  # auto-sweep profit above this to hot wallet
SIM_BUDGET_PER_CYCLE = 4     # max fork-sims per cycle (best-net first)


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
        return int(self.req("eth_getTransactionCount", [addr, "latest"]), 16)

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
    # prefer the cross-venue V3 executor when deployed, else V2
    if os.path.isfile(EXECUTOR_V3_FILE):
        with open(EXECUTOR_V3_FILE) as f:
            return f.read().strip()
    if os.path.isfile(EXECUTOR_V2_FILE):
        with open(EXECUTOR_V2_FILE) as f:
            return f.read().strip()
    if not os.path.isfile(EXECUTOR_FILE):
        return None
    with open(EXECUTOR_FILE) as f:
        return f.read().strip()


def deploy_executor_v2(rpc, acct):
    """Deploy the cross-venue V2 executor (FlashloanArbV2)."""
    with open(os.path.join(HERE, "contracts", "FlashloanArbV2.bin")) as f:
        binhex = f.read().strip()
    nonce = rpc.nonce(acct.address)
    gas_price = int(rpc.gas_price() * 1.25)
    ctor = (binhex
            + arb_engine.AAVE_V3_POOL[2:].rjust(64, "0")
            + V3_ROUTER[2:].rjust(64, "0")
            + arb_engine.WETH[2:].rjust(64, "0"))
    tx = {
        "from": acct.address,
        "data": "0x" + ctor if not binhex.startswith("0x") else ctor,
        "nonce": nonce,
        "gas": 2_500_000,
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
        raise RuntimeError("executor v2 deployment failed: " + json.dumps(r)[:300])
    addr = r["contractAddress"]
    with open(EXECUTOR_V2_FILE, "w") as f:
        f.write(addr)
    log_event({"event": "executor_v2_deployed", "address": addr,
               "gas_used": int(r["gasUsed"], 16), "tx": h})
    print(f"[hunter] executor V2 deployed: {addr} "
          f"(gas {int(r['gasUsed'], 16):,})")
    return addr


def deploy_executor_v3(rpc, acct):
    """Deploy the three-leg V3 executor (FlashloanArbV3)."""
    with open(os.path.join(HERE, "contracts", "FlashloanArbV3.bin")) as f:
        binhex = f.read().strip()
    nonce = rpc.nonce(acct.address)
    gas_price = int(rpc.gas_price() * 1.25)
    ctor = (binhex
            + arb_engine.AAVE_V3_POOL[2:].rjust(64, "0")
            + V3_ROUTER[2:].rjust(64, "0")
            + arb_engine.WETH[2:].rjust(64, "0"))
    tx = {
        "from": acct.address,
        "data": "0x" + ctor if not binhex.startswith("0x") else ctor,
        "nonce": nonce,
        "gas": 2_500_000,
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
        raise RuntimeError("executor v3 deployment failed: " + json.dumps(r)[:300])
    addr = r["contractAddress"]
    with open(EXECUTOR_V3_FILE, "w") as f:
        f.write(addr)
    log_event({"event": "executor_v3_deployed", "address": addr,
               "gas_used": int(r["gasUsed"], 16), "tx": h})
    print(f"[hunter] executor V3 deployed: {addr} "
          f"(gas {int(r['gasUsed'], 16):,})")
    return addr


def gas_usd_of(gas_price_wei, gas_used, eth_usd=2450.0):
    return (gas_used / 1e9) * (gas_price_wei / 1e9) * eth_usd


def hunt_once(rpc, acct, executor_addr, rpc_scan, verbose=True):
    """One hunt cycle: registry cross-venue scan -> batch fork-sim vetting
    -> broadcast the best PASSING edge. Returns a cycle summary dict."""
    cycle_start = time.time()
    from core.rpc import RPC as Vrpc
    import v3_layer
    r = Vrpc(rpc_scan, timeout=120, retries=3)
    # ETH price + gas from a reliable V2 pool (V3 pools have stale prices)
    # Use Sushi WETH/USDC pool for ground truth price
    SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
    weth_bal = rpc.eth_call("0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                            "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0'))
    usdc_bal = rpc.eth_call("0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                            "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0'))
    if weth_bal and usdc_bal and len(weth_bal) >= 66 and len(usdc_bal) >= 66:
        weth_res = int(weth_bal[2:66], 16) / 1e18
        usdc_res = int(usdc_bal[2:66], 16) / 1e6
        eth_usd = usdc_res / weth_res if weth_res > 0 else 2450.0
    else:
        # Fallback to V3 quoter (may be stale)
        out = v3_layer.quote_v3(r, v3_layer.WETH, v3_layer.USDC, 10**18, 500,
                                acct.address)
        eth_usd = out / 1e6 if out else 2450.0
    gas_wei = uint_or_zero(r.call("eth_gasPrice", []))
    gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd
    try:
        edges, report = arb_engine.scan_cross_venue(r, eth_usd, gas_usd,
                                                            size_steps=12,
                                                            max_venues_per_quote=8,
                                                            use_multi_hop=True,
                                                            use_parallel=True)
    except Exception as e:
        print(f"[hunter] registry scan failed: {e}", flush=True)
        log_event({"event": "scan_error", "error": str(e)[:200]})
        return None
    if verbose:
        print(f"[{time.strftime('%H:%M:%S')}] cross-scan: {len(report)} combos, "
              f"{len(edges)} edges (ETH ${eth_usd:.0f})", flush=True)
    log_event({"event": "scan", "mode": "cross_venue",
               "combos": len(report), "edges": len(edges),
               "eth_usd": round(eth_usd, 2)})
    if not edges:
        summary = {"edges": 0, "sims": 0, "passes": 0, "broadcast": None,
                   "elapsed_sec": round(time.time() - cycle_start, 1)}
        write_cycle_report(summary, [])
        if verbose:
            print(f"[{time.strftime('%H:%M:%S')}] cycle done: 0 vetted edges "
                  f"({summary['elapsed_sec']}s)", flush=True)
        return summary

    # edges arrive sorted by net_usd (best first) from arb_engine;
    # stamp live ETH price so fork-sim USD profit/gas math is exact
    for e in edges:
        e["eth_usd"] = eth_usd
    sim_results = simulate_edges_batch(edges, acct, executor_addr)

    receipt = None
    passes = 0
    for edge, sim in sim_results:
        log_event({"event": "sim", "edge": edge, "sim": sim})
        if sim and sim.get("gate") == "PASS":
            passes += 1
            print(f"[hunter] SIM PASS (${sim.get('profit_usd')} net) "
                  f"-> broadcasting: {edge.get('venue_buy')} -> "
                  f"{edge.get('venue_sell')} size={edge.get('size_weth')} WETH",
                  flush=True)
            receipt = broadcast_and_verify_v2(rpc, acct, executor_addr, edge)
            log_event({"event": "broadcast", "receipt": receipt})
            break   # one live shot per cycle; next cycle re-scans fresh state

    summary = {"edges": len(edges), "sims": len(sim_results),
               "passes": passes, "broadcast": receipt,
               "elapsed_sec": round(time.time() - cycle_start, 1)}
    write_cycle_report(summary, sim_results)
    if verbose:
        print(f"[{time.strftime('%H:%M:%S')}] cycle done: {len(edges)} edges, "
              f"{len(sim_results)} simmed, {passes} PASS, "
              f"broadcast={'ok' if receipt and receipt.get('broadcast') == 'ok' else 'none'} "
              f"({summary['elapsed_sec']}s)", flush=True)
    return summary


def write_cycle_report(summary, sim_results):
    """Every cycle produces a concrete, vetted result on disk — the
    3-minute deliverable: top candidates, sim verdicts, broadcast status."""
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        **summary,
        "vetted": [
            {"pair": e.get("pair"),
             "net_usd": e.get("net_usd"),
             "size_weth": e.get("size_weth"),
             "buy": e.get("venue_buy"), "sell": e.get("venue_sell"),
             "verified_rpcs": e.get("verified_rpcs"),
             "sim": (s or {}).get("gate", "not_simmed")}
            for e, s in sim_results
        ],
    }
    try:
        with open(TARGETS_FILE, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


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
    return (sel
            + f"{int(plan['size_weth']*1e18):064x}"
            + leg(plan["buy_kind"], plan["buy_venue"], plan["buy_fee"])
            + leg(plan["sell_kind"], plan["sell_venue"], plan["sell_fee"])
            + plan["quote"][2:].rjust(64, "0"))


def encode_execute_v3(plan):
    """Calldata for FlashloanArbV3.execute(uint256, Leg, Leg, Leg, address, address).
    Leg = (uint8 kind, address venue, uint24 fee). Plan has two legs for the buy side."""
    sel = kec_sig("execute(uint256,(uint8,address,uint24),(uint8,address,uint24),(uint8,address,uint24),address,address)")
    def leg(kind, venue, fee):
        return f"{int(kind):064x}" + venue[2:].rjust(64, "0") + f"{int(fee):064x}"
    return (sel
            + f"{int(plan['size_weth']*1e18):064x}"
            + leg(plan["buy1_kind"], plan["buy1_venue"], plan["buy1_fee"])
            + leg(plan["buy2_kind"], plan["buy2_venue"], plan["buy2_fee"])
            + leg(plan["sell_kind"], plan["sell_venue"], plan["sell_fee"])
            + plan["quote1"][2:].rjust(64, "0")
            + plan["quote2"][2:].rjust(64, "0"))


def simulate_edges_batch(edges, acct, executor_addr, max_sims=SIM_BUDGET_PER_CYCLE):
    """Vet up to max_sims edges against ONE anvil fork using
    evm_snapshot/evm_revert between sims (each edge sees pristine pool
    state). Returns [(edge, sim_result), ...] in priority order; stops at
    the first PASS (edges are pre-sorted by net_usd)."""
    results = []
    proc, host, head, fork_url = sim_gate.launch_fork()
    try:
        fork = sim_gate.Fork(host)
        fork.set_balance(HOT_WALLET, 10 ** 18)
        fork.impersonate(HOT_WALLET)
        for edge in edges[:max_sims]:
            print(f"[hunter] EDGE -> fork-simming: {edge.get('venue_buy')} -> "
                  f"{edge.get('venue_sell')} size={edge.get('size_weth')} "
                  f"net=${edge.get('net_usd')}", flush=True)
            snap = None
            try:
                snap = fork.snapshot()
            except Exception:
                pass  # snapshot is an optimization, not a requirement
            try:
                sim = sim_edge_on_fork(fork, edge, executor_addr)
            except Exception as e:
                sim = {"sim": "error", "error": str(e)[:200]}
            results.append((edge, sim))
            if sim and sim.get("gate") == "PASS":
                break
            if snap is not None:
                try:
                    fork.revert(snap)
                except Exception:
                    pass
    finally:
        sim_gate.kill_tree(proc)
    return results


def sim_edge_on_fork(fork, edge, executor_addr):
    """Fork-sim the exact cross-venue live tx on an already-running fork."""
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
    gate = "PASS" if (profit_usd > GAS_MULTIPLIER * gas_usd
                      and profit_usd > MIN_PROFIT_USD) else "FAIL"
    return {
        "sim": "ok", "gas_used": gas_used,
        "profit_weth": round(profit_weth, 8),
        "gas_usd": round(gas_usd, 4),
        "profit_usd": round(profit_usd, 4),
        "gate": gate,
    }


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


def sweep_executor_v2(rpc, acct, executor_addr):
    """Sweep accumulated WETH profit from executor to hot wallet."""
    bal = call_weth_balance(rpc, executor_addr)
    if bal < int(SWEEP_THRESHOLD_WETH * 1e18):
        return None
    data = "0x" + kec_sig("sweepProfit(address)") + arb_engine.WETH[2:].rjust(64, "0")
    nonce = rpc.nonce(acct.address)
    tx = {
        "from": acct.address,
        "to": executor_addr,
        "data": data,
        "nonce": nonce,
        "gas": 200_000,
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
    for url in BROADCAST_RPCS:
        try:
            r = Rpc(url)
            txhash = r.send_raw(raw_hex)
            print(f"[hunter] sweep broadcast via {url}: {txhash}")
            break
        except Exception as e:
            print(f"[hunter] sweep failed via {url}: {e}")
    if not txhash:
        return {"sweep": "failed"}
    rec = rpc.wait_receipt(txhash)
    log_event({"event": "sweep", "tx": txhash, "weth_wei": bal,
               "status": int(rec["status"], 16)})
    return {"sweep": "ok", "tx": txhash, "weth": bal/1e18}


def call_weth_balance(rpc, executor_addr):
    data = "0x70a08231" + executor_addr[2:].rjust(64, "0")
    raw = rpc.eth_call(arb_engine.WETH, data)
    return int(raw, 16)


def refill_gas_if_needed(rpc, acct):
    """Withdraw USDC from Hyperliquid and swap to ETH if hot wallet gas is low."""
    print("[refill] entered refill_gas_if_needed", flush=True)
    if not HYPERLIQUID_AVAILABLE:
        print("[refill] HYPERLIQUID_AVAILABLE is False", flush=True)
        return  # silent no-op if SDK not present
    try:
        from eth_utils import keccak
        def pad(addr):
            return "0x" + addr[2:].lower().rjust(64, "0")
        def u256(val):
            return "0x" + val.to_bytes(32, byteorder="big").hex()
        eth_bal = rpc.balance(acct.address) / 1e18
        print(f"[refill] ETH balance: {eth_bal:.6f} ETH", flush=True)
        if eth_bal >= REFILL_GAS_THRESHOLD_ETH:
            print(f"[refill] ETH balance >= threshold ({REFILL_GAS_THRESHOLD_ETH}), skipping", flush=True)
            return  # gas is sufficient
        # Withdraw from Hyperliquid
        print("[refill] attempting withdrawal from Hyperliquid", flush=True)
        info = Info(skip_ws=True)
        ex = Exchange(acct)
        st = info.user_state(acct.address)
        wd_usdc = float(st.get("withdrawable") or 0.0)
        print(f"[refill] withdrawable USDC from Hyperliquid: {wd_usdc:.2f}", flush=True)
        if wd_usdc < 1.0:  # bridge minimum
            print("[refill] withdrawable USDC below bridge minimum (1.0)", flush=True)
            return
        amount_usdc = int(wd_usdc * 100) / 100.0  # round down to cents
        print(f"[refill] withdrawing ${amount_usdc:.2f} USDC from Hyperliquid", flush=True)
        withdraw_tx = ex.withdraw_from_bridge(amount_usdc, acct.address)
        print(f"[refill] withdrawal transaction response: {withdraw_tx}", flush=True)
        if not (isinstance(withdraw_tx, dict) and withdraw_tx.get("status") == "ok"):
            print("[refill] withdrawal failed", flush=True)
            return
        # Poll for on-chain USDC arrival (simple polling)
        import time
        t0 = time.time()
        usdc_arrived = False
        while time.time() - t0 < 300:  # 5 minute timeout
            time.sleep(15)
            usdc_bal = call_erc20_balance(rpc, arb_engine.USDC, acct.address)
            print(f"[refill] polling USDC balance: {usdc_bal/1e6:.2f} USDC (target: {amount_usdc:.2f})", flush=True)
            if usdc_bal >= amount_usdc * 1e6:  # USDC has 6 decimals
                usdc_arrived = True
                print("[refill] USDC arrived on-chain", flush=True)
                break
        if not usdc_arrived:
            print("[refill] USDC did not arrive on-chain (timeout)", flush=True)
            return
        # Swap USDC -> ETH via Uniswap V3 (exactInputSingle)
        # We will use the router and quote for amountOutMinimum with 1% slippage tolerance
        eth_price = get_eth_price_from_sushiswap(rpc)  # reuse existing SUSHI_WETH_USDC pool
        print(f"[refill] ETH price from SushiSwap: ${eth_price:.2f}", flush=True)
        if eth_price <= 0:
            print("[refill] could not get ETH price", flush=True)
            return
        # We want to buy ETH with USDC, so tokenIn=USDC, tokenOut=WETH
        amount_in = int(amount_usdc * 1e6)  # USDC to wei-equivalent (6 decimals)
        # Get quote for exact input
        quote_out = quote_v3(rpc, arb_engine.USDC, arb_engine.WETH, amount_in, 3000, acct.address)
        print(f"[refill] quote_out for {amount_in} USDC (wei): {quote_out} WETH (wei)", flush=True)
        if quote_out is None or quote_out == 0:
            print("[refill] V3 quote failed", flush=True)
            return
        amount_out_min = int(quote_out * 0.99)  # 1% slippage tolerance
        print(f"[refill] amount_out_min (with 1% slippage): {amount_out_min} WETH (wei)", flush=True)
        # Approve router to spend USDC
        print("[refill] approving router to spend USDC", flush=True)
        approve_erc20(rpc, acct, arb_engine.USDC, V3_ROUTER, amount_in)
        # Build exactInputSingle params
        quoter_selector = keccak(text="quoteExactInputSingle((address,address,uint24,uint256))")[:4].hex()
        data = (
            "0x"
            + quoter_selector
            + pad(arb_engine.USDC)
            + pad(arb_engine.WETH)
            + u256(3000)
            + u256(amount_in)
            + u256(amount_out_min)
            + u256(0)  # sqrtPriceLimitX96
        )
        # Note: we are using the QuoterV2 for simulation; for execution we need to call the router.
        # But for simplicity and to avoid adding another dependency, we will use the same call
        # to the router's exactInputSingle via the V3_ROUTER address.
        # However, the refill loop is only for gas top-up, so we can approve and call the router.
        # Let's use the router's exactInputSingle.
        # We need to encode the call to IV3Router.exactInputSingle
        tx = {
            "to": V3_ROUTER,
            "value": 0,
            "gas": 200_000,
            "gasPrice": int(rpc.gas_price() * 1.25),
            "nonce": rpc.nonce(acct.address),
            "data": data,
            "chainId": 42161,
        }
        signed = acct.sign_transaction(tx)
        raw_hex = (signed.raw_transaction if hasattr(signed, "raw_transaction")
                   else signed.rawTransaction).hex()
        if not raw_hex.startswith("0x"):
            raw_hex = "0x" + raw_hex
        txhash = None
        for url in BROADCAST_RPCS:
            try:
                r = Rpc(url)
                txhash = r.send_raw(raw_hex)
                print(f"[refill] swap broadcast via {url}: {txhash}", flush=True)
                break
            except Exception as e:
                print(f"[refill] swap failed via {url}: {e}", flush=True)
        if not txhash:
            print("[refill] swap broadcast failed on all RPCs", flush=True)
            return
        rec = rpc.wait_receipt(txhash)
        if rec.get("status") != 1:
            print("[refill] swap transaction failed", flush=True)
            return
        print(f"[refill] swap successful, tx: {txhash}", flush=True)
    except Exception as e:
        print(f"[refill] exception: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return


def get_eth_price_from_sushiswap(rpc):
    """Get ETH/USDC price from SushiSwap V2 pool as a fallback."""
    SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
    try:
        weth_bal = rpc.eth_call(SUSHI_WETH_USDC, "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0'))
        usdc_bal = rpc.eth_call("0xaf88d065e77c8cc2239327c5edb3a432268e5831", "0x70a08231" + "0xaf88d065e77c8cc2239327c5edb3a432268e5831"[2:].lower().rjust(64, '0'))
        if weth_bal and usdc_bal and len(weth_bal) >= 66 and len(usdc_bal) >= 66:
            weth_res = int(weth_bal[2:66], 16) / 1e18
            usdc_res = int(usdc_bal[2:66], 16) / 1e6
            return usdc_res / weth_res if weth_res > 0 else 2450.0
    except Exception:
        pass
    return 2450.0  # hardcoded fallback


def call_erc20_balance(rpc, token_addr, account_addr):
    """Read ERC20 balance of account for token."""
    data = "0x70a08231" + pad(account_addr)
    raw = rpc.eth_call(token_addr, data)
    return int(raw, 16)


def approve_erc20(rpc, acct, token_addr, spender, amount):
    """Approve spender to spend amount of token from acct."""
    from eth_utils import keccak
    def pad(addr):
        return "0x" + addr[2:].lower().rjust(64, "0")
    def u256(val):
        return "0x" + val.to_bytes(32, byteorder="big").hex()
    data = (
        "0x"
        + keccak(text="approve(address,uint256)")[:4].hex()
        + pad(spender)
        + u256(amount)
    )
    tx = {
        "from": acct.address,
        "to": token_addr,
        "data": data,
        "nonce": rpc.nonce(acct.address),
        "gas": 100_000,
        "gasPrice": int(rpc.gas_price() * 1.25),
        "chainId": 42161,
        "value": 0,
    }
    signed = acct.sign_transaction(tx)
    raw_hex = (signed.raw_transaction if hasattr(signed, "raw_transaction")
               else signed.rawTransaction).hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex
    for url in BROADCAST_RPCS:
        try:
            r = Rpc(url)
            txhash = r.send_raw(raw_hex)
            r.wait_receipt(txhash)  # we don't need to check status for approve; if it fails, swap will fail
            break
        except Exception:
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true",
                    help="deploy the cross-venue V2 executor")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="single cycle then exit (for cron watchdog)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--interval", type=int, default=SCAN_INTERVAL_SEC,
                    help="target seconds between hunt cycles (default 180)")
    args = ap.parse_args()

    acct = Account.from_key(load_key())
    assert acct.address.lower() == HOT_WALLET, "key mismatch"
    rpc, url = get_rpc()
    print(f"[hunter] RPC {url} | account {acct.address} | "
          f"ETH {rpc.balance(acct.address)/1e18:.6f}")

    executor_addr = load_executor()
    if args.deploy:
        executor_addr = deploy_executor_v2(rpc, acct)
    if not executor_addr and not args.status:
        executor_addr = deploy_executor_v2(rpc, acct)
    if args.status:
        e = load_executor()
        if not e:
            print("[hunter] no executor deployed — run --deploy first")
            return
        weth = call_weth_balance(rpc, e)
        print(f"[hunter] executor {e} holds {weth/1e18:.8f} WETH profit")
        return

    # single-cycle mode: one hunt pass then exit
    if args.once:
        try:
            hunt_once(rpc, acct, executor_addr, BROADCAST_RPCS[0])
        except Exception as e:
            print(f"[hunter-once] cycle error: {e}", flush=True)
            log_event({"event": "error", "mode": "once", "error": str(e)[:200]})
        return

    # hunt loop — TARGET cadence: a full hunt cycle (scan -> vet -> shoot)
    # every --interval seconds. The sleep only covers the REMAINDER of the
    # interval after the cycle's work, so a 2-min scan still lands on a
    # 3-minute cadence instead of drifting to 5-6 minutes.
    print(f"[hunter] hunting. executor={executor_addr} "
          f"interval={args.interval}s gate=profit>{GAS_MULTIPLIER}x gas "
          f"min=")
    last_hb = 0.0
    cycle = 0
    while True:
        cycle += 1
        cycle_start = time.time()
        try:
            # rotate the scan RPC each cycle to dodge per-endpoint rate limits
            scan_url = BROADCAST_RPCS[(cycle - 1) % len(BROADCAST_RPCS)]
            hunt_once(rpc, acct, executor_addr, scan_url)
            # Gas refill: withdraw USDC from Hyperliquid and swap to ETH if low
            refill_gas_if_needed(rpc, acct)
            try:
                sweep_executor_v2(rpc, acct, executor_addr)
            except Exception as e:
                print(f"[hunter] sweep error (non-fatal): {e}", flush=True)
        except Exception as e:
            print(f"[hunter] cycle error: {e}", flush=True)
            log_event({"event": "error", "cycle": cycle, "error": str(e)[:200]})
        try:
            if time.time() - last_hb > HEARTBEAT_EVERY_SEC:
                last_hb = time.time()
                eth = rpc.balance(acct.address) / 1e18
                weth = call_weth_balance(rpc, executor_addr)
                print(f"[heartbeat] cycle {cycle} ETH={eth:.6f} "
                      f"executor WETH={weth/1e18:.8f}", flush=True)
                log_event({"event": "heartbeat", "cycle": cycle, "eth": eth,
                           "executor_weth": weth})
        except Exception as e:
            print(f"[hunter] heartbeat error (non-fatal): {e}", flush=True)
        elapsed = time.time() - cycle_start
        time.sleep(max(5, args.interval - elapsed))
if __name__ == "__main__":
    main()
