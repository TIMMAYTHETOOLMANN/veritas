#!/usr/bin/env python3
import json
import time
import os
from web3 import Web3
from eth_account import Account

# Connect to Arbitrum
rpc_url = "https://arb1.arbitrum.io/rpc"
w3 = Web3(Web3.HTTPProvider(rpc_url))
if not w3.is_connected():
    raise Exception("Failed to connect to Arbitrum")

# Load addresses
with open('.executor_zk_address', 'r') as f:
    zk_executor_addr = f.read().strip()
print(f"ZK Executor address: {zk_executor_addr}")

# Hot wallet address from code
hot_wallet = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"
print(f"Hot wallet: {hot_wallet}")

# Load private key
with open('.hot_secret', 'r') as f:
    private_key = f.read().strip()
if not private_key.startswith('0x'):
    private_key = '0x' + private_key
account = Account.from_key(private_key)
print(f"Account address: {account.address}")
assert account.address.lower() == hot_wallet.lower(), "Hot wallet mismatch"

# Load ABI for sweepProfit (from FlashloanArb.abi.json, same as ZKArbExecutor)
with open('contracts/FlashloanArb.abi.json', 'r') as f:
    abi = json.load(f)

# Create contract instance
zk_contract = w3.eth.contract(address=w3.to_checksum_address(zk_executor_addr), abi=abi)

# Function to get WETH balance in contract
def get_weth_balance():
    # WETH token address on Arbitrum
    weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    weth_abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
    weth_contract = w3.eth.contract(address=w3.to_checksum_address(weth_addr), abi=weth_abi)
    balance = weth_contract.functions.balanceOf(zk_executor_addr).call()
    return balance

# Function to sweep profit
def sweep_profit():
    # Build transaction
    nonce = w3.eth.get_transaction_count(hot_wallet)
    # We'll sweep WETH
    weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    txn = zk_contract.functions.sweepProfit(weth_addr).build_transaction({
        'chainId': 42161,
        'gas': 200000,
        'gasPrice': w3.to_wei('0.1', 'gwei'),
        'nonce': nonce,
    })
    # Sign transaction
    signed = account.sign_transaction(txn)
    # Send transaction
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"Sweep transaction sent: {tx_hash.hex()}")
    # Wait for receipt
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Sweep transaction receipt: status={receipt.status}, gasUsed={receipt.gasUsed}")
    return receipt

# Function to get ETH price in USD (using the Sushi pool as in the hunter)
def get_eth_usd_price():
    # Sushi WETH/USDC pool
    pair_addr = "0x57b85fef094e10b5eecdf350af688299e9553378"
    # ABI for reserve
    pair_abi = [{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"name":"reserve0","type":"uint112"},{"name":"reserve1","type":"uint112"},{"name":"blockTimestampLast","type":"uint32"}],"type":"function"}]
    pair_contract = w3.eth.contract(address=w3.to_checksum_address(pair_addr), abi=pair_abi)
    reserves = pair_contract.functions.getReserves().call()
    # Assuming reserve0 is WETH, reserve1 is USDC (as per the hunter's code)
    weth_res = reserves[0]
    usdc_res = reserves[1]
    if weth_res == 0:
        return 0
    return usdc_res / weth_res  # USDC has 6 decimals, WETH 18, but ratio is same

# Function to get WETH balance in wallet
def get_wallet_weth_balance():
    weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    weth_abi = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}]
    weth_contract = w3.eth.contract(address=w3.to_checksum_address(weth_addr), abi=weth_abi)
    balance = weth_contract.functions.balanceOf(hot_wallet).call()
    return balance

# Main loop
print("Starting monitoring loop...")
target_usd = 10.0
check_interval = 10  # seconds
dust_threshold_wei = int(0.0001 * 1e18)  # 0.0001 WETH

while True:
    # Check contract WETH balance
    weth_balance = get_weth_balance()
    eth_usd = get_eth_usd_price()
    usd_value = weth_balance / 1e18 * eth_usd
    print(f"Contract WETH balance: {weth_balance / 1e18:.6f} WETH (${usd_value:.2f})")
    
    if weth_balance > dust_threshold_wei:
        # Sweep if there's any balance above dust
        print("Sweeping profit...")
        receipt = sweep_profit()
        if receipt.status == 1:
            print("Sweep successful")
            # Wait a bit for the transaction to be processed
            time.sleep(5)
        else:
            print("Sweep failed")
    else:
        print("No profit to sweep (below dust threshold)")
    
    # Check wallet balance
    wallet_weth = get_wallet_weth_balance()
    wallet_usd = wallet_weth / 1e18 * eth_usd
    print(f"Wallet WETH balance: {wallet_weth / 1e18:.6f} WETH (${wallet_usd:.2f})")
    
    if wallet_usd >= target_usd:
        print(f"Target of ${target_usd} reached! Stopping.")
        break
    
    print(f"Waiting {check_interval} seconds...")
    time.sleep(check_interval)

print("Monitoring complete.")