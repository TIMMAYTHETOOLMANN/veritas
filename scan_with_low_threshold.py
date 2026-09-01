#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '.')

from flash_hunter import get_rpc, load_key
from eth_account import Account
import arb_engine
import json

# Temporarily lower the safety margin to see if we can get any edges
original_safety = arb_engine.SAFETY_MARGIN_USD
arb_engine.SAFETY_MARGIN_USD = 0.01  # $0.01 net profit required
print(f"Setting SAFETY_MARGIN_USD to {arb_engine.SAFETY_MARGIN_USD}")

# Also lower MIN_DISLOCATION_BPS? That is in arb_engine as well.
original_mindisl = arb_engine.MIN_DISLOCATION_BPS
arb_engine.MIN_DISLOCATION_BPS = 5.0  # 5 bps instead of 15
print(f"Setting MIN_DISLOCATION_BPS to {arb_engine.MIN_DISLOCATION_BPS}")

rpc, _ = get_rpc()
acct = Account.from_key(load_key())
print(f"RPC: {rpc.url}")
print(f"Account: {acct.address}")

# Get ETH/USD price from Sushi using getReserves
SUSHI_WETH_USDC = "0x57b85fef094e10b5eecdf350af688299e9553378"
from web3 import Web3
w3 = Web3(Web3.HTTPProvider(rpc.url))
pair_addr = Web3.to_checksum_address(SUSHI_WETH_USDC)
pair_abi = [{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"reserve0","type":"uint112"},{"name":"reserve1","type":"uint112"},{"name":"blockTimestampLast","type":"uint32"}],"type":"function"}]
pair_contract = w3.eth.contract(address=pair_addr, abi=pair_abi)
reserves = pair_contract.functions.getReserves().call()
weth_res = reserves[0]
usdc_res = reserves[1]
if weth_res == 0:
    eth_usd = 2450.0
else:
    eth_usd = (usdc_res / 1e6) / (weth_res / 1e18)
print(f"ETH/USD price from Sushi reserves: {eth_usd}")

# Gas price
gas_wei = int(rpc.call("eth_gasPrice", []), 16)
gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd  # 450k gas estimate
print(f"Gas price: {gas_wei} wei, gas cost in USD: {gas_usd}")

# Now scan with increased size_steps to capture more granularity
print("Calling scan_cross_venue with size_steps=20...")
edges, report = arb_engine.scan_cross_venue(rpc, eth_usd, gas_usd,
                                            size_steps=20,
                                            max_venues_per_quote=8,
                                            use_multi_hop=True,
                                            use_parallel=True)
print(f"Found {len(edges)} edges")
if edges:
    for i, e in enumerate(edges[:10]):
        print(f"  Edge {i}: {e.get('venue_buy')} -> {e.get('venue_sell')} size={e.get('size_weth')} WETH net=${e.get('net_usd'):.6f} gross=${e.get('gross_usd'):.6f}")
else:
    print("No edges found. Let's examine the report.")
    if isinstance(report, list) and len(report) > 0:
        print(f"Report has {len(report)} entries. First entry: {json.dumps(report[0], indent=2)}")
    else:
        print(f"Report type: {type(report)}")
        print(f"Report content: {report}")

# Restore original values
arb_engine.SAFETY_MARGIN_USD = original_safety
arb_engine.MIN_DISLOCATION_BPS = original_mindisl
print("Restored original constants.")