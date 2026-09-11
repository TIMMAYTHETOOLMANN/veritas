#!/usr/bin/env python3
"""Fix RPC scope issue."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Fix the rpc scope issue
old = '''                    try:
                        rpc = self.resilient_rpc.get_healthy_rpc() if self.resilient_rpc else rpc
                        slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")'''

new = '''                    try:
                        if self.resilient_rpc:
                            rpc = self.resilient_rpc.get_healthy_rpc()
                        slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")'''

content = content.replace(old, new)

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed RPC scope")
