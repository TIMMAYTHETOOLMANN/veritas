#!/usr/bin/env python3
"""Use minimal discovery for testing - 2 tokens, 1 fee tier."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace the discover_and_refresh call with minimal parameters
old_call = '''                    discovered = self.market_discovery.discover_and_refresh(
                        self._hot_tokens,
                        venues=["uniswap_v3"],
                    )'''

new_call = '''                    discovered = self.market_discovery.discover_and_refresh(
                        self._hot_tokens[:2],  # Just WETH and USDC for speed
                        venues=["uniswap_v3"],
                        fee_tiers=[500],  # Just 0.05% tier
                    )'''

content = content.replace(old_call, new_call)

pathlib.Path('veritas_engine.py').write_text(content)
print("Limited discovery to 2 tokens, 1 fee tier")
