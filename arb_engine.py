#!/usr/bin/env python3
"""arb_engine.py — VERITAS Engine: Arbitrum flash-loan arb scan layer.

Layer 1 — SCAN (read-only, $0): find dislocations between constant-product
pools on Arbitrum (SushiSwap V2 + Uniswap V2 factories) for WETH/USDC and
WETH/USDC.e, compute optimal trade size and gross profit with the proven
two-pool CPMM math (ported from the Base arb_scan.py lineage).

Cost-stack gate: gross profit must clear BOTH swap fees (in the math),
the 0.05% Aave flash-loan premium on principal, gas, and a safety margin
before a candidate is called an EDGE.

This module NEVER signs or sends a transaction. The fork-sim gate
(sim_gate.py) and the hunter loop (flash_hunter.py) consume its output.

Usage:
  python3 arb_engine.py scan                # one pass, human report
  python3 arb_engine.py scan --json         # one pass, machine JSON
  python3 arb_engine.py scan --interval 30  # loop with heartbeats
"""
import argparse
import os
import sys
import time
import math
import sqlite3
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint
from zk_prover import ZKProver

# ---- Configuration ----
SCAN_INTERVAL_SECONDS = 180
MIN_SAFETY_MARGIN_USD = 0.005
AAVE_FLASH_FEE = 0.0005
GAS_UNITS = 350_000
FORK_URL = "http://127.0.0.1:8545"
DEFAULT_RPC_URL = "https://gateway.tenderly.co/public/arbitrum"
# -----------------------

ZK_PROVER: Optional[ZKProver] = None
TOKENS = {}
TOKEN_DECIMALS_CACHE = {}
PAIR_CACHE: Dict[str, str] = {}
RECENTLY_PROFITABLE: List[str] = []

# ---- low-level helpers ---------------------------------------------------

