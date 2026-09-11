#!/usr/bin/env python3
"""Update QuoteEngine to accept health monitor."""
import pathlib

content = pathlib.Path('core/quote_engine_v2.py').read_text()

# Update V3QuoteAdapter to accept health monitor
old_init = '''class V3QuoteAdapter:
    """V3-style quote adapter using pool slot0() for price."""

    def __init__(self, rpc: RPC):
        self.rpc = rpc'''

new_init = '''class V3QuoteAdapter:
    """V3-style quote adapter using pool slot0() for price."""

    def __init__(self, rpc: RPC, health_monitor=None):
        self.rpc = rpc
        self.health_monitor = health_monitor'''

content = content.replace(old_init, new_init)

# Update QuoteEngine to accept health monitor
old_engine = '''class QuoteEngine:
    """Unified quote engine."""

    def __init__(self, rpc: RPC):
        self.rpc = rpc
        self.v2_adapter = V2QuoteAdapter(rpc)
        self.v3_adapter = V3QuoteAdapter(rpc)'''

new_engine = '''class QuoteEngine:
    """Unified quote engine."""

    def __init__(self, rpc: RPC, health_monitor=None):
        self.rpc = rpc
        self.health_monitor = health_monitor
        self.v2_adapter = V2QuoteAdapter(rpc)
        self.v3_adapter = V3QuoteAdapter(rpc, health_monitor)'''

content = content.replace(old_engine, new_engine)

pathlib.Path('core/quote_engine_v2.py').write_text(content)
print("Updated QuoteEngine to accept health monitor")
