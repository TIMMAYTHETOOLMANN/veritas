#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '.')
from flash_hunter import get_rpc
from eth_account import Account
import arb_engine
import json

rpc, _ = get_rpc()
acct = Account.from_key(open('.hot_secret').read().strip())
print('Account:', acct.address)

from web3 import Web3
w3 = Web3(Web3.HTTPProvider(rpc.url))
pair_addr = Web3.to_checksum_address('0x57b85fef094e10b5eecdf350af688299e9553378')
pair_abi = [{'inputs':[],'name':'getReserves','outputs':[{'internalType':'uint112','name':'reserve0','type':'uint112'},{'internalType':'uint112','name':'reserve1','type':'uint112'},{'internalType':'uint32','name':'blockTimestampLast','type':'uint32'}],'stateMutability':'view','type':'function'}]
pair = w3.eth.contract(address=pair_addr, abi=pair_abi)
res = pair.functions.getReserves().call()
weth_res = res[0]
usdc_res = res[1]
eth_usd = (usdc_res / 1e6) / (weth_res / 1e18) if weth_res != 0 else 2450.0
print('ETH/USD: %.2f' % eth_usd)
gas_wei = int(rpc.call('eth_gasPrice', []), 16)
gas_usd = (gas_wei * 450_000 / 1e18) * eth_usd
print('Gas cost USD: %.6f' % gas_usd)
edges, report = arb_engine.scan_cross_venue(rpc, eth_usd, gas_usd, size_steps=12, max_venues_per_quote=8, use_multi_hop=True, use_parallel=True)
print('Edges found: %d' % len(edges))
if edges:
    for e in edges[:5]:
        print('  %s -> %s size=%s net=$%.4f gross=$%.4f' % (e.get('venue_buy'), e.get('venue_sell'), e.get('size_weth'), e.get('net_usd'), e.get('gross_usd')))
else:
    print('No edges.')
    print('Report entries:', len(report) if isinstance(report, list) else report)
    if isinstance(report, list):
        for r in report[:5]:
            print('  ', json.dumps(r))