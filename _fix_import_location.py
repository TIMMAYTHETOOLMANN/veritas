#!/usr/bin/env python3
"""Fix import location in veritas_engine.py."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Remove the misplaced import
content = content.replace(
    '                    from core.rpc import RPC\nfrom core.external_apis import AlchemyRPC, CryptoArbitrageAPI, DeFiLlamaAPI\n',
    '                    from core.rpc import RPC\n'
)

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed import location")
