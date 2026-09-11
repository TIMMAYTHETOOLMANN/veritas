#!/usr/bin/env python3
"""Add more debug to V3 quote adapter."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Add debug after each RPC call
old = '''            # Use the working RPC for all subsequent calls
            liquidity = working_rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
            liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0

            token0 = working_rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
            token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""'''

new = '''            # Use the working RPC for all subsequent calls
            try:
                liquidity = working_rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
                liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0
            except Exception as e:
                print(f"[QUOTE]   liquidity call failed: {str(e)[:60]}", flush=True)
                liquidity_val = 0

            try:
                token0 = working_rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
                token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""
            except Exception as e:
                print(f"[QUOTE]   token0 call failed: {str(e)[:60]}", flush=True)
                token0_addr = ""'''

content = content.replace(old, new)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Added more debug to V3 quote adapter")
