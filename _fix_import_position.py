#!/usr/bin/env python3
"""Fix import position in veritas_engine.py."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Remove the misplaced import
content = content.replace(
    '                    from core.rpc import RPC\nfrom core.rpc_resilience import ResilientRPC, RPCCache\n',
    '                    from core.rpc import RPC\n'
)

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed import position")
