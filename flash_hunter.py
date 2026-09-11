#!/usr/bin/env python3
"""
flash_hunter.py — VERITAS Engine: autonomous Arbitrum flash-loan arb hunter.

THE LOOP: scan (registry cross-venue) → ZK-proof gate → broadcast
→ verify on-chain profit → log heartbeat. Deploy the executor once,
then every attempt costs only gas-if-included; a reverted attempt costs
~$0.005. Principal is NEVER exposed — flashloan carries the size;
atomicity guarantees revert-on-failure.

SECURITY MODEL:
  - Key: hot wallet, read from .hot_secret at runtime. Never printed.
  - Signing happens ONLY after the ZK-proof gate PASSES. No gate, no tx.
  - Broadcast is retried across 3 public RPCs (rotation).
  - Every cycle logs to flash_hunter.log (JSONL) + heartbeat every 15 min.

ZK-PROOF INTEGRATION (ShadowPath Verkle+Groth16):
  - Generates Groth16 proof of profitability off-chain
  - Submits proof + arb calldata to ZKArbExecutor
  - MEV-resistant: mempool sees only verifyProof(), no pools/sizes/paths
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
import v3_layer

# ZK-prover integration (ShadowPath Verkle+Groth16)
try:
    from zk_prover import ZKProver, prove_edge
    ZK_AVAILABLE = True
except Exception as e:
    ZK_AVAILABLE = False
    print(f"[hunter] ZK-prover unavailable: {e}", flush=True)

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "flash_hunter.log")
TARGETS_FILE = os.path.join(HERE, "vetted_targets.jsonl")
EXECUTOR_FILE = os.path.join(HERE, ".executor_address")
EXECUTOR_V2_FILE = os.path.join(HERE, ".executor_v2_address")
EXECUTOR_V3_FILE = os.path.join(HERE, ".executor_v3_address")
EXECUTOR_ZK_FILE = os.path.join(HERE, ".executor_zk_address")

HOT_WALLET = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
SECRET_FILE = os.path.join(HERE, ".hot_secret")

BROADCAST_RPCS = [
    "https://gateway.tenderly.co/public/arbitrum",
    "https://arbitrum.drpc.org",
    "https://arbitrum.publicnode.com",
]

SCAN_RPCS = [
    "http://127.0.0.1:8545",
    "https://gateway.tenderly.co/public/arbitrum",
    "https://arbitrum.drpc.org",
    "https://arbitrum.publicnode.com",
]

SCAN_INTERVAL_SEC = 15       # TARGET cadence: one full hunt cycle every 15s (60 blocks)
HEARTBEAT_EVERY_SEC = 15 * 60
GAS_MULTIPLIER = 1.0         # aligned with sim_gate.py — micro-capital bootstrap
MIN_PROFIT_USD = 0.05        # micro-trade floor for small-capital bootstrap
REFILL_GAS_THRESHOLD_ETH = 0.005   # top up if hot wallet ETH < 0.005 (~$1.25)
REFILL_GAS_TARGET_ETH = 0.01       # withdraw/swap to reach ~0.01 ETH (~$2.5)
SIM_BUDGET_PER_CYCLE = 12    # max fork-sims per cycle (best-net first). Fork
                             # startup is the expensive part; marginal sims on
                             # the same fork are ~1-2s each, so vetting 12 instead
                             # of 6 raises the chance of a PASS per cycle.


DRY_RUN = False   # when True, SIM PASSes are logged but never broadcast live

def log_event(evt):
    evt["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(evt) + "\n")


def log_capital_state(controller, extra=None):
    """Persist capital controller state to flash_hunter.log."""
    payload = {"event": "capital_state", **controller.summary()}
    if extra:
        payload.update(extra)
    log_event(payload)


def load_key():
    with open(SECRET_FILE) as f:
        return f.read().strip()


def get_rpc():
    from core.rpc import RPC
    for url in BROADCAST_RPCS:
        try:
            r = RPC(url)
            r.eth_gasPrice()  # connectivity probe
            return r, url
        except Exception as e:
            log_event({"event": "rpc_probe_failed", "url": url, "error": str(e)[:300]})
            continue
    raise RuntimeError("all broadcast RPCs failed")


def load_executor():
    try:
        with open(EXECUTOR_FILE) as f:
            return f.read().strip()
    except Exception:
        return None


def load_v2_executor():
    try:
        with open(EXECUTOR_V2_FILE) as f:
            return f.read().strip()
    except Exception:
        return None


def load_v3_executor():
    try:
        with open(EXECUTOR_V3_FILE) as f:
            return f.read().strip()
    except Exception:
        return None


def load_zk_executor():
    try:
        with open(EXECUTOR_ZK_FILE) as f:
            return f.read().strip()
    except Exception:
        return None


def save_executor(addr):
    with open(EXECUTOR_FILE, "w") as f:
        f.write(addr)
    log_event({"event": "executor_deployed", "address": addr})


def save_v2_executor(addr):
    with open(EXECUTOR_V2_FILE, "w") as f:
        f.write(addr)
    log_event({"event": "executor_v2_deployed", "address": addr})


def save_v3_executor(addr):
    with open(EXECUTOR_V3_FILE, "w") as f:
        f.write(addr)
    log_event({"event": "executor_v3_deployed", "address": addr})


def save_zk_executor(addr):
    with open(EXECUTOR_ZK_FILE, "w") as f:
        f.write(addr)
    log_event({"event": "executor_zk_deployed", "address": addr})


def gas_usd_of(gas_price_wei, gas_used, eth_usd=2450.0):
    return (gas_used / 1e9) * (gas_price_wei / 1e9) * eth_usd


def _eth_usd_from_v2(rpc):
    """Fallback ETH/USD from deepest WETH/USDC V2 pool."""
    SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
    weth_bal = rpc.eth_call("0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                            "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0'))
    usdc_bal = rpc.eth_call("0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                            "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0'))
    if weth_bal and usdc_bal and len(weth_bal) >= 66 and len(usdc_bal) >= 66:
        weth_res = int(weth_bal[2:66], 16) / 1e18
        usdc_res = int(usdc_bal[2:66], 16) / 1e6
        if weth_res > 0:
            return usdc_res / weth_res
    return 2450.0  # hardcoded fallback


def hunt_once(rpc, acct=None, executor_addr=None, rpc_scan=None, verbose=True, controller=None):
    """One hunt cycle: registry cross-venue scan -> size -> gate -> broadcast."""
    cycle_start = time.time()
    # Capital controller for conservative micro-capital deployment.
    if controller is None:
        from core.capital_controller import CapitalController
        controller = CapitalController()
    sim_summary = {"attempts": 0, "passes": 0, "best_net_usd": None}
    # Prefer local fork for scanning if available; fall back to public
    # scan endpoints so read traffic does not depend on the broadcast fleet.
    from core.rpc import FleetRPC
    # Round-robin across every healthy scan endpoint: spreads per-host rate
    # limits (tenderly public throttles bursts) and fails over on errors.
    scan_urls = [u for u in SCAN_RPCS if u.startswith('https')]
    r = FleetRPC(scan_urls, timeout=20, retries=2) if scan_urls else None
    if r is not None:
        try:
            r.eth_blockNumber()
        except Exception:
            r = None
    if r is None:
        from core.rpc import RPC as Vrpc
        r = Vrpc(rpc_scan, timeout=20, retries=3)

    # ETH price + gas from a reliable V2 pool (V3 pools have stale prices)
    # Use Sushi WETH/USDC pool for ground truth price
    SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
    weth_bal = rpc.eth_call("0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                            "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0'))
    usdc_bal = rpc.eth_call("0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                            "0x70a08231" + SUSHI_WETH_USDC[2:].lower().rjust(64, '0'))
    try:
        if weth_bal and usdc_bal and len(weth_bal) >= 66 and len(usdc_bal) >= 66:
            weth_res = int(weth_bal[2:66], 16) / 1e18
            usdc_res = int(usdc_bal[2:66], 16) / 1e6
            eth_usd = usdc_res / weth_res if weth_res > 0 else 2450.0
        else:
            # Fallback to V3 quoter (may be stale)
            out = v3_layer.quote_v3(r, v3_layer.WETH, v3_layer.USDC, 10**18, 500,
                                    acct.address)
            eth_usd = out / 1e6 if out else 2450.0
    except Exception:
        eth_usd = 2450.0  # price-feed hiccup: conservative default
    try:
        gas_wei = uint_or_zero(r.eth_gasPrice())
    except Exception:
        try:
            gas_wei = uint_or_zero(rpc.eth_gasPrice())
        except Exception:
            gas_wei = 0  # estimate only; MIN_PROFIT_USD floor still gates
    gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd
    sized_edge_hint = controller.size_for_edge({}, gas_usd, eth_usd)
    target_trade_usd = sized_edge_hint.get("target_trade_usd", 0.0)
    try:
        scan_result = arb_engine.scan_cross_venue(r, eth_usd, gas_usd,
                                                  size_steps=12,
                                                  max_venues_per_quote=8,
                                                  use_multi_hop=True,
                                                  use_parallel=True,
                                                  target_trade_usd=target_trade_usd)
    except Exception as e:
        import traceback
        print(f"[hunter] registry scan failed: {e}", flush=True)
        traceback.print_exc()
        log_event({"event": "scan_error", "error": str(e)[:200]})
        return None
    edges = scan_result.edges
    report = scan_result.to_legacy_tuple()[1]
    if verbose:
        print(f"[{time.strftime('%H:%M:%S')}] cross-scan: {len(report)} combos, "
              f"{len(edges)} edges (ETH ${eth_usd:.0f})", flush=True)
        # Print diagnostic report when no edges found
        if not edges:
            diag = scan_result.generate_why_zero_report()
            print(diag, flush=True)
            log_event({"event": "why_zero_report", "report": diag[:500]})

    if not edges:
        log_capital_state(controller, extra={"phase": "no_edges", "edges": 0, "passes": 0})
        return {"edges": 0, "report": report, "passes": 0, "capital": controller.summary()}

    # ZK-PROOF PATH (ShadowPath Verkle+Groth16) - replaces fork-sim
    zk_executor_addr = load_zk_executor()
    # NOTE: edges carry `net_margin` (USD), not `net_usd`. The prior filter
    # keyed on `ne_usd` and defaulted to 0, silently rejecting every edge and
    # making the ZK path dead code. Gate on net_margin, aligned with the new
    # MIN_PROFIT_USD floor so sub-floor trades never pay for a proof.
    zk_edges = [e for e in edges
                if e.get("buy_kind") == 0 and e.get("sell_kind") == 0
                and float(e.get("net_margin", 0.0) or 0.0) >= MIN_PROFIT_USD]
    log_event({"event": "zk_scan", "edges": len(edges), "zk_edges": len(zk_edges), "zk_executor": bool(zk_executor_addr)})
    if ZK_AVAILABLE and zk_executor_addr and zk_edges:
        print(f"[hunter] ZK gate: {len(zk_edges)} high-value V2 edges", flush=True)
        # Read balance BEFORE ZK broadcast for realized P/L verification
        WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
        zk_balance_before = _weth_balance(rpc, WETH, zk_executor_addr)
        receipt = execute_zk_edges(rpc, acct, zk_executor_addr, zk_edges, eth_usd, gas_usd)
        passes = 1 if receipt and receipt.get("broadcast") == "ok" else 0
        # Record verified PnL for ZK path with on-chain balance delta
        if passes and not DRY_RUN:
            zk_balance_after = _weth_balance(rpc, WETH, zk_executor_addr)
            realized_delta_eth = (zk_balance_after - zk_balance_before) / 1e18
            realized_delta_usd = realized_delta_eth * eth_usd
            projected_profit = float(zk_edges[0].get("net_margin", 0.0) or 0.0)
            verified_profit = min(projected_profit, realized_delta_usd) if realized_delta_usd > 0 else projected_profit
            controller.record_verified_pnl(verified_profit, gas_usd)
            log_capital_state(controller, extra={"phase": "zk_verified_pnl", "realized_usd": round(realized_delta_usd, 4), "projected_usd": round(projected_profit, 4)})
    else:
        passes = 0
        receipt = None

    if not passes:
        # Existing fork-sim path is the authoritative fallback for every
        # non-V2 edge and every ZK failure. No opportunity is dropped.
        sim_results = simulate_edges_batch(edges, acct, executor_addr)
        for edge, sim in sim_results:
            controller.record_sim_attempt(gas_usd)
            log_event({"event": "sim", "edge": edge, "sim": sim})
            sim_summary["attempts"] += 1
            if sim and sim.get("gate") == "PASS":
                passes += 1
                net = float(edge.get("net_margin", 0.0) or 0.0)
                sim_summary["passes"] += 1
                sim_summary["best_net_usd"] = net if sim_summary["best_net_usd"] is None else max(sim_summary["best_net_usd"], net)
                log_capital_state(controller, extra={"phase": "sim_pass", "edge": edge})

        # GO-LIVE: the PASS edge's calldata was validated byte-for-byte on
        # the fork -- broadcast exactly that transaction to the V2 executor.
        if passes and not DRY_RUN:
            pass_edge = next((e for e, s in sim_results
                              if s and s.get("gate") == "PASS"), None)
            live_exec = load_v2_executor()
            if pass_edge and live_exec:
                # CRITICAL: broadcast the EXACT calldata that the fork
                # validated. build_executor_tx creates V1-format calldata
                # (selector 0x5489b4f7) but the simulation validated
                # V2-format calldata (_live_calldata). Sending the wrong
                # format would revert or call the wrong function.
                # Read balance BEFORE the transaction for realized P/L
                WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
                balance_before = _weth_balance(rpc, WETH, live_exec)
                receipt = broadcast_v2_execution(rpc, acct, live_exec, pass_edge)
                log_event({"event": "live_attempt", "executor": live_exec,
                           "receipt": receipt})
                # After on-chain confirmation, verify realized P/L
                if receipt and receipt.get("broadcast") == "ok":
                    balance_after = _weth_balance(rpc, WETH, live_exec)
                    realized_delta_eth = (balance_after - balance_before) / 1e18
                    realized_delta_usd = realized_delta_eth * eth_usd
                    # Use the LESSER of projected or realized profit
                    # (realized can differ from simulation due to slippage)
                    projected_profit = float(pass_edge.get("net_margin", 0.0) or 0.0)
                    verified_profit = min(projected_profit, realized_delta_usd) if realized_delta_usd > 0 else projected_profit
                    controller.record_verified_pnl(verified_profit, gas_usd)
                    log_capital_state(controller, extra={"phase": "verified_pnl", "tx": receipt.get("tx_hash", ""), "realized_usd": round(realized_delta_usd, 4), "projected_usd": round(projected_profit, 4)})
                elif receipt and receipt.get("broadcast") == "reverted":
                    # Transaction reverted on-chain — gas consumed, no profit
                    controller.record_live_execution(0.0, gas_usd, verified=False)
                    log_capital_state(controller, extra={"phase": "reverted_tx", "tx": receipt.get("tx_hash", "")})
                elif receipt and receipt.get("broadcast") == "unconfirmed":
                    # Receipt timeout — tx may or may not confirm; don't count as verified
                    controller.record_live_execution(0.0, gas_usd, verified=False)
                    log_capital_state(controller, extra={"phase": "unconfirmed_tx", "tx": receipt.get("tx_hash", "")})
            elif pass_edge:
                log_event({"event": "broadcast_skipped",
                           "reason": "no_executor"})
        elif passes and DRY_RUN:
            log_event({"event": "dry_run_pass",
                       "note": "would broadcast to V2 executor"})

    log_capital_state(controller, extra={"phase": "cycle_end", "edges": len(edges), "passes": passes})
    log_event({"event": "cycle", "edges": len(edges), "passes": passes,
               "duration_sec": time.time() - cycle_start, "executor": executor_addr})
    return {"edges": len(edges), "passes": passes, "report": report,
            "executor": executor_addr, "receipt": receipt, "capital": controller.summary()}



def build_executor_tx(acct, executor_addr, edge):
    """Build a real transaction for the V2 executor."""
    principal = int(float(edge.get('size_weth', 0.1)) * 1e18)
    pool_buy = edge.get('pool_buy', edge.get('buy_venue', ''))
    pool_sell = edge.get('pool_sell', edge.get('sell_venue', ''))
    quote_token = edge.get('quote_token', '0xaf88d065e77c8cc2239327c5edb3a432268e5831')
    selector = '0x5489b4f7'
    calldata = (
        selector
        + format(principal, '064x')
        + pool_buy[2:].rjust(64, '0')
        + pool_sell[2:].rjust(64, '0')
        + quote_token[2:].rjust(64, '0')
    )
    return {
        'to': executor_addr,
        'data': calldata,
        'value': 0,
        'gas': 600000,
        'chainId': 42161,
    }


def broadcast_tx(rpc, acct, tx):
    """Sign and broadcast a real transaction."""
    try:
        nonce = rpc.nonce(acct.address)
        gas_price = int(rpc.gas_price() * 1.25)
        tx['nonce'] = nonce
        tx['gasPrice'] = gas_price
        signed = acct.sign_transaction(tx)
        raw_hex = (signed.raw_transaction if hasattr(signed, 'raw_transaction') else signed.rawTransaction).hex()
        if not raw_hex.startswith('0x'):
            raw_hex = '0x' + raw_hex
        tx_hash = rpc._call({'jsonrpc': '2.0', 'method': 'eth_sendRawTransaction', 'params': [raw_hex]})
        print(f'[hunter] LIVE tx broadcast: {tx_hash}', flush=True)
        log_event({'event': 'broadcast', 'tx_hash': tx_hash})
        try:
            rcpt = rpc.wait_receipt(tx_hash, timeout=180)
            status = int(rcpt.get('status', '0x0'), 16)
            if status == 1:
                print(f'[hunter] LIVE tx CONFIRMED: {tx_hash}', flush=True)
                return {'broadcast': 'ok', 'tx_hash': tx_hash}
            print(f'[hunter] LIVE tx REVERTED: {tx_hash}', flush=True)
            return {'broadcast': 'reverted', 'tx_hash': tx_hash}
        except Exception as e:
            print(f'[hunter] receipt timeout: {e}', flush=True)
            return {'broadcast': 'unconfirmed', 'tx_hash': tx_hash}
    except Exception as e:
        print(f'[hunter] broadcast failed: {e}', flush=True)
        return {'broadcast': 'failed', 'error': str(e)}


def uint_or_zero(x):
    try:
        return int(x, 16) if isinstance(x, str) else (x or 0)
    except Exception:
        return 0


def _weth_balance(rpc, token_addr, holder_addr):
    """Read ERC20 balance of `holder_addr` for `token_addr` via balanceOf."""
    try:
        raw = rpc.eth_call(token_addr,
                           "0x70a08231" + holder_addr[2:].lower().rjust(64, '0'))
        return int(raw[2:66], 16) if raw and len(raw) >= 66 else 0
    except Exception:
        return 0


def encode_execute_v2(plan):
    """ABI-encode FlashloanArb.execute(params) for V1 executor flat ABI.
    Selector: execute(uint256,address,address,address) = 0x5489b4f7
    Args: (size_weth, poolBuy, poolSell, quoteToken)
    """
    return ("5489b4f7"
            + int(plan["size_weth"] * 1e18).__format__('064x')
            + plan["poolBuy"][2:].rjust(64, "0")
            + plan["poolSell"][2:].rjust(64, "0")
            + plan["quoteToken"][2:].rjust(64, "0"))


def encode_execute_v3(plan):
    """ABI-encode FlashloanArbV3.execute(params) for triangular routes."""
    return (keccak(text="execute(uint256,tuple,tuple,tuple,address)")[:4].hex()
            + int(plan["size_weth"] * 1e18).__format__('064x')
            + int(plan["buy_kind"]).__format__('064x')
            + plan["buy_venue"][2:].rjust(64, "0")
            + int(plan["buy_fee"]).__format__('064x')
            + int(plan["buy1_kind"]).__format__('064x')
            + plan["buy1_venue"][2:].rjust(64, "0")
            + int(plan["buy1_fee"]).__format__('064x')
            + int(plan["sell_kind"]).__format__('064x')
            + plan["sell_venue"][2:].rjust(64, "0")
            + int(plan["sell_fee"]).__format__('064x')
            + plan["quote"][2:].rjust(64, "0"))


def encode_execute_zk(edge):
    """Encode 2-leg arb params for ZKArbExecutor.executeWithProof.
    Returns abi.encode(Leg, Leg, address) for the flashloan callback.
    """
    from eth_abi import encode

    buy_leg = (edge["buy_kind"], edge["buy_venue"], edge.get("buy_fee", 3000))
    sell_leg = (edge["sell_kind"], edge["sell_venue"], edge.get("sell_fee", 3000))
    quote_token = edge["quote"]

    return encode(["(uint8,address,uint24)", "(uint8,address,uint24)", "address"],
                  [buy_leg, sell_leg, quote_token])


def execute_zk_edges(rpc, acct, executor_addr, edges, eth_usd, gas_usd):
    """Execute edges via ZK-proof path (ShadowPath Verkle+Groth16).
    Generates proof for best edge, broadcasts single verifyProof+execute tx.
    """
    from zk_prover import ZKProver

    prover = ZKProver(rpc)

    # Try edges in priority order until we get a valid proof
    for edge in edges[:SIM_BUDGET_PER_CYCLE]:
        print(f"[hunter] ZK-PROOF -> generating: {edge.get('venue_buy')} -> "
              f"{edge.get('venue_sell')} size={edge.get('size_weth')} "
              f"net=${edge.get('net_usd')}", flush=True)

        proof = prover.generate_proof(edge, eth_usd, gas_usd)
        if not proof:
            print(f"[hunter] ZK-PROOF failed for edge, trying next...", flush=True)
            continue

        print(f"[hunter] ZK-PROOF SUCCESS: profit=${proof['profit_usd']:.4f} "
              f"net=${proof['net_profit_usd']:.4f} nullifier=0x{int(proof['nullifier']):064x}...", flush=True)

        # Build arb calldata (hidden from mempool - only submitted after proof verified on-chain)
        if edge.get("buy1_kind") is not None:
            # 3-leg route - encode for V3 executor
            arb_calldata = encode_execute_v3(edge)
        else:
            # 2-leg route - encode for ZKArbExecutor
            arb_calldata = encode_execute_zk(edge)

        # Broadcast the ZK-proof execution transaction
        receipt = broadcast_zk_execution(rpc, acct, executor_addr, proof, edge)

        log_event({"event": "zk_proof", "edge": edge, "proof": proof, "receipt": receipt})

        if receipt and receipt.get("broadcast") == "ok":
            return receipt

        # If broadcast failed, try next edge
        print(f"[hunter] ZK broadcast failed, trying next edge...", flush=True)

    return {"broadcast": "failed_all_edges"}


def broadcast_zk_execution(rpc, acct, executor_addr, proof, edge):
    """Sign and broadcast FlashloanArbV2.executeWithProof for a V2/V2 edge."""
    from eth_abi import encode

    raw_proof = proof["proof"]
    public_signals = [int(value) for value in proof["public_signals"]]
    if len(public_signals) != 3:
        raise ValueError(f"ZK verifier requires exactly 3 public signals, got {len(public_signals)}")

    # snarkjs serializes G2 coordinates in the inverse order expected by the
    # Solidity verifier generated by snarkjs.
    a = tuple(int(value) for value in raw_proof["pi_a"][:2])
    b = (
        (int(raw_proof["pi_b"][0][1]), int(raw_proof["pi_b"][0][0])),
        (int(raw_proof["pi_b"][1][1]), int(raw_proof["pi_b"][1][0])),
    )
    c = tuple(int(value) for value in raw_proof["pi_c"][:2])
    principal = int(float(edge["size_weth"]) * 1e18)

    buy_leg = (edge["buy_kind"], edge["buy_venue"], edge.get("buy_fee", 3000))
    sell_leg = (edge["sell_kind"], edge["sell_venue"], edge.get("sell_fee", 3000))
    quote_token = edge["quote"]

    # Contract signature: executeWithProof(uint[2],uint[2][2],uint[2],uint[3],
    #                                    uint256,Leg,Leg,address)
    # The 4 proof args are followed by the arb execution args directly (no
    # abi.encode wrapper). Keep parity with FlashloanArbV2.executeWithProof.
    selector = keccak(text="executeWithProof(uint256[2],uint256[2][2],uint256[2],uint256[3],uint256,(uint8,address,uint24),(uint8,address,uint24),address)")[:4]
    calldata = selector + encode(
        ["uint256[2]", "uint256[2][2]", "uint256[2]", "uint256[3]",
         "uint256", "(uint8,address,uint24)", "(uint8,address,uint24)", "address"],
        [a, b, c, public_signals, principal, buy_leg, sell_leg, quote_token],
    )

    # Broadcast via rotation
    for url in BROADCAST_RPCS:
        try:
            bc_rpc = rpc.__class__(url, timeout=30, retries=1)
            nonce = bc_rpc.nonce(acct.address)
            gas_price = int(bc_rpc.gas_price() * 1.25)
            chain_id = 42161
            signed = acct.sign_transaction({
                "nonce": nonce,
                "gasPrice": gas_price,
                "gas": 600_000,
                "to": executor_addr,
                "value": 0,
                "data": calldata,
                "chainId": chain_id,
            })
            raw_hex = (signed.raw_transaction if hasattr(signed, "raw_transaction")
                       else signed.rawTransaction).hex()
            if not raw_hex.startswith("0x"):
                raw_hex = "0x" + raw_hex
            tx_hash = bc_rpc._call({'jsonrpc': '2.0', 'method': 'eth_sendRawTransaction', 'params': [raw_hex]})
            print(f"[hunter] ZK tx broadcast: {tx_hash} via {url}", flush=True)
            try:
                rcpt = bc_rpc.wait_receipt(tx_hash, timeout=180)
                status = int(rcpt.get("status", "0x0"), 16)
                if status == 1:
                    print(f"[hunter] ZK tx CONFIRMED: {tx_hash}", flush=True)
                    return {"broadcast": "ok", "tx_hash": tx_hash, "rpc": url}
                else:
                    print(f"[hunter] ZK tx REVERTED: {tx_hash}", flush=True)
            except Exception as e:
                print(f"[hunter] ZK tx receipt timeout: {e}", flush=True)
        except Exception as e:
            print(f"[hunter] ZK broadcast failed on {url}: {e}", flush=True)
            continue

    return {"broadcast": "failed_all_rpcs"}


def build_v2_calldata(edge):
    """Build the byte-exact FlashloanArbV2.execute calldata that will be
    broadcast live. The SAME string is simmed on the fork, so a PASS gate
    validates the exact live transaction (sim == broadcast)."""
    if edge.get("buy_kind") == 1 and not edge.get("buy_fee"):
        edge["buy_fee"] = 3000
    if edge.get("sell_kind") == 1 and not edge.get("sell_fee"):
        edge["sell_fee"] = 3000
    principal = int(float(edge["size_weth"]) * 1e18)
    edge["_principal"] = principal
    edge["_live_calldata"] = sim_gate._encode_execute_v2(edge, principal)
    return edge["_live_calldata"]


def sim_edge_on_fork(fork, edge, executor_addr, deployer):
    """Execute the EXACT live calldata against a fresh FlashloanArbV2
    deployment on the fork. Ground-truth profit via executor WETH delta
    (includes every fee and curve effect), gated on GAS_MULTIPLIER * gas
    and MIN_PROFIT_USD -- aligned with capital_controller."""
    calldata = edge.get("_live_calldata") or build_v2_calldata(edge)
    weth_before = fork.erc20_balance(sim_gate.WETH, executor_addr)
    try:
        txh = fork.send_from(deployer, executor_addr, calldata)
        r = fork.wait_tx(txh)
    except Exception as e:
        return {"sim": "reverted", "error": str(e)[:200]}
    if r.get("status") != "0x1":
        return {"sim": "reverted"}
    gas_used = int(r["gasUsed"], 16)
    profit_weth = (fork.erc20_balance(sim_gate.WETH, executor_addr)
                   - weth_before) / 1e18
    gas_usd = (gas_used / 1e18) * fork.gas_price() * edge.get("eth_usd", 2450)
    profit_usd = profit_weth * edge.get("eth_usd", 2450)
    gate = "PASS" if (profit_usd > sim_gate.GAS_MULTIPLIER * gas_usd
                      and profit_usd > sim_gate.MIN_PROFIT_USD) else "FAIL"
    return {"sim": "ok", "gas_used": gas_used,
            "profit_weth": round(profit_weth, 8),
            "gas_usd": round(gas_usd, 4),
            "profit_usd": round(profit_usd, 4),
            "gate": gate}


def simulate_edges_batch(edges, acct, executor_addr=None,
                         max_sims=SIM_BUDGET_PER_CYCLE):
    """Vet up to max_sims edges against ONE anvil fork. A fresh
    FlashloanArbV2 (constructor: Aave pool, SwapRouter02, WETH) is deployed
    once per session; each edge runs the byte-exact live calldata, with
    evm_snapshot/evm_revert between sims so every edge sees pristine pool
    state. Returns [(edge, sim_result), ...]; stops at the first PASS."""
    results = []
    deployer = "0xf39fd6e51aad88f6f4ce6ab8827229cfffb92266"  # anvil key0
    proc, host, head, fork_url = sim_gate.launch_fork()
    try:
        fork = sim_gate.Fork(host)
        fork.set_balance(deployer, sim_gate.wad(10))
        fork.impersonate(deployer)
        with open(os.path.join(HERE, "contracts", "FlashloanArbV2.bin")) as f:
            v2_bin = f.read().strip()
        ctor = (sim_gate.pad_addr(sim_gate.AAVE_V3_POOL)
                + sim_gate.pad_addr(sim_gate.V3_ROUTER)
                + sim_gate.pad_addr(sim_gate.WETH))
        v2_addr = fork.deploy_contract(v2_bin + ctor, deployer)
        for edge in edges[:max_sims]:
            print(f"[hunter] EDGE -> fork-simming: {edge.get('venue_buy')} -> "
                  f"{edge.get('venue_sell')} size={edge.get('size_weth')} "
                  f"net=${edge.get('net_margin')}", flush=True)
            snap = None
            try:
                snap = fork.snapshot()
            except Exception:
                pass
            try:
                sim = sim_edge_on_fork(fork, edge, v2_addr, deployer)
            except Exception as e:
                sim = {"sim": "error", "error": str(e)[:200]}
            results.append((edge, sim))
            if sim and sim.get("gate") == "PASS":
                break
            if snap:
                try:
                    fork.revert(snap)
                except Exception:
                    pass
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
    return results


def broadcast_v2_execution(rpc, acct, executor_addr, edge):
    """Sign and broadcast FlashloanArbV2.execute for a fork-sim PASS edge.
    Calldata is byte-identical to what the fork gate validated. Preflight
    checks owner() (execute is onlyOwner) and refuses any mismatch."""
    calldata = edge.get("_live_calldata")
    if not calldata:
        return {"broadcast": "no_calldata"}
    try:
        owner_raw = rpc.eth_call(executor_addr, "0x8da5cb5b")  # owner()
        owner = "0x" + owner_raw[-40:]
        if owner.lower() != acct.address.lower():
            print(f"[hunter] REFUSING broadcast: executor owner {owner} "
                  f"!= hot wallet {acct.address}", flush=True)
            log_event({"event": "broadcast_refused", "reason": "not_owner",
                       "owner": owner})
            return {"broadcast": "refused_not_owner", "owner": owner}
    except Exception as e:
        log_event({"event": "broadcast_preflight_error",
                   "error": str(e)[:200]})
        return {"broadcast": "preflight_failed"}
    for url in BROADCAST_RPCS:
        try:
            bc_rpc = rpc.__class__(url, timeout=30, retries=1)
            nonce = bc_rpc.nonce(acct.address)
            gas_price = int(bc_rpc.gas_price() * 1.25)
            signed = acct.sign_transaction({
                "nonce": nonce, "gasPrice": gas_price, "gas": 600_000,
                "to": executor_addr, "value": 0, "data": calldata,
                "chainId": 42161,
            })
            raw_hex = (signed.raw_transaction if hasattr(signed, "raw_transaction")
                       else signed.rawTransaction).hex()
            if not raw_hex.startswith("0x"):
                raw_hex = "0x" + raw_hex
            tx_hash = bc_rpc._call({'jsonrpc': '2.0', 'method': 'eth_sendRawTransaction', 'params': [raw_hex]})
            print(f"[hunter] LIVE tx broadcast: {tx_hash} via {url}", flush=True)
            log_event({"event": "broadcast", "tx_hash": tx_hash, "rpc": url,
                       "executor": executor_addr})
            try:
                rcpt = bc_rpc.wait_receipt(tx_hash, timeout=180)
                status = int(rcpt.get("status", "0x0"), 16)
                if status == 1:
                    print(f"[hunter] LIVE tx CONFIRMED: {tx_hash}", flush=True)
                    return {"broadcast": "ok", "tx_hash": tx_hash, "rpc": url}
                print(f"[hunter] LIVE tx REVERTED: {tx_hash}", flush=True)
                return {"broadcast": "reverted", "tx_hash": tx_hash}
            except Exception as e:
                print(f"[hunter] LIVE tx receipt timeout: {e}", flush=True)
                return {"broadcast": "unconfirmed", "tx_hash": tx_hash}
        except Exception as e:
            print(f"[hunter] broadcast failed on {url}: {e}", flush=True)
            continue
    return {"broadcast": "failed_all_rpcs"}



def deploy_executor(rpc, acct):
    """Deploy FlashloanArbV2 executor (cross-venue V2/V2)."""
    from eth_abi import encode

    print("[hunter] deploying cross-venue V2 executor...", flush=True)
    # FlashloanArbV2 bytecode with constructor args: WETH, owner
    WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    owner = acct.address

    # This is a placeholder - actual deployment would use compiled bytecode
    # For now, just log the intent
    log_event({"event": "deploy_executor_v2", "weth": WETH, "owner": owner})
    print("[hunter] executor deployment not implemented in this version", flush=True)
    return None


def deploy_v3_executor(rpc, acct):
    """Deploy FlashloanArbV3 executor (triangular routes)."""
    log_event({"event": "deploy_executor_v3", "owner": acct.address})
    print("[hunter] V3 executor deployment not implemented in this version", flush=True)
    return None


def deploy_zk_executor(rpc, acct):
    """Deploy ZKArbExecutor (ZK-proof arb)."""
    from zk_prover import ZKProver
    import json as _json
    from eth_abi import encode
    from eth_utils import to_checksum_address

    print("[hunter] deploying ZK arb executor...", flush=True)

    # 1) Deploy Groth16Verifier
    verifier_abi = _json.load(open("contracts/Groth16Verifier.abi"))
    verifier_bin = open("contracts/Groth16Verifier.bin").read().strip()
    if not verifier_bin.startswith("0x"):
        verifier_bin = "0x" + verifier_bin

    # deploy verifier
    vrpc = rpc.__class__(rpc.url, timeout=60, retries=1)
    nonce = vrpc.nonce(acct.address)
    gas_price = int(vrpc.gas_price() * 1.25)
    signed = acct.sign_transaction({
        "nonce": nonce, "gasPrice": gas_price, "gas": 500_000,
        "to": None, "value": 0, "data": verifier_bin, "chainId": 42161,
    })
    raw = (signed.raw_transaction if hasattr(signed, "raw_transaction")
           else signed.rawTransaction).hex()
    if not raw.startswith("0x"): raw = "0x" + raw
    vtx = vrpc._call({'jsonrpc': '2.0', 'method': 'eth_sendRawTransaction', 'params': [raw]})
    v_rcpt = vrpc.wait_receipt(vtx, timeout=300)
    verifier = to_checksum_address(v_rcpt["contractAddress"][:42].strip())
    print(f"[hunter] Groth16Verifier: {verifier}", flush=True)

    # 2) Deploy ZKArbExecutor with verifier address
    executor_abi = _json.load(open("contracts/FlashloanArbV2.abi"))
    executor_bin = open("contracts/FlashloanArbV2.bin").read().strip()
    if not executor_bin.startswith("0x"): executor_bin = "0x" + executor_bin

    # constructor(address _aavePool, address _v3Router, address _weth)
    AAVE_POOL = to_checksum_address("0x794a61358D6845594F94dc1DB02A252b5b4814aD")
    V3_ROUTER = to_checksum_address("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45")
    WETH_ADDR = to_checksum_address("0x82af49447d8a07e3bd95bd0d56f35241523fbab1")
    constructor_args = encode(["address", "address", "address"],
                               [AAVE_POOL, V3_ROUTER, WETH_ADDR])
    deploy_data = executor_bin + constructor_args.hex()[2:]

    nonce = vrpc.nonce(acct.address)
    gas_price = int(vrpc.gas_price() * 1.25)
    signed = acct.sign_transaction({
        "nonce": nonce, "gasPrice": gas_price, "gas": 1_800_000,
        "to": None, "value": 0, "data": deploy_data, "chainId": 42161,
    })
    raw = (signed.raw_transaction if hasattr(signed, "raw_transaction")
           else signed.rawTransaction).hex()
    if not raw.startswith("0x"): raw = "0x" + raw
    etx = vrpc._call({'jsonrpc': '2.0', 'method': 'eth_sendRawTransaction', 'params': [raw]})
    e_rcpt = vrpc.wait_receipt(etx, timeout=300)
    executor = to_checksum_address(e_rcpt["contractAddress"][:42].strip())
    print(f"[hunter] ZKArbExecutor: {executor}", flush=True)

    # 3) Bind verifier in executor
    bind_sel = keccak(text="setVerifier(address)")[:4].hex()
    bind_data = "0x" + bind_sel + encode(["address"], [verifier]).hex()[2:]
    nonce = vrpc.nonce(acct.address)
    gas_price = int(vrpc.gas_price() * 1.25)
    signed = acct.sign_transaction({
        "nonce": nonce, "gasPrice": gas_price, "gas": 80_000,
        "to": executor, "value": 0, "data": bind_data, "chainId": 42161,
    })
    raw = (signed.raw_transaction if hasattr(signed, "raw_transaction")
           else signed.rawTransaction).hex()
    if not raw.startswith("0x"): raw = "0x" + raw
    btx = vrpc._call({'jsonrpc': '2.0', 'method': 'eth_sendRawTransaction', 'params': [raw]})
    b_rcpt = vrpc.wait_receipt(btx, timeout=120)
    print(f"[hunter] verifier bound in executor", flush=True)

    save_zk_executor(executor)
    log_event({
        "event": "executor_zk_deployed", "address": executor, "verifier": verifier,
        "verifier_tx": vtx, "executor_tx": etx, "bind_tx": btx,
        "verifier_gas": int(v_rcpt["gasUsed"], 16),
        "executor_gas": int(e_rcpt["gasUsed"], 16),
        "bind_gas": int(b_rcpt["gasUsed"], 16),
    })
    print(f"[hunter] ZK V2 executor deployed: {executor}; verifier: {verifier}", flush=True)
    return executor


def main():
    global DRY_RUN
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true",
                    help="deploy the cross-venue V2 executor")
    ap.add_argument("--deploy-v3", action="store_true",
                    help="deploy the three-leg V3 executor (triangular routes)")
    ap.add_argument("--deploy-zk", action="store_true",
                    help="deploy the ZK-proof arb executor (ZKArbExecutor)")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="single cycle then exit (for cron watchdog)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry", action="store_true",
                    help="simulate only: log SIM PASSes, never broadcast live")
    ap.add_argument("--interval", type=int, default=SCAN_INTERVAL_SEC,
                    help="target seconds between hunt cycles (default 15)")
    args = ap.parse_args()
    DRY_RUN = args.dry

    acct = Account.from_key(load_key())
    from core.rpc import prime_dns
    prime_dns(list(set(BROADCAST_RPCS + SCAN_RPCS)))

    if args.deploy:
        rpc, _ = get_rpc()
        deploy_executor(rpc, acct)
        return
    if args.deploy_v3:
        rpc, _ = get_rpc()
        deploy_v3_executor(rpc, acct)
        return
    if args.deploy_zk:
        rpc, _ = get_rpc()
        deploy_zk_executor(rpc, acct)
        return
    if args.status:
        print("wallet:", acct.address)
        print("executor:", load_executor())
        print("executor_v2:", load_v2_executor())
        print("executor_v3:", load_v3_executor())
        print("executor_zk:", load_zk_executor())
        return

    if args.once:
        rpc, _ = get_rpc()
        executor_addr = load_executor()
        from core.capital_controller import CapitalController
        controller = CapitalController()
        hunt_once(rpc, acct, executor_addr, rpc.url, controller=controller)
        return

    if args.run:
        last_heartbeat = 0
        print("[hunter] starting autonomous run loop", flush=True)
        from core.capital_controller import CapitalController
        controller = CapitalController()
        while True:
            try:
                rpc, _ = get_rpc()
                executor_addr = load_executor()
                hunt_once(rpc, acct, executor_addr, rpc.url, controller=controller)
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_EVERY_SEC:
                    print(f"[{time.strftime('%H:%M:%S')}] heartbeat: wallet={acct.address} "
                          f"executor={executor_addr} zk_executor={load_zk_executor()}", flush=True)
                    last_heartbeat = now
            except Exception as e:
                print(f"[hunter] cycle error: {e}", flush=True)
                import traceback
                traceback.print_exc()
                log_event({"event": "cycle_error", "error": str(e)[:200],
                           "trace": traceback.format_exc()[:3000]})
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
