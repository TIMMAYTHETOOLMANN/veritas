#!/usr/bin/env python3
"""Fix Alchemy priority in config."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace the rpc_urls config to put Alchemy first
old_urls = '''    "rpc_urls": [
        AlchemyRPC().url,
        "https://arbitrum.drpc.org",
        "https://arbitrum.publicnode.com",
    ],'''

new_urls = '''    "rpc_urls": [
        "https://arb-mainnet.g.alchemy.com/v2/alch_VNgR_d3fLq-3WDpDb7_Ol",
        "https://gateway.tenderly.co/public/arbitrum",
        "https://arbitrum.drpc.org",
    ],'''

content = content.replace(old_urls, new_urls)

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed Alchemy priority in config")
