#!/usr/bin/env python3
"""Fix: persist pool after fetch_reserves updates it."""
import pathlib

content = pathlib.Path('core/market_discovery.py').read_text()

# In discover_and_refresh, persist the pool after fetch_reserves
old_refresh = '''        # Then fetch reserves for each discovered pool
        # Reserve fetch failures are non-fatal - the pool is still returned
        # The route evaluation will check has_sufficient_liquidity()
        pools_with_reserves = 0
        for pool in discovered:
            if self.fetch_reserves(pool):
                pools_with_reserves += 1
            # Always include the pool, even if reserve fetch failed
            # (it may have been registered with zero reserves, which is correct)
        
        self._stats["pools_with_reserves"] = pools_with_reserves
        return discovered'''

new_refresh = '''        # Then fetch reserves for each discovered pool
        # Reserve fetch failures are non-fatal - the pool is still returned
        # The route evaluation will check has_sufficient_liquidity()
        pools_with_reserves = 0
        for pool in discovered:
            if self.fetch_reserves(pool):
                pools_with_reserves += 1
                # Persist the updated pool (with reserves) to the database
                self.registry.register(pool, persist=True)
            # Always include the pool, even if reserve fetch failed
        
        self._stats["pools_with_reserves"] = pools_with_reserves
        return discovered'''

content = content.replace(old_refresh, new_refresh)

pathlib.Path('core/market_discovery.py').write_text(content)
print("Fixed: persist pool after fetch_reserves")
