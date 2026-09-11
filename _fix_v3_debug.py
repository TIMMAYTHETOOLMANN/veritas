#!/usr/bin/env python3
"""Add debug to V3 quote adapter."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Add debug to the failover loop
old = '''        # Try with current RPC, then failover to health monitor
        rpcs_to_try = [self.rpc]
        if self.health_monitor:
            for url in self.health_monitor.get_healthy_urls():
                from core.rpc import RPC
                rpcs_to_try.append(RPC(url, timeout=30, retries=1))
        
        try:
            # Get slot0 from pool (try multiple RPCs if needed)
            slot0 = None
            for rpc in rpcs_to_try:
                try:
                    slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
                    if slot0 and len(slot0) >= 66:
                        break
                except Exception:
                    continue'''

new = '''        # Try with current RPC, then failover to health monitor
        rpcs_to_try = [self.rpc]
        if self.health_monitor:
            for url in self.health_monitor.get_healthy_urls():
                from core.rpc import RPC
                rpcs_to_try.append(RPC(url, timeout=30, retries=1))
        
        print(f"[QUOTE] Trying {len(rpcs_to_try)} RPCs for {token_in[:6]}->{token_out[:6]}", flush=True)
        
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
                    continue'''

content = content.replace(old, new)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Added debug to V3 quote adapter")
