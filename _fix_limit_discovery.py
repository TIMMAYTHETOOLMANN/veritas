#!/usr/bin/env python3
"""Limit discovery scope for faster scanning."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace the venues list with a smaller set
old_venues = '                        venues=["uniswap_v3", "sushi", "camelot", "uniswap_v2"],'
new_venues = '                        venues=["uniswap_v3"],'

content = content.replace(old_venues, new_venues)

pathlib.Path('veritas_engine.py').write_text(content)
print("Limited discovery to uniswap_v3 only")
