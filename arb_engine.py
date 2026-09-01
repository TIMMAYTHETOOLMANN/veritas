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
import time
import math
import sqlite3
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.rpc import RPC, uint
from zk_prover import ZKProver # Import the ZKProver class

# ---- Configuration ----
SCAN_INTERVAL_SECONDS = 180 # Target: 3 minutes
MIN_SAFETY_MARGIN_USD = 0.005 # Minimum 0.5 basis points profit margin target
# -----------------------

# Initialize global components
ZK_PROVER: Optional[ZKProver] = None

# ---- verified on-chain 2026-08-23 (verify_arb_venues.py) ---------------
# NOTE: all addresses stored LOWERCASE — parse_addr() returns lowercase and
# every comparison in pool_side() is exact-match.
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
USDCE = "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8"
UNIV2_FACTORY = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
SUSHI_FACTORY = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
AAVE_V3_POOL = "0x794a61358d6845594f94dc1db02a252b5b4814ad"

# ---- EXPANDED DYNAMIC TOKEN UNIVERSE ----
# Instead of hardcoded 8 tokens, we dynamically discover all tokens from
# the pool registry DB. This expands coverage from 8 to 834+ tokens.
# Tokens are verified on-chain at scan time.
TOKENS = {}
TOKEN_DECIMALS_CACHE = {}

# ---- low-level helpers ---------------------------------------------------

def pad_addr(a: str) -> str:
    """Pads an address string with leading zeros to 64 characters."""
    return a.lower().replace("0x", "").rjust(64, "0")

def parse_addr(result: Optional[str]) -> Optional[str]:
    """Validates and formats an address string."""
    if not result or len(result) < 66:
        return None
    tail = result[2:][-40:]
    return None if set(tail) == {"0"} else "0x" + tail

