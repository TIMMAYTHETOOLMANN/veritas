#!/usr/bin/env python3
"""Expand discovery to all venues and fee tiers."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Update discovery call to use all venues and fee tiers
old_discovery = '''                    discovered = self.market_discovery.discover_and_refresh(
                        self._hot_tokens[:2],  # Just WETH and USDC for speed
                        venues=["uniswap_v3"],
                        fee_tiers=[500],  # Just 0.05% tier
                    )'''

new_discovery = '''                    discovered = self.market_discovery.discover_and_refresh(
                        self._hot_tokens,  # All hot tokens
                        venues=["uniswap_v3", "sushi_v3", "camelot", "uniswap_v2", "sushi"],
                        fee_tiers=[100, 500, 3000, 10000],  # All fee tiers
                    )'''

content = content.replace(old_discovery, new_discovery)

# Update the hot tokens list to include more tokens
old_tokens = '''CORE_TOKENS = [
    "0x82af49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",  # USDC.e
    "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  # WBTC
    "0x912CE59144191C1204E64559FE8253a0e49E6548",  # ARB
]'''

new_tokens = '''CORE_TOKENS = [
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
    "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",  # USDC.e
    "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  # WBTC
    "0x912CE59144191C1204E64559FE8253a0e49E6548",  # ARB
    "0xf97f4df75117a78c1A5a0dbb814aF92458539FB4",  # LINK
    "0xFa7F8980b0f1E64a2062791cc3b0871572f1F7f0",  # UNI
]'''

content = content.replace(old_tokens, new_tokens)

pathlib.Path('veritas_engine.py').write_text(content)
print("Expanded discovery to all venues and fee tiers")