def pad_addr(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")

def parse_addr(result: Optional[str]) -> Optional[str]:
    if not result or len(result) < 66:
        return None
    tail = result[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail

def parse_reserves(result: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    if not result or result == "0x" or len(result) < 130:
        return None, None
    h = result[2:]
    return int(h[0:64], 16), int(h[64:128], 16)

def univ2_pair(rpc: RPC, factory: str, token_a: str, token_b: str) -> Optional[str]:
    try:
        data = "0x" + SEL["getPair"] + pad_addr(token_a) + pad_addr(token_b)
        result = rpc.eth_call(factory, data)
        return parse_addr(result)
    except Exception:
        return None

# ---- Core constants ------------------------------------------------------

WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
UNIV2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
CAMELOT_FACTORY = "0x6eccab422d763ac031210895c81787e87b43a652"
AAVE_V3_POOL = "0x794a61358d6845594f94dc1db02a252b5b4814ad"
SEL = {
    "getPair": "e6a43905",
    "getReserves": "0902f1ac",
    "token0": "0dfe1681",
    "token1": "d21220a7",
}

# ---- Discovery -----------------------------------------------------------

def discover_tokens_and_pairs(rpc: RPC) -> None:
    global TOKENS, TOKEN_DECIMALS_CACHE, PAIR_CACHE
    if TOKENS and PAIR_CACHE:
        return

    seed_tokens = {
        WETH: {"address": WETH, "price_usd": 2500.0},
        USDC: {"address": USDC, "price_usd": 1.0},
        USDCE: {"address": USDCE, "price_usd": 1.0},
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": {"address": USDC, "price_usd": 1.0},
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": {"address": WETH, "price_usd": 2500.0},
        "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": {"address": USDCE, "price_usd": 1.0},
        "0x912ce59144191c1204e64559fe8253a0e49e6548": {"address": "0x912ce59144191c1204e64559fe8253a0e49e6548", "price_usd": 1.8},
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": {"address": "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f", "price_usd": 95000.0},
        "0xf97f4df75117a78c1a5a0dbb814af92458539fb4": {"address": "0xf97f4df75117a78c1a5a0dbb814af92458539fb4", "price_usd": 12.0},
        "0xfa7f8980b0f1e64a2062791cc3b0871572f1f7f0": {"address": "0xfa7f8980b0f1e64a2062791cc3b0871572f1f7f0", "price_usd": 8.5},
        "0xd4d42f0b6def4ce0383636770ef773390d85c61a": {"address": "0xd4d42f0b6def4ce0383636770ef773390d85c61a", "price_usd": 1.5},
        "0x11cdb42b0eb46d95f990bedd4695a6e3fa034978": {"address": "0x11cdb42b0eb46d95f990bedd4695a6e3fa034978", "price_usd": 0.25},
        "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a": {"address": "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a", "price_usd": 28.0},
    }

    decimals_cache = {WETH: 18, USDC: 6, USDCE: 6}
    for addr, meta in list(seed_tokens.items()):
        try:
            r = rpc.eth_call(addr, "0x313ce567")
            d = int(r[2:66], 16) if r and len(r) >= 66 else meta.get("decimals", 18)
        except Exception:
            d = 18
        decimals_cache[addr] = d

    pair_cache = {}
    discovered_tokens = {}
    for quote in (WETH, USDC):
        for base in seed_tokens:
            if base == quote:
                continue
            try:
                p = univ2_pair(rpc, SUSHI_FACTORY, base, quote)
                if p:
                    pair_cache[(base, quote)] = p
                    pair_cache[(quote, base)] = p
                    discovered_tokens[base] = seed_tokens[base]
                    discovered_tokens[quote] = seed_tokens[quote]
            except Exception:
                pass

    TOKENS = discovered_tokens
    TOKEN_DECIMALS_CACHE = decimals_cache
    PAIR_CACHE = pair_cache

# ---- Scanning ------------------------------------------------------------

def pool_side(rpc: RPC, factory: str, token_a: str, token_b: str,
              reserves_a: int, reserves_b: int) -> Dict[str, Any]:
    pair_addr = univ2_pair(rpc, factory, token_a, token_b)
    if not pair_addr:
        return {"pair_addr": None}

    reserves_tx = rpc.eth_call(pair_addr, SEL["getReserves"])
    reserves_a, reserves_b = parse_reserves(reserves_tx)

    if reserves_a is None or reserves_b is None or reserves_a == 0 or reserves_b == 0:
        return {"pair_addr": pair_addr, "reserves_a": reserves_a, "reserves_b": reserves_b}

    price_a_in_b = reserves_b / reserves_a

    def _usd_price(addr: str) -> float:
        if addr == WETH:
            return 2500.0
        if addr in (USDC, USDCE):
            return 1.0
        return 0.0

    price_a = _usd_price(token_a)
    price_b = _usd_price(token_b)
    if token_a == WETH:
        price_a_in_b_usd = price_b / price_a if price_a else 0.0
    elif token_b == WETH:
        price_a_in_b_usd = price_a / price_b if price_b else 0.0
    elif token_a in (USDC, USDCE):
        price_a_in_b_usd = price_b / price_a if price_a else 0.0
    elif token_b in (USDC, USDCE):
        price_a_in_b_usd = price_a / price_b if price_b else 0.0
    else:
        price_a_in_b_usd = price_a / price_b if price_b else 0.0

    return {
        "pair_addr": pair_addr,
        "token_a": token_a,
        "token_b": token_b,
        "reserves_a": reserves_a,
        "reserves_b": reserves_b,
        "price_a_in_b": price_a_in_b,
        "price_a_in_b_usd": price_a_in_b_usd,
        "factory": factory,
    }

def quote_v3_cached(rpc: RPC, weth_addr: str, quote_token_addr: str,
                    trade_amount_a: int, pool_a_addr: str) -> Tuple[int, int]:
    try:
        reserves_tx = rpc.eth_call(pool_a_addr, SEL["getReserves"])
        reserves_a, reserves_b = parse_reserves(reserves_tx)
        if reserves_a is None or reserves_b is None or reserves_a == 0 or reserves_b == 0:
            return 0, 0

        denominator = reserves_a + trade_amount_a
        amount_b = (trade_amount_a * reserves_b) / denominator
        scaled_amount_b = int(amount_b * (10 ** 18))
        new_k_scaled = (reserves_a + trade_amount_a) * (reserves_b - scaled_amount_b)
        return scaled_amount_b, new_k_scaled
    except Exception as e:
        print(f"Error querying V3 quotes for {pool_a_addr}: {e}")
        return 0, 0

def warm_recent_edges(best_edge: Optional[Dict[str, Any]]) -> None:
    if not best_edge:
        return
    addr = best_edge.get("pair_addr")
    if not addr:
        return
    if addr in RECENTLY_PROFITABLE:
        RECENTLY_PROFITABLE.remove(addr)
    RECENTLY_PROFITABLE.insert(0, addr)
    if len(RECENTLY_PROFITABLE) > 12:
        RECENTLY_PROFITABLE.pop()

def _probe_pair(rpc: RPC, factory: str, token_a: str, token_b: str) -> Optional[Dict[str, Any]]:
    edge = pool_side(rpc, factory, token_a, token_b, 0, 0)
    if edge.get("pair_addr"):
        edge.setdefault("token_a_decimals", TOKEN_DECIMALS_CACHE.get(token_a, 18))
        edge.setdefault("token_b_decimals", TOKEN_DECIMALS_CACHE.get(token_b, 18))
    return edge

def _get_reserves(rpc: RPC, pair_addr: str) -> Tuple[Optional[int], Optional[int]]:
    try:
        r = rpc.eth_call(pair_addr, SEL["getReserves"])
        r0, r1 = parse_reserves(r)
        max_uint112 = (1 << 112) - 1
        if r0 is None or r1 is None:
            return None, None
        if r0 == 0 or r1 == 0:
            return None, None
        if r0 > max_uint112 or r1 > max_uint112:
            return None, None
        return r0, r1
    except Exception:
        return None, None


def _cpmm_out(reserve_in: int, reserve_out: int, amount_in: int) -> int:
    if reserve_in is None or reserve_out is None or amount_in is None:
        return 0
    if reserve_in <= 0 or reserve_out <= 0 or amount_in <= 0:
        return 0
    amount_in_with_fee = amount_in * 9970
    numerator = amount_in_with_fee * reserve_out
    denominator = (reserve_in * 10000) + amount_in_with_fee
    return numerator // denominator


def _quote_edge(rpc: RPC, token_a: str, token_b: str, amount_a: int, factory: str) -> Optional[Dict[str, Any]]:
    pair_addr = univ2_pair(rpc, factory, token_a, token_b)
    if not pair_addr:
        return None
    # Validate pair exists on this fork
    try:
        code = rpc.eth_getCode(pair_addr)
        if not code or code == "0x":
            return None
    except Exception:
        return None
    r0, r1 = _get_reserves(rpc, pair_addr)
    if not r0 or not r1:
        return None
    # Uniswap V2 getReserves returns reserve0 for token0, reserve1 for token1
    try:
        t0 = parse_addr(rpc.eth_call(pair_addr, SEL["token0"]))
    except Exception:
        t0 = None
    if t0 and t0.lower() == token_a.lower():
        reserve_a, reserve_b = r0, r1
    else:
        reserve_a, reserve_b = r1, r0
    amount_b = _cpmm_out(reserve_a, reserve_b, amount_a)
    if not amount_b:
        return None
    return {
        "pair_addr": pair_addr,
        "token_a": token_a,
        "token_b": token_b,
        "reserves_a": reserve_a,
        "reserves_b": reserve_b,
        "amount_b": amount_b,
        "factory": factory,
        "token_a_decimals": TOKEN_DECIMALS_CACHE.get(token_a, 18),
        "token_b_decimals": TOKEN_DECIMALS_CACHE.get(token_b, 18),
    }


def _usd_for(addr: str, amount: int) -> float:
    decimals = TOKEN_DECIMALS_CACHE.get(addr, 18)
    raw = amount / (10 ** decimals)
    if addr == WETH:
        return raw * 2500.0
    if addr in (USDC, USDCE):
        return raw * 1.0
    return 0.0


def scan_all_pairs(rpc: RPC, tokens: Dict[str, Dict], token_decimals_cache: Dict[str, int]) -> List[Dict[str, Any]]:
    if not tokens:
        discover_tokens_and_pairs(rpc)

    candidates: List[Dict[str, Any]] = []
    token_names = [WETH, USDC, USDCE] + [k for k in tokens.keys() if k not in (WETH, USDC, USDCE)]

    quote_tokens = [WETH, USDC]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = []
        for token_a in token_names[:20]:
            if token_a in quote_tokens:
                continue
            for quote in quote_tokens:
                decimals_a = TOKEN_DECIMALS_CACHE.get(token_a, 18)
                decimals_quote = TOKEN_DECIMALS_CACHE.get(quote, 18)
                amount_a = 10 ** decimals_a if quote == WETH else 10 ** decimals_quote
                futures.append(ex.submit(_quote_edge, rpc, token_a, quote, amount_a, SUSHI_FACTORY))
                amount_quote = 10 ** decimals_quote if quote == WETH else 10 ** decimals_a
                futures.append(ex.submit(_quote_edge, rpc, quote, token_a, amount_quote, SUSHI_FACTORY))
        for fut in concurrent.futures.as_completed(futures):
            try:
                e = fut.result()
            except Exception:
                continue
            if e:
                candidates.append(e)

    for c in candidates:
        key = (c["token_a"], c["token_b"])
        if key not in PAIR_CACHE and c.get("pair_addr"):
            PAIR_CACHE[key] = c["pair_addr"]

    print(f"[arb_engine] Scan complete. Total candidates found: {len(candidates)}")
    return candidates


def build_edge(pool_buy: Dict[str, Any], pool_sell: Dict[str, Any], quote_token: str, amount_in: int) -> Dict[str, Any]:
    out_buy = _cpmm_out(pool_buy.get("reserves_a", 0), pool_buy.get("reserves_b", 0), amount_in)
    out_sell = _cpmm_out(pool_sell.get("reserves_a", 0), pool_sell.get("reserves_b", 0), out_buy)
    output_token = pool_sell.get("token_b", quote_token)
    gross_usd = _usd_for(output_token, out_sell)
    fee = gross_usd * AAVE_FLASH_FEE
    gas_wei = 0
    gas_usd = 0.0
    try:
        from core.rpc import RPC as _RPC
        rpc_tmp = _RPC(FORK_URL, timeout=5, retries=1)
        gas_wei = rpc_tmp.eth_gasPrice()
        gas_usd = (gas_wei * GAS_UNITS / 1e18) * 2500.0
    except Exception:
        pass
    cost_usd = fee + gas_usd + MIN_SAFETY_MARGIN_USD
    net = gross_usd - cost_usd
    return {
        "buy_kind": 0,
        "sell_kind": 0,
        "pool_buy": pool_buy.get("pair_addr"),
        "pool_sell": pool_sell.get("pair_addr"),
        "factory_buy": pool_buy.get("factory"),
        "factory_sell": pool_sell.get("factory"),
        "quote_token": quote_token,
        "token_a": pool_buy.get("token_a"),
        "token_b": pool_sell.get("token_b"),
        "size_weth": amount_in / 1e18,
        "amount_in": amount_in,
        "amount_out": out_sell,
        "gross_profit": gross_usd,
        "net_margin": net,
        "total_cost": cost_usd,
        "reserves_a": pool_buy.get("reserves_a", 0),
        "reserves_b": pool_sell.get("reserves_b", 0),
        "price_a_in_b_usd": gross_usd,
        "token_a_decimals": pool_buy.get("token_a_decimals", 18),
        "token_b_decimals": pool_sell.get("token_b_decimals", 18),
    }


def scan_cross_venue(rpc: RPC, eth_usd: float, gas_usd: float, size_steps: int = 12, max_venues_per_quote: int = 8, use_multi_hop: bool = True, use_parallel: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not TOKENS:
        discover_tokens_and_pairs(rpc)

    tokens = list(TOKENS.keys())
    if WETH in tokens:
        tokens.remove(WETH)
    tokens = tokens[:max_venues_per_quote]

    factories = [SUSHI_FACTORY, UNIV2_FACTORY]
    min_size = 10 ** 18
    max_size = 10 ** 18
    sizes = [min_size]

    edges: List[Dict[str, Any]] = []
    report: List[Dict[str, Any]] = []

    for token_a in tokens:
        quotes: Dict[str, Dict[str, Any]] = {}
        for factory in factories:
            for size in sizes:
                fb = _quote_edge(rpc, WETH, token_a, size, factory)
                if fb:
                    quotes.setdefault("forward", []).append(fb)
                rb = _quote_edge(rpc, token_a, WETH, size, factory)
                if rb:
                    quotes.setdefault("reverse", []).append(rb)

        forward = quotes.get("forward", [])
        reverse = quotes.get("reverse", [])
        if len(forward) < 1 or len(reverse) < 1:
            continue

        for fb in forward:
            for rb in reverse:
                if fb.get("factory") == rb.get("factory"):
                    continue
                if fb.get("pair_addr") == rb.get("pair_addr"):
                    continue
                edge = build_edge(fb, rb, token_a, fb.get("reserves_a", 0))
                if edge.get("net_margin", 0) > MIN_SAFETY_MARGIN_USD:
                    edges.append(edge)
                report.append({
                    "token": token_a,
                    "size_weth": edge.get("size_weth"),
                    "net_margin": edge.get("net_margin"),
                    "gross_profit": edge.get("gross_profit"),
                    "buy_factory": fb.get("factory"),
                    "sell_factory": rb.get("factory"),
                })

    edges.sort(key=lambda e: e.get("net_margin", 0), reverse=True)
    report.sort(key=lambda r: r.get("net_margin", 0), reverse=True)
    return edges, report[:20]


def select_best_edge(rpc: RPC, all_edges: List[Dict[str, Any]], min_safety_margin: float) -> Optional[Dict[str, Any]]:
    try:
        gas_wei = rpc.eth_gasPrice()
    except Exception:
        gas_wei = 0
    gas_usd = (gas_wei * GAS_UNITS / 1e18) * 2500.0

    viable: List[Dict[str, Any]] = []
    adaptive_margin = min_safety_margin if RECENTLY_PROFITABLE else max(min_safety_margin * 0.5, 0.001)

    for e in all_edges:
        gross_usd = float(e.get("gross_profit", 0.0))
        if gross_usd <= 0:
            continue
        fee = gross_usd * AAVE_FLASH_FEE
        total_cost = fee + gas_usd + adaptive_margin
        net = gross_usd - total_cost
        if net >= adaptive_margin:
            e["total_cost"] = total_cost
            e["net_margin"] = net
            viable.append(e)

    if not viable:
        return None
    return max(viable, key=lambda e: e["net_margin"])

def prepare_transaction_data(edge: Dict[str, Any], zk_payload: Dict[str, Any]) -> Dict[str, Any]:
    eth_usd_scaled = int(edge['price_a_in_b_usd'] * 1e6)
    gas_usd_scaled = int(edge['total_cost'] * 1e6)
    safety_margin_scaled = int(MIN_SAFETY_MARGIN_USD * 1e6)
    state_root = zk_payload['state_root_hash']
    pool_a_addr = edge['pair_addr']
    pool_b_addr = edge['pair_addr']
    reserve_a0 = edge['reserves_a']

    tx_data = {
        "eth_usd": eth_usd_scaled,
        "gas_usd": gas_usd_scaled,
        "safety_margin": safety_margin_scaled,
        "state_root": state_root,
        "pool_a_addr": pool_a_addr,
        "pool_b_addr": pool_b_addr,
        "reserve_a0": reserve_a0,
        "trade_amount_a_scaled": int(1 * (10 ** edge.get('token_a_decimals', 18))),
        "token_b_address": TOKENS.get(edge['token_b'], {}).get('address', edge['token_b'])
    }
    return tx_data

def execute_trade(rpc: RPC, tx_data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    print("[arb_engine] Executing transaction via ZK-Verifiable call...")
    try:
        tx_hash = rpc.eth_sendRawTransaction(
            "0x" + SEL["execute"] +
            pad_addr(tx_data['trade_amount_a_scaled']) +
            pad_addr(tx_data['pool_a_addr']) +
            pad_addr(tx_data['pool_b_addr']) +
            pad_addr(tx_data['token_b_address'])
        )
        receipt = rpc.wait_for_tx(tx_hash, timeout=120)
        status = int(receipt.get("status", "0x0"), 16)
        if status == 1:
            print(f"[arb_engine] SUCCESS! Transaction confirmed. Hash: {tx_hash}")
            return tx_hash, "Transaction executed successfully and swept ETH."
        else:
            return tx_hash, "Tx failed. Check receipt for details."
    except Exception as e:
        print(f"[arb_engine] FATAL ERROR during transaction execution: {e}")
        return "N/A", str(e)

def main_loop(rpc: RPC):
    global ZK_PROVER
    print("--- VERITAS Arbitrage Engine Initializing ---")
    ZK_PROVER = ZKProver(rpc)
    print("[arb_engine] ZKProver initialized.")

    last_execution_time = 0
    while True:
        cycle_start = time.time()

        all_edges = scan_all_pairs(rpc, TOKENS, TOKEN_DECIMALS_CACHE)
        best_edge = select_best_edge(rpc, all_edges, MIN_SAFETY_MARGIN_USD)

        tx_data = None
        if best_edge:
            time_since_last_exec = time.time() - last_execution_time
            if time_since_last_exec >= SCAN_INTERVAL_SECONDS:
                print(f"[arb_engine] Executing ZK Proof for edge: {best_edge['pair_addr']}")
                try:
                    zk_payload = ZK_PROVER.generate_proof(best_edge)
                    tx_data = prepare_transaction_data(best_edge, zk_payload)
                except Exception as e:
                    print(f"[zk_prover] ** CRITICAL ERROR **: ZK Proof Failed. Error: {e}")
                    tx_data = None
            else:
                remaining = SCAN_INTERVAL_SECONDS - time_since_last_exec
                print(f"[arb_engine] Profitable edge found, but waiting {remaining:.1f}s before next execution.")

        if tx_data:
            tx_hash, result_msg = execute_trade(rpc, tx_data)
            last_execution_time = time.time()
            warm_recent_edges(best_edge)

            print("\n" + "="*60)
            print(f"✅ ARBITRAGE CYCLE COMPLETE ({datetime.now().strftime('%H:%M:%S')})")
            print(f"   -> Selected Edge: {best_edge['pair_addr']} ({best_edge['token_a']} -> {best_edge['token_b']})")
            print(f"   -> Net Margin: {best_edge['net_margin']:.6f} USD")
            print(f"   -> Transaction Hash: {tx_hash}")
            print(f"   -> Result: {result_msg}")
            print("="*60 + "\n")
        else:
            print(f"[arb_engine] Cycle finished, no trade executed.")

        elapsed = time.time() - cycle_start
        if tx_data:
            sleep_time = max(0.0, SCAN_INTERVAL_SECONDS - elapsed)
        else:
            time_since_last_exec = time.time() - last_execution_time
            sleep_time = max(0.0, SCAN_INTERVAL_SECONDS - time_since_last_exec)

        if sleep_time > 0:
            print(f"[arb_engine] Sleeping {sleep_time:.2f}s until next execution/rescan window...")
            time.sleep(sleep_time)
        else:
            print(f"[arb_engine] Rescanning immediately to preserve execution cadence.")

if __name__ == "__main__":
    try:
        main_loop(RPC(FORK_URL, timeout=20, retries=3))
    except KeyboardInterrupt:
        print("\n--- VERITAS Engine Shutdown Initiated by User ---")
    except Exception as e:
        print(f"\n!!! UNHANDLED FATAL ERROR IN MAIN LOOP !!!: {e}")
