#!/usr/bin/env python3
"""Update quote engine to use RPC health monitor for failover."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Update the quote engine initialization to use health monitor
old = '''            # Update quote engine with current RPC
            self.quote_engine = QuoteEngine(rpc)
            self.price_oracle = PriceOracle(rpc)
            self.economic_model = EconomicModel(self.price_oracle)'''

new = '''            # Update quote engine with current RPC and health monitor
            self.quote_engine = QuoteEngine(rpc, self.rpc_health)
            self.price_oracle = PriceOracle(rpc)
            self.economic_model = EconomicModel(self.price_oracle)'''

content = content.replace(old, new)

pathlib.Path('veritas_engine.py').write_text(content)
print("Updated quote engine to use health monitor")
