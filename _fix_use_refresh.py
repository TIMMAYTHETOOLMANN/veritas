#!/usr/bin/env python3
"""Update engine to use discover_and_refresh."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace discover_pools with discover_and_refresh
content = content.replace(
    '                    discovered = self.market_discovery.discover_pools(\n'
    '                        self._hot_tokens,\n'
    '                        venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],\n'
    '                    )',
    '                    discovered = self.market_discovery.discover_and_refresh(\n'
    '                        self._hot_tokens,\n'
    '                        venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],\n'
    '                    )'
)

pathlib.Path('veritas_engine.py').write_text(content)
print("Updated engine to use discover_and_refresh")
