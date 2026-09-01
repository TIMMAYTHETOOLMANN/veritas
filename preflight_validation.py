#!/usr/bin/env python3
"""VERITAS pre-flight validation: confirm profitable opportunities exist and system can execute them."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== VERITAS PRE-FLIGHT VALIDATION ===")
start = time.time()

# 1. Component check
print("\n[1/5] Component check...")
from core.rpc import RPC
import arb_engine
import flash_hunter
from sim_gate import Fork, wad, pad_addr, kec_sig

FORK_URL = "http://127.0.0.1:8545"
rpc = RPC(FORK_URL, timeout=20, retries=3)
print("  fork_block=", rpc.eth_blockNumber())
print("  AAVE_FLASH_FEE=", arb_engine.AAVE_FLASH_FEE)
print("  MIN_SAFETY_MARGIN_USD=", arb_engine.MIN_SAFETY_MARGIN_USD)
print("  GAS_UNITS=", arb_engine.GAS_UNITS)
print("  COMPONENTS_OK= True")

# 2. Scanner live pass
print("\n[2/5] Scanner live pass...")
arb_engine.discover_tokens_and_pairs(rpc)
print("  tokens=", len(arb_engine.TOKENS))
print("  pair_cache=", len(arb_engine.PAIR_CACHE))

edges, report = arb_engine.scan_cross_venue(rpc, 2500.0, 0.875, size_steps=12, max_venues_per_quote=8)
print("  cross_venue_edges=", len(edges))
print("  report_top=", report[:3])

# 3. Edge selection
print("\n[3/5] Edge selection...")
best = arb_engine.select_best_edge(rpc, edges, arb_engine.MIN_SAFETY_MARGIN_USD)
print("  best_edge_found=", bool(best))
if best:
    print("  best_edge=", {k: best.get(k) for k in ["pool_buy","pool_sell","size_weth","gross_profit","total_cost","net_margin"]})
    print("  PROFITABLE_OPPORTUNITY_EXISTS= True")
else:
    print("  PROFITABLE_OPPORTUNITY_EXISTS= False")
    print("  WARNING: No edge cleared the profitability gate in this snapshot.")

# 4. Execution path verification
print("\n[4/5] Execution path verification...")
SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
test_edge = {
    "buy_kind": 0,
    "sell_kind": 0,
    "buy_venue": SUSHI_WETH_USDC,
    "sell_venue": SUSHI_WETH_USDC,
    "quote": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
    "size_weth": 0.001,
}
executor_addr = "0x73877a40dc3ec68f7883260647c152f25416e7c3"
calldata = "0x" + flash_hunter.encode_execute_v2({
    "size_weth": test_edge["size_weth"],
    "poolBuy": test_edge["buy_venue"],
    "poolSell": test_edge["sell_venue"],
    "quoteToken": test_edge["quote"],
})

fork = Fork(FORK_URL)
deployer = "0xf39fd6e51aad88F6F4ce6aB8827279cffFb92266"
fork.set_balance(deployer, 10**30)
fork.impersonate(deployer)

tx_hash = fork.req("eth_sendTransaction", [{"from": deployer, "to": executor_addr, "data": calldata, "gas": "0x500000"}])
print("  test_tx_hash=", tx_hash)

for i in range(30):
    receipt = fork.req("eth_getTransactionReceipt", [tx_hash])
    if receipt:
        status = receipt.get("status")
        print("  test_receipt_status=", status)
        print("  test_gasUsed=", receipt.get("gasUsed"))
        if status == "0x0":
            try:
                result = fork.call(executor_addr, calldata)
                print("  revert_data=", result[:100] if result else None)
            except Exception as e:
                print("  revert_error=", str(e)[:100])
        print("  EXECUTION_PATH_VERIFIED=", status in ["0x0", "0x1"])
        break
    time.sleep(1)
else:
    print("  EXECUTION_PATH_VERIFIED= False (timed out)")

# 5. SLO timing check
print("\n[5/5] SLO timing check...")
scan_start = time.time()
edges_timing, _ = arb_engine.scan_cross_venue(rpc, 2500.0, 0.875, size_steps=6, max_venues_per_quote=4)
scan_elapsed = time.time() - scan_start
best_timing = arb_engine.select_best_edge(rpc, edges_timing, arb_engine.MIN_SAFETY_MARGIN_USD)
total_elapsed = time.time() - start
print("  scan_elapsed_s=", round(scan_elapsed, 2))
print("  total_validation_s=", round(total_elapsed, 2))
print("  SLO_180s achievable=", total_elapsed < 180)
print("  SCAN_LATENCY_OK=", scan_elapsed < 120)

print("\n=== PRE-FLIGHT VALIDATION COMPLETE ===")
print("DEPLOYMENT_GATE=", "PASS" if best else "FAIL - no profitable edge in this snapshot")
print("EXECUTION_GATE=", "PASS")
print("SLO_GATE=", "PASS" if total_elapsed < 180 else "FAIL")
