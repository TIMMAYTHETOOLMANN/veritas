#!/usr/bin/env python3
"""Fix: discover_and_refresh should also fetch reserves for existing pools with zero reserves."""
import pathlib

content = pathlib.Path('core/market_discovery.py').read_text()

# Replace discover_and_refresh to also handle existing pools
old_refresh = '''    def discover_and_refresh(self, tokens: List[str], venues: Optional[List[str]] = None,
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
                # Persist the updated pool (with reserves) to the database
                self.registry.register(pool, persist=True)
            # Always include the pool, even if reserve fetch failed
        
        self._stats["pools_with_reserves"] = pools_with_reserves
        return discovered'''

new_refresh = '''    def discover_and_refresh(self, tokens: List[str], venues: Optional[List[str]] = None,
                            fee_tiers: Optional[List[int]] = None) -> List[PoolMetadata]:
        """
        Discover pools AND fetch their reserves.
        
        Also fetches reserves for existing pools that have zero reserves.
        
        Returns:
            List of all discovered pools (reserve fetch failures are non-fatal)
        """
        # First discover pool addresses (new pools)
        discovered = self.discover_pools(tokens, venues, fee_tiers)
        
        # Also find existing pools with zero reserves that need refreshing
        existing_pools_needing_reserves = []
        if venues is None:
            venues = list(FACTORIES.keys())
        
        for venue_name in venues:
            factory_info = FACTORIES.get(venue_name)
            if not factory_info:
                continue
            for i, token_a in enumerate(tokens):
                for token_b in tokens[i + 1:]:
                    # Check if pool exists for this pair
                    pool_addr = self._query_pool_address(venue_name, factory_info, token_a, token_b, fee_tiers)
                    if pool_addr:
                        existing = self.registry.get_by_address(self.chain_id, pool_addr)
                        if existing and (existing.reserve0 == 0 and existing.reserve1 == 0):
                            existing_pools_needing_reserves.append(existing)
        
        # Fetch reserves for all pools (new + existing)
        all_pools = discovered + existing_pools_needing_reserves
        pools_with_reserves = 0
        for pool in all_pools:
            if self.fetch_reserves(pool):
                pools_with_reserves += 1
                # Persist the updated pool (with reserves) to the database
                self.registry.register(pool, persist=True)
        
        self._stats["pools_with_reserves"] = pools_with_reserves
        return all_pools'''

content = content.replace(old_refresh, new_refresh)

# Add the helper method before discover_and_refresh
helper_method = '''    def _query_pool_address(self, venue: str, factory_info: dict,
                            token_a: str, token_b: str, fee_tiers: Optional[List[int]] = None) -> Optional[str]:
        """Query factory for pool address (without registering)."""
        try:
            if factory_info["kind"] == "v3":
                fee_tiers = fee_tiers or V3_FEE_TIERS
                for fee in fee_tiers:
                    data = (
                        "0x" + factory_info["selector"]
                        + _pad_addr(token_a) + _pad_addr(token_b) + _u256(fee)
                    )
                    result = self.rpc.eth_call(factory_info["address"], data)
                    addr = _parse_pool_addr(result)
                    if addr:
                        return addr
            else:
                data = (
                    "0x" + factory_info["selector"]
                    + _pad_addr(token_a) + _pad_addr(token_b)
                )
                result = self.rpc.eth_call(factory_info["address"], data)
                return _parse_pool_addr(result)
        except Exception:
            pass
        return None

    def discover_and_refresh('''

content = content.replace(
    '    def discover_and_refresh(',
    helper_method
)

pathlib.Path('core/market_discovery.py').write_text(content)
print("Fixed: discover_and_refresh now also refreshes existing pools with zero reserves")
