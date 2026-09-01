#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '.')

from flash_hunter import get_rpc, load_key
from eth_account import Account
import arb_engine
import json

def main():
    rpc, _ = get_rpc()
    acct = Account.from_key(load_key())
    print(f"RPC: {rpc.url}")
    print(f"Account: {acct.address}")
    
    # Get ETH/USD price from Sushi using getReserves (as in hunter's fallback)
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
    
    # Now scan
    print("Calling scan_cross_venue...")
    edges, report = arb_engine.scan_cross_venue(rpc, eth_usd, gas_usd,
                                                size_steps=12,
                                                max_venues_per_quote=8,
                                                use_multi_hop=True,
                                                use_parallel=True)
    print(f"Found {len(edges)} edges")
    if edges:
        for i, e in enumerate(edges[:10]):
            print(f"  Edge {i}: {e.get('venue_buy')} -> {e.get('venue_sell')} size={e.get('size_weth')} WETH net=${e.get('net_usd'):.6f} gross=${e.get('gross_usd'):.6f}")
    else:
        print("No edges found. Let's examine the report.")
        # The report might contain info about why no edges.
        # Let's see if we can print the report keys.
        if isinstance(report, dict):
            print("Report keys:", list(report.keys()))
            # Maybe there is a 'debug' or 'errors' key.
            for k, v in report.items():
                if isinstance(v, (list, dict)) and len(str(v)) < 200:
                    print(f"  {k}: {v}")
                else:
                    print(f"  {k}: <{type(v)}>")
        else:
            print(f"Report type: {type(report)}")
            print(f"Report content: {report}")

if __name__ == "__main__":
    main()