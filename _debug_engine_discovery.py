#!/usr/bin/env python3
"""Debug: trace discovery in the engine."""
import pathlib

# Read the engine and add debug output
content = pathlib.Path('veritas_engine.py').read_text()

# Find the discovery section and add debug prints
old_discovery = '''            if needs_discovery:
                # Discover pools with RPC failover
                discovered = []
                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}
                healthy_urls = self.rpc_health.get_healthy_urls()'''

new_discovery = '''            if needs_discovery:
                # Discover pools with RPC failover
                discovered = []
                discovery_stats = {"rate_limits": 0, "rpc_errors": 0}
                healthy_urls = self.rpc_health.get_healthy_urls()
                print(f"[DEBUG] Discovery needed. Healthy URLs: {healthy_urls}", flush=True)'''

content = content.replace(old_discovery, new_discovery)

# Add debug after each discovery attempt
old_attempt = '''                    discovery_stats = self.market_discovery.stats()
                    if discovered:
                        break'''

new_attempt = '''                    discovery_stats = self.market_discovery.stats()
                    print(f"[DEBUG] Tried {url}: discovered={len(discovered)}, stats={discovery_stats}", flush=True)
                    if discovered:
                        break'''

content = content.replace(old_attempt, new_attempt)

pathlib.Path('veritas_engine.py').write_text(content)
print("Added debug output to discovery")
