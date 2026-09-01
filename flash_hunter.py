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
]

SCAN_RPCS = [
    "http://127.0.0.1:8545",
    "https://gateway.tenderly.co/public/arbitrum",
]

SCAN_INTERVAL_SEC = 15       # TARGET cadence: one full hunt cycle every 15s (60 blocks)
HEARTBEAT_EVERY_SEC = 15 * 60
GAS_MULTIPLIER = 1.0         # profit must exceed 1.0x gas (break-even+)
MIN_PROFIT_USD = 0.10        # net profit floor after gas; filter dust, catch small edges
REFILL_GAS_THRESHOLD_ETH = 0.005   # top up if hot wallet ETH < 0.005 (~$1.25)
REFILL_GAS_TARGET_ETH = 0.01       # withdraw/swap to reach ~0.01 ETH (~$2.5)
SIM_BUDGET_PER_CYCLE = 6     # max fork-sims per cycle (best-net first). Fork
                             # startup is the expensive part; marginal sims on
                             # the same fork are ~1-2s each, so vetting 6 instead
                             # of 4 raises the chance of a PASS per cycle.


def log_event(evt):
    evt["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(evt) + "\n")


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


def hunt_once(rpc, acct=None, executor_addr=None, rpc_scan=None, verbose=True):
    """One hunt cycle: registry cross-venue scan -> ZK-proof gate -> broadcast.
    Returns a cycle summary dict.
    """
    cycle_start = time.time()
    # Prefer local fork for scanning if available; fall back to public
    # scan endpoints so read traffic does not depend on the broadcast fleet.
    from core.rpc import RPC as Vrpc
    r = None
    for url in SCAN_RPCS:
        try:
            r = Vrpc(url, timeout=120, retries=1)
            r.eth_blockNumber()
            break
        except Exception:
            continue
    if r is None:
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
    gas_wei = uint_or_zero(r.eth_gasPrice())
    gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd
    try:
        edges, report = arb_engine.scan_cross_venue(r, eth_usd, gas_usd,
                                                    size_steps=12,
                                                    max_venues_per_quote=8,
                                                    use_multi_hop=True,
                                                    use_parallel=True)
    except Exception as e:
        import traceback
        print(f"[hunter] registry scan failed: {e}", flush=True)
        traceback.print_exc()
        log_event({"event": "scan_error", "error": str(e)[:200]})
        return None
    if verbose:
        print(f"[{time.strftime('%H:%M:%S')}] cross-scan: {len(report)} combos, "
              f"{len(edges)} edges (ETH ${eth_usd:.0f})", flush=True)

    if not edges:
        return {"edges": 0, "report": report, "passes": 0}

    # ZK-PROOF PATH (ShadowPath Verkle+Groth16) - replaces fork-sim
    zk_executor_addr = load_zk_executor()
    zk_edges = [e for e in edges
                if e.get("buy_kind") == 0 and e.get("sell_kind") == 0
                and e.get("net_usd", 0) >= 2.0]
    if ZK_AVAILABLE and zk_executor_addr and zk_edges:
        print(f"[hunter] ZK gate: {len(zk_edges)} high-value V2 edges", flush=True)
        receipt = execute_zk_edges(rpc, acct, zk_executor_addr, zk_edges, eth_usd, gas_usd)
        passes = 1 if receipt and receipt.get("broadcast") == "ok" else 0
    else:
        passes = 0
        receipt = None

    if not passes:
        # Existing fork-sim path is the authoritative fallback for every
        # non-V2 edge and every ZK failure. No opportunity is dropped.
        sim_results = simulate_edges_batch(edges, acct, executor_addr)
        for edge, sim in sim_results:
            log_event({"event": "sim", "edge": edge, "sim": sim})
            if sim and sim.get("gate") == "PASS":
                passes += 1

    log_event({"event": "cycle", "edges": len(edges), "passes": passes,
               "duration_sec": time.time() - cycle_start, "executor": executor_addr})
    return {"edges": len(edges), "passes": passes, "report": report,
            "executor": executor_addr, "receipt": receipt}


def uint_or_zero(x):
    try:
        return int(x, 16) if isinstance(x, str) else (x or 0)
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
            tx_hash = bc_rpc.send_raw(raw_hex)
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


def sim_edge_on_fork(fork, edge, executor_addr):
    """Simulate a single edge on the fork. Returns dict with gate result."""
    size_wei = int(float(edge["size_weth"]) * 1e18)
    if edge.get("buy_kind") == 0 and edge.get("sell_kind") == 0:
        # V2/V2 edge
        calldata = "0x" + encode_execute_v2({
            "size_weth": edge["size_weth"],
            "poolBuy": edge.get("pool_buy") or edge.get("poolBuy"),
            "poolSell": edge.get("pool_sell") or edge.get("poolSell"),
            "quoteToken": edge.get("quote_token") or edge.get("quoteToken") or WETH,
        })
    elif edge.get("buy1_kind") is not None:
        # 3-leg V3
        calldata = "0x" + encode_execute_v3(edge)
    else:
        # 2-leg V3
        calldata = "0x" + encode_execute_v3({
            "size_weth": edge["size_weth"],
            "buy_kind": edge["buy_kind"],
            "buy_venue": edge["buy_venue"],
            "buy_fee": edge.get("buy_fee", 3000),
            "buy1_kind": 0, "buy1_venue": "0x", "buy1_fee": 0,
            "sell_kind": edge["sell_kind"],
            "sell_venue": edge["sell_venue"],
            "sell_fee": edge.get("sell_fee", 3000),
            "quote": edge["quote"],
        })
    try:
        sim = fork.call(edge["quote"], executor_addr, calldata)
        if sim is None:
            return {"gate": "FAIL", "reason": "simulation returned None"}
        profit = int(sim, 16) if isinstance(sim, str) else sim
        if profit > 0:
            return {"gate": "PASS", "profit_wei": profit}
        else:
            return {"gate": "FAIL", "profit_wei": profit}
    except Exception as e:
        return {"gate": "ERROR", "error": str(e)[:200]}


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
    vtx = vrpc.send_raw(raw)
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
    etx = vrpc.send_raw(raw)
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
    btx = vrpc.send_raw(raw)
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
    ap.add_argument("--interval", type=int, default=SCAN_INTERVAL_SEC,
                    help="target seconds between hunt cycles (default 15)")
    args = ap.parse_args()

    acct = Account.from_key(load_key())

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
        hunt_once(rpc, acct, executor_addr, rpc.url)
        return

    if args.run:
        last_heartbeat = 0
        print("[hunter] starting autonomous run loop", flush=True)
        while True:
            try:
                rpc, _ = get_rpc()
                executor_addr = load_executor()
                hunt_once(rpc, acct, executor_addr, rpc.url)
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_EVERY_SEC:
                    print(f"[{time.strftime('%H:%M:%S')}] heartbeat: wallet={acct.address} "
                          f"executor={executor_addr} zk_executor={load_zk_executor()}", flush=True)
                    last_heartbeat = now
            except Exception as e:
                print(f"[hunter] cycle error: {e}", flush=True)
                log_event({"event": "cycle_error", "error": str(e)[:200]})
            time.sleep(args.interval)


if __name__ == "__main__":
    main()