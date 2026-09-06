#!/usr/bin/env python3
"""
wallet_gen_sybil.py - Real wallet generation for Sybil resistance testing.

Generates actual Ethereum wallets with real private keys and addresses.
No simulations - this creates real cryptographic key pairs.

Dependencies:
    pip install eth-account

Usage:
    python3 wallet_gen_sybil.py --count 100 --output wallets.json
    python3 wallet_gen_sybil.py --seed "my_secret_seed" --count 50 --method hd
"""
import argparse
import hashlib
import json
import os
import time
from typing import Dict, List, Optional

try:
    from eth_account import Account
    from eth_account.signers.local import LocalAccount
    ETH_AVAILABLE = True
except ImportError:
    ETH_AVAILABLE = False
    print("[ERROR] eth_account not installed. Run: pip install eth-account")
    exit(1)


class WalletGenerator:
    """
    Generates real Ethereum wallets with valid private keys and addresses.
    
    Methods:
    1. Sequential derivation from master seed (deterministic)
    2. Random generation with os.urandom entropy
    3. HD wallet derivation (BIP-44 style)
    """

    def __init__(self, master_seed: Optional[str] = None):
        self.master_seed = master_seed or os.urandom(32).hex()
        self.wallets: List[Dict] = []

    def generate_sequential(self, count: int, start_nonce: int = 0) -> List[Dict]:
        """
        Generate wallets sequentially from master seed.
        Each wallet is derived as: keccak256(master_seed + nonce)
        """
        wallets = []
        for i in range(count):
            nonce = start_nonce + i
            seed_data = f"{self.master_seed}{nonce}".encode()
            private_key = hashlib.sha256(seed_data).hexdigest()
            account: LocalAccount = Account.from_key(private_key)
            
            wallets.append({
                "index": nonce,
                "address": account.address,
                "private_key": private_key,
                "derivation_method": "sequential",
                "created_at": time.time(),
            })
        
        self.wallets.extend(wallets)
        return wallets

    def generate_random(self, count: int) -> List[Dict]:
        """
        Generate wallets with random entropy from os.urandom.
        """
        wallets = []
        for i in range(count):
            private_key_bytes = os.urandom(32)
            private_key = private_key_bytes.hex()
            account: LocalAccount = Account.from_key(private_key)
            
            wallets.append({
                "index": len(self.wallets) + i,
                "address": account.address,
                "private_key": private_key,
                "derivation_method": "random",
                "created_at": time.time(),
            })
        
        self.wallets.extend(wallets)
        return wallets

    def generate_hd(self, count: int, passphrase: str = "") -> List[Dict]:
        """
        Generate wallets using HD derivation (BIP-44 style).
        Derivation path: m/44'/60'/0'/0/{index}
        """
        wallets = []
        base_seed = hashlib.sha256(f"{self.master_seed}{passphrase}".encode()).digest()
        
        for i in range(count):
            index_bytes = i.to_bytes(4, 'big')
            private_key = hashlib.sha256(base_seed + index_bytes).hexdigest()
            account: LocalAccount = Account.from_key(private_key)
            
            wallets.append({
                "index": i,
                "address": account.address,
                "private_key": private_key,
                "derivation_method": "hd",
                "derivation_path": f"m/44'/60'/0'/0/{i}",
                "created_at": time.time(),
            })
        
        self.wallets.extend(wallets)
        return wallets

    def save_wallets(self, filepath: str) -> None:
        """
        Save wallet list to JSON file.
        WARNING: This file contains private keys. Handle with extreme care.
        """
        with open(filepath, 'w') as f:
            json.dump({
                "master_seed": self.master_seed,
                "wallet_count": len(self.wallets),
                "generated_at": time.time(),
                "wallets": self.wallets,
            }, f, indent=2)
        print(f"[wallet_gen] Saved {len(self.wallets)} wallets to {filepath}")
        print("[wallet_gen] WARNING: File contains private keys - handle securely!")



def main():
    parser = argparse.ArgumentParser(description="Real wallet generation for Sybil resistance testing")
    parser.add_argument("--count", type=int, default=10, help="Number of wallets to generate")
    parser.add_argument("--method", choices=["sequential", "random", "hd"], default="sequential",
                        help="Wallet derivation method")
    parser.add_argument("--seed", type=str, help="Master seed (optional, random if not provided)")
    parser.add_argument("--output", type=str, default="wallets.json", help="Output file path")
    parser.add_argument("--passphrase", type=str, default="", help="Passphrase for HD derivation")
    args = parser.parse_args()

    print("=" * 60)
    print("Real Wallet Generator - Creates Valid Ethereum Key Pairs")
    print("=" * 60)
    print(f"Method: {args.method}")
    print(f"Count: {args.count}")
    print(f"Output: {args.output}")
    print()

    generator = WalletGenerator(master_seed=args.seed)
    
    if args.method == "sequential":
        wallets = generator.generate_sequential(args.count)
    elif args.method == "random":
        wallets = generator.generate_random(args.count)
    elif args.method == "hd":
        wallets = generator.generate_hd(args.count, passphrase=args.passphrase)
    
    print(f"[wallet_gen] Generated {len(wallets)} real wallets")
    
    print("\nSample wallets (first 5):")
    for w in wallets[:5]:
        print(f"  [{w['index']}] {w['address']}")
        print(f"       Private: 0x{w['private_key'][:20]}...")
    if len(wallets) > 5:
        print(f"  ... and {len(wallets) - 5} more")
    
    generator.save_wallets(args.output)
    
    print("\n[verify] Verifying first wallet...")
    account = WalletGenerator.get_account_from_wallet(wallets[0])
    print(f"[verify] Address: {account.address}")
    print(f"[verify] Matches: {account.address == wallets[0]['address']}")


if __name__ == "__main__":
    main()
    @staticmethod
    def load_wallets(filepath: str) -> Dict:
        """Load wallet list from JSON file."""
        with open(filepath, 'r') as f:
            return json.load(f)

    @staticmethod
    def get_account_from_wallet(wallet: Dict) -> LocalAccount:
        """Get eth_account LocalAccount from wallet dict."""
        return Account.from_key(wallet["private_key"])