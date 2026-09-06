#!/usr/bin/env python3
"""
scanner_v3.py - Uniswap V3 arbitrage scanner using web3.py for ABI encoding.
"""
import time
from typing import Dict, List, Optional

from web3 import Web3

RPC_URL = "https://arb1.arbitrum.io/rpc"
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
FEE_TIERS = [500, 3000, 10000]

WETH = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDC_E = "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8"
WBTC = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
ARB = "0x912CE59144191C1204E64559FE8253a0e49E6548"
LINK = "0xf97f4df75117a78c1A5a0dbb814aF92458539FB4"

TOKENS = {
    WETH: {"symbol": "WETH", "decimals": 18, "price_usd": 2500.0},
    USDC: {"symbol": "USDC", "decimals": 6, "price_usd": 1.0},
    USDC_E: {"symbol": "USDC.e", "decimals": 6, "price_usd": 1.0},
    WBTC: {"symbol": "WBTC", "decimals": 8, "price_usd": 95000.0},
    ARB: {"symbol": "ARB", "decimals": 18, "price_usd": 1.8},
    LINK: {"symbol": "LINK", "decimals": 18, "price_usd": 12.0},
}

QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"}
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]


class V3Scanner:
    def __init__(self, rpc_url: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.quoter = self.w3.eth.contract(address=QUOTER_V2, abi=QUOTER_ABI)
        self.factory = self.w3.eth.contract(address=FACTORY, abi=FACTORY_ABI)
        self.pool_cache: Dict[str, str] = {}

    def get_pool(self, token_a: str, token_b: str, fee: int) -> Optional[str]:
        cache_key = f"{token_a.lower()}_{token_b.lower()}_{fee}"
        if cache_key in self.pool_cache:
            return self.pool_cache[cache_key]
        try:
            pool = self.factory.functions.getPool(
                self.w3.to_checksum_address(token_a),
                self.w3.to_checksum_address(token_b),
                fee
            ).call()
            if pool and pool != "0x0000000000000000000000000000000000000000":
                self.pool_cache[cache_key] = pool
                return pool
        except:
            pass
        return None

    def quote(self, token_in: str, token_out: str, amount_in: int, fee: int) -> Optional[int]:
        try:
            params = (
                self.w3.to_checksum_address(token_in),
                self.w3.to_checksum_address(token_out),
                fee,
                amount_in,
                0
            )
            result = self.quoter.functions.quoteExactInputSingle(params).call()
            amount_out = result[0]
            return amount_out if amount_out > 0 else None
        except Exception as e:
            return None

    def scan(self, trade_size_weth: float = 1.0) -> List[Dict]:
        edges = []
        size_wei = int(trade_size_weth * 1e18)

        for token_addr, token_info in TOKENS.items():
            if token_addr == WETH:
                continue

            buy_quotes = {}
            sell_quotes = {}

            for fee in FEE_TIERS:
                amount_out = self.quote(WETH, token_addr, size_wei, fee)
                if amount_out:
                    buy_quotes[fee] = amount_out

                token_amount = int(size_wei * token_info["price_usd"] / TOKENS[WETH]["price_usd"])
                if token_info["decimals"] == 6:
                    token_amount = int(token_amount / 1e12)
                amount_back = self.quote(token_addr, WETH, token_amount, fee)
                if amount_back:
                    sell_quotes[fee] = amount_back

            for buy_fee, buy_amount in buy_quotes.items():
                for sell_fee, sell_amount in sell_quotes.items():
                    if buy_fee == sell_fee:
                        continue

                    if sell_amount > size_wei:
                        profit_wei = sell_amount - size_wei
                        profit_eth = profit_wei / 1e18
                        profit_usd = profit_eth * TOKENS[WETH]["price_usd"]
                        aave_fee = trade_size_weth * TOKENS[WETH]["price_usd"] * 0.0005
                        gas_usd = 0.01
                        net_profit = profit_usd - aave_fee - gas_usd

                        if net_profit > 0.01:
                            edges.append({
                                "token": token_info["symbol"],
                                "token_addr": token_addr,
                                "buy_fee": buy_fee,
                                "sell_fee": sell_fee,
                                "size_weth": trade_size_weth,
                                "gross_profit_usd": profit_usd,
                                "net_profit_usd": net_profit,
                                "timestamp": time.time(),
                            })

        edges.sort(key=lambda e: e["net_profit_usd"], reverse=True)
        return edges


def main():
    print("=" * 60)
    print("V3 Arbitrage Scanner - Uniswap V3 on Arbitrum")
    print("=" * 60)

    scanner = V3Scanner(RPC_URL)
    print(f"Connected. Block: {scanner.w3.eth.block_number}")
    print(f"Scanning {len(TOKENS)} tokens across {len(FEE_TIERS)} fee tiers...")

    start = time.time()
    edges = scanner.scan(trade_size_weth=1.0)
    elapsed = time.time() - start

    print(f"Scan complete in {elapsed:.1f}s")
    print(f"Edges found: {len(edges)}")

    if edges:
        print("\nTop 5 opportunities:")
        print("-" * 60)
        for i, edge in enumerate(edges[:5], 1):
            print(f"{i}. {edge['token']} | Buy:{edge['buy_fee']} Sell:{edge['sell_fee']}")
            print(f"   Net profit: ${edge['net_profit_usd']:.2f}")
    else:
        print("\nNo profitable opportunities found.")


if __name__ == "__main__":
    main()