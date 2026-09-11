#!/usr/bin/env python3
"""Fix V3 quote adapter to use failover RPC for all calls."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Replace the quote method to use failover RPC for all calls
old_method = '''        print(f"[QUOTE] Trying {len(rpcs_to_try)} RPCs for {token_in[:6]}->{token_out[:6]}", flush=True)
        
        try:
            # Get slot0 from pool (try multiple RPCs if needed)
            slot0 = None
            for i, rpc in enumerate(rpcs_to_try):
                try:
                    print(f"[QUOTE]   RPC {i}: {rpc.url[:40]}...", flush=True)
                    slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
                    if slot0 and len(slot0) >= 66:
                        print(f"[QUOTE]   SUCCESS!", flush=True)
                        break
                    else:
                        print(f"[QUOTE]   Failed: short result", flush=True)
                except Exception as e:
                    print(f"[QUOTE]   Error: {str(e)[:60]}", flush=True)
                    continue

            if not slot0 or len(slot0) < 66:
                result.error = "failed to get slot0"
                return result

            sqrt_price_x96 = int(slot0[2:66], 16)
            if sqrt_price_x96 <= 0:
                result.error = "invalid sqrtPriceX96"
                return result

            # Get pool state
            liquidity = self.rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
            liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0

            # Get token order
            token0 = self.rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
            token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""'''

new_method = '''        try:
            # Get all pool data from a single RPC (with failover)
            slot0 = None
            liquidity = None
            token0 = None
            working_rpc = self.rpc
            
            for rpc in rpcs_to_try:
                try:
                    slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
                    if slot0 and len(slot0) >= 66:
                        working_rpc = rpc
                        break
                except Exception:
                    continue

            if not slot0 or len(slot0) < 66:
                result.error = "failed to get slot0"
                return result

            # Use the working RPC for all subsequent calls
            liquidity = working_rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
            liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0

            token0 = working_rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
            token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""'''

content = content.replace(old_method, new_method)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Fixed V3 quote adapter to use failover RPC for all calls")
