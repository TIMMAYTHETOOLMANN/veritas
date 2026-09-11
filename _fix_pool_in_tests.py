#!/usr/bin/env python3
"""Remove pool=PoolId(...) from test file since pool check was removed from gate."""
import pathlib
import re

p = pathlib.Path('tests/test_veritas_overhaul.py')
content = p.read_text()

# Remove blocks like:
#             pool=PoolId(
#                 chain_id=42161, venue="uniswap_v2",
#                 factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
#                 pool_address="0x1234567890abcdef1234567890abcdef12345678"
#             ),
pattern = r'\n\s+pool=PoolId\(\n\s+chain_id=42161,\s+venue="uniswap_v2",\n\s+factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",\n\s+pool_address="0x1234567890abcdef1234567890abcdef12345678"\n\s+\),'
content = re.sub(pattern, '', content)

# Also remove the PoolId import if it was added
content = content.replace('        from core.pool import PoolId\n', '')

p.write_text(content)
print("Removed pool=PoolId blocks from test file")