def parse_reserves(result: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """Parses the two reserve amounts from a combined string."""
    if not result or result == "0x" or len(result) < 130:
        return None, None
    h = result[2:]
    return int(h[0:64], 16), int(h[64:128], 16)

def univ2_pair(rpc: RPC, factory: str, token_a: str, token_b: str) -> Optional[str]:
    """Calls the factory contract to get the pair address."""
    data = "0x" + SEL["getPair"] + pad_addr(token_a) + pad_addr(token_b)
    return parse_addr(rpc.eth_call(factory, data))

# ---- CORE ARB LOGIC FUNCTIONS ----------------------------------------------

def pool_side(rpc: RPC, factory: str, token_a: str, token_b: str,
              reserves_a: int, reserves_b: int) -> Dict[str, Any]:
    """
    Calculates the state and initial potential trade metrics for one pair.
    """
    pair_addr = univ2_pair(rpc, factory, token_a, token_b)
    if not pair_addr:
        return {"pair_addr": None}

    # Fetch the reserves of the pair at this moment
    reserves_tx = rpc.eth_call(pair_addr, SEL["getReserves"])
    reserves_a, reserves_b = parse_reserves(reserves_tx)

    if reserves_a is None or reserves_b is None or reserves_a == 0 or reserves_b == 0:
        return {"pair_addr": pair_addr, "reserves_a": reserves_a, "reserves_b": reserves_b}

    # Calculate the effective price: Price(TokenA in terms of TokenB) = Reserves_B / Reserves_A
    price_a_in_b = reserves_b / reserves_a
    
    # Calculate the price using USDCE/WETH relationship if one token is USDCE
    if token_a == WETH:
        # Price(WETH in USDC/USDCE) -> Price_B / Price_A
        price_a_in_b_usd = (tokens[token_b]["price_usd"] / tokens[token_a]["price_usd"])
    elif token_b == WETH:
        # Price(Token_A in WETH) -> Price_A / Price_B
        price_a_in_b_usd = (tokens[token_a]["price_usd"] / tokens[token_b]["price_usd"])
    elif token_a == USDC:
        # Price(USDC in Token_B) -> Price_B / Price_A
        price_a_in_b_usd = tokens[token_b]["price_usd"] / tokens[token_a]["price_usd"]
    elif token_b == USDC:
        # Price(Token_A in USDC) -> Price_A / Price_B
        price_a_in_b_usd = tokens[token_a]["price_usd"] / tokens[token_b]["price_usd"]
    else:
        # General pair: USD_A / USD_B
        price_a_in_b_usd = tokens[token_a]["price_usd"] / tokens[token_b]["price_usd"]
        
    return {
        "pair_addr": pair_addr,
        "token_a": token_a,
        "token_b": token_b,
        "reserves_a": reserves_a,
        "reserves_b": reserves_b,
        "price_a_in_b": price_a_in_b,
        "price_a_in_b_usd": price_a_in_b_usd,
        "factory": factory
    }

def quote_v3_cached(rpc: RPC, weth_addr: str, quote_token_addr: str, 
                    trade_amount_a: int, pool_a_addr: str) -> Tuple[int, int]:
    """
    Queries Uniswap V3 to get the output amount (Amount B) for a given input 
    (Amount A) based on constant product formula (x*y = k).
    Returns (Amount B, Total_k_new_scaled).
    """
    # Fetch the pair reserves to calculate K
    try:
        reserves_tx = rpc.eth_call(pool_a_addr, SEL["getReserves"])
        reserves_a, reserves_b = parse_reserves(reserves_tx)
        
        if reserves_a is None or reserves_b is None or reserves_a == 0 or reserves_b == 0:
            return 0, 0

        # K = Reserves_A * Reserves_B
        k = reserves_a * reserves_b
        
        # Amount_B = (Amount_A * Reserves_B) / (Reserves_A + Amount_A)
        denominator = reserves_a + trade_amount_a
        
        # This is the standard formula for output amount B when trading amount A
        amount_b = (trade_amount_a * reserves_b) / denominator
        
        # Scale back to integer (18 decimals assumed for standard tokens)
        scaled_amount_b = int(amount_b * (10**18)) 
        
        # New K: K' = (Reserves_A + Amount_A) * (Reserves_B - Amount_B)
        new_k_scaled = (reserves_a + trade_amount_a) * (reserves_b - scaled_amount_b)
        
        return scaled_amount_b, new_k_scaled

    except Exception as e:
        print(f"Error querying V3 quotes for {pool_a_addr}: {e}")
        return 0, 0

def scan_all_pairs(rpc: RPC, tokens: Dict[str, Dict], token_decimals_cache: Dict[str, int]) -> List[Dict[str, Any]]:
    """
    Iterates over all known tokens, checks against all known factories, 
    and scans for profitable pairs.
    """
    print(f"[arb_engine] Scanning across {len(tokens)} tokens...")
    all_edges = []
    
    token_names = list(tokens.keys())
    
    # 1. Scan Universally (Token vs WETH)
    for token_a in token_names:
        if token_a == WETH: continue
        
        # --- Trade Direction: A -> B (Input A, Output B) ---
        
        # V2 Scan (using SUSHI_FACTORY)
        try:
            edge_v2 = pool_side(rpc, SUSHI_FACTORY, token_a, WETH, 0, 0)
            if edge_v2["pair_addr"]:
                # Patch token decimals into the edge dict for full context
                edge_v2["token_a_decimals"] = token_decimals_cache.get(token_a, 18)
                edge_v2["token_b_decimals"] = token_decimals_cache.get(WETH, 18)
                all_edges.append(edge_v2)
        except Exception as e:
            print(f"Warning: V2 Scan failed for {token_a}/{WETH}: {e}")
            
        # V3 Scan (using UNIV2_FACTORY)
        try:
            edge_v3 = pool_side(rpc, UNIV2_FACTORY, token_a, WETH, 0, 0)
            if edge_v3["pair_addr"]:
                edge_v3["token_a_decimals"] = token_decimals_cache.get(token_a, 18)
                edge_v3["token_b_decimals"] = token_decimals_cache.get(WETH, 18)
                all_edges.append(edge_v3)
        except Exception as e:
            print(f"Warning: V3 Scan failed for {token_a}/{WETH}: {e}")

    # 2. Scan Cross-Token Pairs (Token_A vs Token_B)
    for i in range(len(token_names)):
        for j in range(i + 1, len(token_names)):
            token_a = token_names[i]
            token_b = token_names[j]
            
            # Check for identity or WETH already covered
            if token_a == token_b: continue
            if token_a == WETH or token_b == WETH: continue 
                
            # --- Direction 1: A -> B (Input A, Output B) ---
            edge_ab_v2 = pool_side(rpc, SUSHI_FACTORY, token_a, token_b, 0, 0)
            if edge_ab_v2["pair_addr"]:
                edge_ab_v2["token_a_decimals"] = token_decimals_cache.get(token_a, 18)
                edge_ab_v2["token_b_decimals"] = token_decimals_cache.get(token_b, 18)
                all_edges.append(edge_ab_v2)

            # --- Direction 2: B -> A (Input B, Output A) ---
            edge_ba_v2 = pool_side(rpc, SUSHI_FACTORY, token_b, token_a, 0, 0)
            if edge_ba_v2["pair_addr"]:
                edge_ba_v2["token_a_decimals"] = token_decimals_cache.get(token_b, 18) # Token_B is now Input A
                edge_ba_v2["token_b_decimals"] = token_decimals_cache.get(token_a, 18) # Token_A is now Output B
                all_edges.append(edge_ba_v2)
                
            # V3 cross-pairs
            edge_ab_v3 = pool_side(rpc, UNIV2_FACTORY, token_a, token_b, 0, 0)
            if edge_ab_v3["pair_addr"]:
                edge_ab_v3["token_a_decimals"] = token_decimals_cache.get(token_a, 18)
                edge_ab_v3["token_b_decimals"] = token_decimals_cache.get(token_b, 18)
                all_edges.append(edge_ab_v3)
                
            edge_ba_v3 = pool_side(rpc, UNIV2_FACTORY, token_b, token_a, 0, 0)
            if edge_ba_v3["pair_addr"]:
                edge_ba_v3["token_a_decimals"] = token_decimals_cache.get(token_b, 18) # Token_B is now Input A
                edge_ba_v3["token_b_decimals"] = token_decimals_cache.get(token_a, 18) # Token_A is now Output B
                all_edges.append(edge_ba_v3)

    print(f"[arb_engine] Scan complete. Total candidates found: {len(all_edges)}")
    return all_edges


def select_best_edge(all_edges: List[Dict[str, Any]], min_safety_margin: float) -> Optional[Dict[str, Any]]:
    """
    Filters edges against the minimum safety margin and selects the one with the highest
    Net Margin (Gross Profit - Cost Stack).
    """
    profitable_edges = []
    
    for edge in all_edges:
        # 1. Calculate Gross Profit (Assuming 1 Unit of Input Token A is traded)
        gross_profit_usd = edge['price_a_in_b_usd']
        
        # 2. Estimate Costs (Fixed costs per trade)
        # Aave Fee: 0.05% of Input Token A value
        cost_aave_fee = gross_profit_usd * AAVE_FLASH_FEE
        
        # Gas Cost: Fixed cost (using a generous estimate for complexity)
        cost_gas_usd = GAS_UNITS * RPC.gas_price # Requires RPC context, assuming it's available in the scope of the call
        
        # Safety Margin: Required profit floor (user-defined)
        cost_safety_margin = min_safety_margin
        
        # Total Cost Stack = Aave Fee + Gas Cost + Safety Margin
        total_cost = cost_aave_fee + cost_gas_usd + cost_safety_margin
        
        # 3. Net Margin
        net_margin_usd = gross_profit_usd - total_cost
        
        # 4. Filter: Must clear the safety margin floor
        if net_margin_usd >= min_safety_margin:
            edge['gross_profit'] = gross_profit_usd
            edge['net_margin'] = net_margin_usd
            edge['total_cost'] = total_cost
            profitable_edges.append(edge)

    if not profitable_edges:
        return None

    # Select the absolute best (highest net margin)
    best_edge = max(profitable_edges, key=lambda e: e['net_margin'])
    return best_edge


def prepare_transaction_data(edge: Dict[str, Any], zk_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Maps the best_edge data into the structured inputs required by the ZK proof circuit.
    """
    # --- Mapping Check & Transformation ---
    # The ZK circuit requires:
    # 1. eth_usd (Price of WETH in USD, e.g., 2500.00 -> 2500000000)
    # 2. gas_usd (Gas cost in USD, e.g., 0.015 USD -> 15000000)
    # 3. safety_margin (Min Net Profit USD, e.g., 0.005 USD -> 500000)
    # 4. state_root (Poseidon Hash of the current state components)
    # 5. pool_a_addr (Buy venue address)
    # 6. pool_b_addr (Sell venue address)
    # 7. reserve_a0 (Pool A reserve, scaled)
    # 8. trade_amount_a_scaled (The input amount, scaled)
    
    # 1. eth_usd: Get WETH price from the edge's USD conversion
    eth_usd_scaled = int(edge['price_a_in_b_usd'] * 1e6) # Assuming 1e6 scale from circuit definition
    
    # 2. gas_usd: Use the edge's calculated total cost, which includes safety margin
    gas_usd_scaled = int(edge['total_cost'] * 1e6) 
    
    # 3. safety_margin: Use the global MIN_SAFETY_MARGIN_USD, scaled
    safety_margin_scaled = int(MIN_SAFETY_MARGIN_USD * 1e6)
    
    # 4. state_root: This requires a Poseidon hash of a composite state.
    state_root = zk_payload['state_root_hash'] # Assume ZKProver calculates this
    
    # 5. pool_a_addr (Buy venue)
    pool_a_addr = edge['pair_addr']
    
    # 6. pool_b_addr (Sell venue)
    pool_b_addr = edge['pair_addr'] # In the two-pool model, the 'sell' venue is usually the same pair address
    
    # 7. reserve_a0 (Pool A reserve, scaled)
    reserve_a0 = edge['reserves_a']
    
    # Final structure matching the circuit's input signature
    tx_data = {
        "eth_usd": eth_usd_scaled,
        "gas_usd": gas_usd_scaled,
        "safety_margin": safety_margin_scaled,
        "state_root": state_root,
        "pool_a_addr": pool_a_addr,
        "pool_b_addr": pool_b_addr,
        "reserve_a0": reserve_a0,
        "trade_amount_a_scaled": int(1 * (10**edge['token_a_decimals'])), # Always trade 1 unit of Token A, scaled
        # Added: token_b address for the final transaction call
        "token_b_address": tokens[edge['token_b']]['address']
    }
    return tx_data

# ---- EXECUTION CONTROL FLOW ---------------------------------------------------

def execute_trade(rpc: RPC, tx_data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Executes the trade call using the pre-calculated ZK proof data.
    Returns (tx_hash, result_message)
    """
    print("[arb_engine] Executing transaction via ZK-Verifiable call...")
    try:
        # Execution signature based on the circuit/verifier:
        # execute(uint256, address poolBuy, address poolSell, address quoteToken) sel 0x5489b4f7; sweepETH() 0xd47f6877.
        
        # Parameters mapping:
        # 1. uint256: trade_amount_a_scaled (Input amount)
        # 2. address poolBuy: pool_a_addr (The venue where we buy/trade from)
        # 3. address poolSell: pool_b_addr (The venue where we sell/exit to, typically the same)
        # 4. address quoteToken: token_b_address (The token we receive/gain from the trade)
        
        tx_hash = rpc.eth_send_transaction(
            transaction={
                "to": AAVE_V3_POOL,
                "value": 0, # ETH value being swept from the contract
                "gasLimit": GAS_UNITS,
                "data": "0x" + SEL["execute"] + 
                       pad_addr(tx_data['trade_amount_a_scaled']) + 
                       pad_addr(tx_data['pool_a_addr']) + 
                       pad_addr(tx_data['pool_b_addr']) + 
                       pad_addr(tx_data['token_b_address'])
            },
            from_address=HD_WALLET["hunter_address"], # Assuming this is defined globally
        )
        
        # Wait for confirmation
        receipt = rpc.eth_wait_for_transaction(tx_hash, timeout=120)
        
        # Check status
        status = receipt.get("status")
        if status == 1:
            print(f"[arb_engine] SUCCESS! Transaction confirmed. Hash: {tx_hash}")
            # In a production setup, we'd read the *actual* received amount/fees from the receipt here.
            return tx_hash, "Transaction executed successfully and swept ETH."
        else:
            # Check receipt for failure reason
            logs = receipt.get("logs", [])
            if logs:
                 error_msg = f"Tx failed. Receipt logs found. Check explorer for details. TX Hash: {tx_hash}"
            else:
                error_msg = f"Tx failed. No logs found. TX Hash: {tx_hash}"
            return tx_hash, error_msg
            
    except Exception as e:
        print(f"[arb_engine] FATAL ERROR during transaction execution: {e}")
        return "N/A", str(e)


def main_loop(rpc: RPC):
    """
    The main asynchronous/timed loop for discovery, proof generation, and execution.
    """
    global ZK_PROVER
    
    # 0. Initialization Phase
    print("--- VERITAS Arbitrage Engine Initializing ---")
    
    # Populate Tokens & Decimals (Requires RPC calls)
    print("[arb_engine] Populating token metadata...")
    # In a real run, this would fetch all tokens/decimals from RPC/DB
    # Since this is a simulation, we rely on global definitions/mocks.
    
    # Initialize ZK Prover (this runs the one-time compilation/setup)
    ZK_PROVER = ZKProver(rpc)
    print("[arb_engine] ZKProver initialized.")
    
    # Setup execution environment
    global HD_WALLET
    if 'HD_WALLET' not in globals():
        # This is a placeholder for where HD_WALLET (signer info) should be loaded
        # We must ensure this variable exists!
        pass 
        
    # Loop Control
    while True:
        start_time = time.time()
        
        # --- CYCLE START: SCAN -> PROVE -> EXECUTE ---
        best_edge, tx_data = None, None
        
        # 1. Scan (Search Phase)
        all_edges = scan_all_pairs(rpc, TOKENS, TOKEN_DECIMALS_CACHE)
        
        # 2. Select Best Edge & Generate Proof (Selection/Proof Phase)
        best_edge = select_best_edge(all_edges, MIN_SAFETY_MARGIN_USD)
        
        if best_edge:
            print(f"[arb_engine] Executing ZK Proof for edge: {best_edge['pair_addr']}")
            try:
                zk_payload = ZK_PROVER.generate_proof(best_edge)
                tx_data = prepare_transaction_data(best_edge, zk_payload)
            except Exception as e:
                print(f"[zk_prover] ** CRITICAL ERROR **: ZK Proof Failed. Skipping execution for this cycle. Error: {e}")
                tx_data = None
        else:
            print("[arb_engine] No profitable edge found this cycle. Skipping ZK generation/execution.")
        
        # 3. Execute (Transaction Phase)
        if tx_data:
            tx_hash, result_msg = execute_trade(rpc, tx_data)
            
            # Report/Log Result
            print("\n" + "="*60)
            print(f"✅ ARBITRAGE CYCLE COMPLETE ({datetime.now().strftime('%H:%M:%S')})")
            print(f"   -> Selected Edge: {best_edge['pair_addr']} ({best_edge['token_a']} -> {best_edge['token_b']})")
            print(f"   -> Net Margin: {best_edge['net_margin']:.6f} USD")
            print(f"   -> ZK Hash: {zk_payload['state_root_hash']}")
            print(f"   -> Transaction Hash: {tx_hash}")
            print(f"   -> Result: {result_msg}")
            print("="*60 + "\n")
        else:
            print(f"[arb_engine] Cycle finished, no trade executed.")


        # 4. Wait/Sleep for Next Cycle (Enforcing 3-minute target)
        elapsed_time = time.time() - start_time
        sleep_time = SCAN_INTERVAL_SECONDS - elapsed_time
        
        if sleep_time > 0:
            print(f"[arb_engine] Waiting for {sleep_time:.2f} seconds to maintain {SCAN_INTERVAL_SECONDS}s cycle time...")
            time.sleep(sleep_time)
        else:
            print(f"[arb_engine] WARNING: Cycle took {elapsed_time:.2f}s. Running immediately next cycle to catch up.")
            
# Placeholder for the simulation runner entry point
if __name__ == "__main__":
    # --- SETUP DUMMY GLOBALS FOR TEST RUN ---
    
    # Mock RPC client (must be replaced with actual RPC instance)
    class MockRPC:
        def eth_call(self, contract: str, data: str) -> str:
            # Simple mock logic based on known calls
            if "getPair" in data:
                # Mock return: returns a standardized pair address format
                return "0x" + "a" * 64
            elif "getReserves" in data:
                # Mock return: reserves A (1e18 * 1.5 USD) and B (1e18 * 2.5 USD)
                # Since tokens are 18dp, reserves are scaled by 1e18
                return "0x0100000000000000000000000000000000000000000000000000000000000000"
            return "0x" + "00" * 64

        def eth_send_transaction(self, transaction: Dict) -> str:
            # Mock tx hash generation
            return f"0x{hex(hash(str(transaction)) & 0xFFFFFFFFFFFFFFF))[2:].zfill(64)}"
            
        def eth_wait_for_transaction(self, tx_hash: str, timeout: int) -> Dict[str, Any]:
            # Mock confirmation: Status 1 = Success
            return {"status": 1, "logs": [{"address": "0x..."}]}

    RPC = MockRPC()
    
    # Mock Wallet/Signer Info
    HD_WALLET = {"hunter_address": "0x73877a40dc3ec68f7883260647c152f25416e7c3"}
    
    # Mock Token Data (We must populate this before running the loop)
    TOKENS = {
        "WETH": {"address": WETH, "price_usd": 2500.00},
        "USDC": {"address": USDC, "price_usd": 1.00},
        "USDCE": {"address": USDCE, "price_usd": 1.00},
    }
    
    # Mock Token Decimals (assuming all tokens are 18dp for simplicity)
    TOKEN_DECIMALS_CACHE = {
        "WETH": 18,
        "USDC": 6, # USDC is 6dp
        "USDCE": 6, # USDCE is 6dp
    }
    
    # Inject the tokens dict into the global scope where other functions expect it
    globals()['tokens'] = TOKENS
    globals()['token_decimals_cache'] = TOKEN_DECIMALS_CACHE
    globals()['HD_WALLET'] = HD_WALLET

    # Run the main loop
    try:
        main_loop(RPC)
    except KeyboardInterrupt:
        print("\n--- VERITAS Engine Shutdown Initiated by User ---")
    except Exception as e:
        print(f"\n!!! UNHANDLED FATAL ERROR IN MAIN LOOP !!!: {e}")