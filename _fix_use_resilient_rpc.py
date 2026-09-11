#!/usr/bin/env python3
"""Update V3 quote adapter to use ResilientRPC."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Update imports
old_import = '''from core.pool import PoolId, PoolMetadata
from core.rpc import RPC
from core.error_taxonomy import ErrorTaxonomy'''

new_import = '''from core.pool import PoolId, PoolMetadata
from core.rpc import RPC
from core.rpc_resilience import ResilientRPC
from core.error_taxonomy import ErrorTaxonomy'''

content = content.replace(old_import, new_import)

# Update V3QuoteAdapter to accept ResilientRPC
old_init = '''class V3QuoteAdapter:
    """V3-style quote adapter using pool slot0() for price."""

    def __init__(self, rpc: RPC, health_monitor=None):
        self.rpc = rpc
        self.health_monitor = health_monitor'''

new_init = '''class V3QuoteAdapter:
    """V3-style quote adapter using pool slot0() for price."""

    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc'''

content = content.replace(old_init, new_init)

# Update QuoteEngine to accept ResilientRPC
old_engine = '''class QuoteEngine:
    """Unified quote engine."""

    def __init__(self, rpc: RPC, health_monitor=None):
        self.rpc = rpc
        self.health_monitor = health_monitor
        self.v2_adapter = V2QuoteAdapter(rpc)
        self.v3_adapter = V3QuoteAdapter(rpc, health_monitor)'''

new_engine = '''class QuoteEngine:
    """Unified quote engine."""

    def __init__(self, rpc: RPC, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc
        self.v2_adapter = V2QuoteAdapter(rpc)
        self.v3_adapter = V3QuoteAdapter(rpc, resilient_rpc)'''

content = content.replace(old_engine, new_engine)

# Update the quote method to use resilient_rpc
old_quote = '''        try:
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

new_quote = '''        try:
            # Use resilient RPC for all calls (with automatic failover)
            rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
            
            # Get slot0
            slot0 = rpc.eth_call(pool.pool_id.pool_address, "0x3850c7bd")
            if not slot0 or len(slot0) < 66:
                result.error = "failed to get slot0"
                return result

            # Get liquidity
            liquidity = rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")
            liquidity_val = int(liquidity[2:66], 16) if liquidity and len(liquidity) >= 66 else 0

            # Get token0
            token0 = rpc.eth_call(pool.pool_id.pool_address, "0x0dfe1681")
            token0_addr = "0x" + token0[2:][-40:].lower() if token0 and len(token0) >= 66 else ""'''

content = content.replace(old_quote, new_quote)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Updated V3 quote adapter to use ResilientRPC")
