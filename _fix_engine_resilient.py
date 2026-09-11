#!/usr/bin/env python3
"""Update engine to use ResilientRPC."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Add import for ResilientRPC
content = content.replace(
    'from core.rpc import RPC',
    'from core.rpc import RPC\nfrom core.rpc_resilience import ResilientRPC, RPCCache'
)

# Update initialization to create ResilientRPC
old_init = '''        self.rpc_health = RPCHealthMonitor(self.config["rpc_urls"])'''

new_init = '''        self.rpc_health = RPCHealthMonitor(self.config["rpc_urls"])
        self.resilient_rpc = ResilientRPC(
            [(url, i) for i, url in enumerate(self.config["rpc_urls"])],
            cache=RPCCache()
        )'''

content = content.replace(old_init, new_init)

# Update quote engine initialization
old_quote = '''            # Update quote engine with current RPC and health monitor
            self.quote_engine = QuoteEngine(rpc, self.rpc_health)'''

new_quote = '''            # Update quote engine with resilient RPC
            self.quote_engine = QuoteEngine(rpc, self.resilient_rpc)'''

content = content.replace(old_quote, new_quote)

pathlib.Path('veritas_engine.py').write_text(content)
print("Updated engine to use ResilientRPC")
