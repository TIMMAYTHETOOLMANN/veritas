#!/usr/bin/env python3
"""Add reserve fetching to MarketDiscovery."""
import pathlib

content = pathlib.Path('core/market_discovery.py').read_text()

# Add reserve fetching method to MarketDiscovery
old_stats = '''    def stats(self) -> dict:
        """Return discovery statistics."""
        return dict(self._stats)'''

new_stats = '''    def fetch_reserves(self, pool: PoolMetadata) -> bool:
        """
        Fetch reserves for a pool from-chain.
        
        Args:
            pool: The pool to fetch reserves for
            
        Returns:
            True if reserves were successfully fetched
        """
        try:
            if pool.kind == "v3":
                # V3: slot0() returns sqrtPriceX96, tick, observationIndex, etc.
                # and liquidity() returns the current liquidity
                result = self.rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")  # liquidity()
                if result and len(result) >= 66:
                    pool.liquidity = int(result[2:66], 16)
                
                # V3 doesn't have simple reserves, but we can use liquidity as proxy
                # For now, mark as having liquidity if liquidity > 0
                if pool.liquidity > 0:
                    pool.reserve1 = pool.liquidity  # Use liquidity as proxy
                    pool.reserve0 = 1  # Mark as having reserves
                    pool.is_live = True
                    return True
                    
            else:
                # V2: getReserves() returns (reserve0, reserve1, blockTimestampLast)
                result = self.rpc.eth_call(pool.pool_id.pool_address, "0x0902f1ac")  # getReserves()
                if result and len(result) >= 66:
                    reserve0 = int(result[2:66], 16)
                    reserve1 = int(result[66:130], 16) if len(result) >= 130 else 0
                    pool.reserve0 = reserve0
                    pool.reserve1 = reserve1
                    pool.is_live = reserve0 > 0 and reserve1 > 0
                    return pool.is_live
                    
        except Exception as e:
            self._stats["rpc_errors"] += 1
            if _is_rate_limit_error(e):
                self._stats["rate_limits"] += 1
        
        return False

    def discover_and_refresh(self, tokens: List[str], venues: Optional[List[str]] = None,
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
        
        return pools_with_reserves

    def stats(self) -> dict:
        """Return discovery statistics."""
        return dict(self._stats)'''

content = content.replace(old_stats, new_stats)

pathlib.Path('core/market_discovery.py').write_text(content)
print("Added reserve fetching to MarketDiscovery")
