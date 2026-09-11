#!/usr/bin/env python3
"""Fix discover_and_refresh to return all discovered pools."""
import pathlib

content = pathlib.Path('core/market_discovery.py').read_text()

# Replace the discover_and_refresh method
old_method = '''    def discover_and_refresh(self, tokens: List[str], venues: Optional[List[str]] = None,
                            fee_tiers: Optional[List[int]] = None) -> List[PoolMetadata]:
        """
        Discover pools AND fetch their reserves.
        
        Returns:
            List of pools with fresh reserve data
        """
        # First discover pool addresses
        discovered = self.discover_pools(tokens, venues, fee_tiers)
        
        # Then fetch reserves for each discovered pool
        pools_with_reserves = []
        for pool in discovered:
            if self.fetch_reserves(pool):
                pools_with_reserves.append(pool)
                self._stats["pools_with_reserves"] = self._stats.get("pools_with_reserves", 0) + 1
        
        return pools_with_reserves'''

new_method = '''    def discover_and_refresh(self, tokens: List[str], venues: Optional[List[str]] = None,
                            fee_tiers: Optional[List[int]] = None) -> List[PoolMetadata]:
        """
        Discover pools AND fetch their reserves.
        
        Returns:
            List of all discovered pools (reserve fetch failures are non-fatal)
        """
        # First discover pool addresses
        discovered = self.discover_pools(tokens, venues, fee_tiers)
        
        # Then fetch reserves for each discovered pool
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

content = content.replace(old_method, new_method)

pathlib.Path('core/market_discovery.py').write_text(content)
print("Fixed discover_and_refresh to return all discovered pools")
