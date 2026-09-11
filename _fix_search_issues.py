#!/usr/bin/env python3
"""Fix 50-route choke point and discovery threshold."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Fix 1: Remove the global 50-route choke point
# Replace the route iteration limit
old_routes = '''            # Phase 3: Evaluate routes
            for route in routes[:self.config["max_routes_per_token"]]:'''

new_routes = '''            # Phase 3: Evaluate routes (quote ALL generated routes)
            for route in routes:'''

content = content.replace(old_routes, new_routes)

# Fix 2: Update discovery to not stop after 2 pools
# Change the discovery condition
old_discovery = '''            # Phase 1: Discovery -- query chain for pools
            # Run discovery if no pools have actual reserves
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = len(pools_with_reserves) < 2'''

new_discovery = '''            # Phase 1: Discovery -- query chain for pools
            # Run discovery on first scan or if registry is stale
            # Discovery runs every N scans to refresh market state
            pools_with_reserves = self.pool_registry.get_pools_with_reserves()
            needs_discovery = (
                self._cycle_count == 1 or  # First scan
                len(pools_with_reserves) < 10 or  # Too few pools
                self._cycle_count % 10 == 0  # Refresh every 10 scans
            )'''

content = content.replace(old_discovery, new_discovery)

# Fix 3: Increase max_routes_per_token default
content = content.replace(
    '"max_routes_per_token": 50,',
    '"max_routes_per_token": 500,'
)

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed search issues")
