#!/usr/bin/env python3
"""
core/market_discovery.py — VERITAS on-chain market discovery.

Discovers pools by querying factory contracts on-chain.
Supports both V2-style (getPair) and V3-style (getPool) factories.

This is the bridge between "the chain" and "the registry."
Without this, the registry is just a database cache.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from core.pool import PoolId, PoolMetadata, PoolRegistry
from core.rpc import RPC
from core.rpc_resilience import ResilientRPC


# Known factory addresses on Arbitrum
FACTORIES = {
    # V2-style factories (getPair)
    "uniswap_v2": {
        "address": "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
        "kind": "v2",
        "selector": "0c49b248",  # getPair(address,address)
    },
    "sushi": {
        "address": "0xc35dadb65012ec5796536bd9864ed8773abc74c4",
        "kind": "v2",
        "selector": "0c49b248",
    },
    "camelot": {
        "address": "0x6eccab422d763ac031210895c81787e87b43a652",
        "kind": "v2",
        "selector": "0c49b248",
    },
    # V3-style factories (getPool)
    "uniswap_v3": {
        "address": "0x1f98431c8ad98523631ae4a59f267346ea31f984",
        "kind": "v3",
        "selector": "1698ee82",  # getPool(address,address,uint24)
    },
    "sushi_v3": {
        "address": "0x1af415a1eba07a4986a52b6f2e7de7003d82231e",
        "kind": "v3",
        "selector": "1698ee82",
    },
    "pancake_v3": {
        "address": "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865",
        "kind": "v3",
        "selector": "1698ee82",
    },
    "ramses": {
        "address": "0xaa2cd7477c451e703f3b9ba5663334914763edf8",
        "kind": "v3",
        "selector": "1698ee82",
    },
}

# V3 fee tiers to query
V3_FEE_TIERS = [100, 500, 3000, 10000]

# Token decimals (known)
TOKEN_DECIMALS = {
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": 18,  # WETH
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": 6,   # USDC
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": 6,   # USDC.e
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": 6,   # USDT
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": 8,   # WBTC
    "0x912ce59144191c1204e64559fe8253a0e49e6548": 18,  # ARB
}


def _pad_addr(addr: str) -> str:
    """Pad address to 32 bytes for ABI encoding."""
    return addr.lower().replace("0x", "").rjust(64, "0")


def _u256(value: int) -> str:
    """Encode uint256 as 32-byte hex."""
    return f"{int(value):064x}"


def _parse_pool_addr(result: str) -> Optional[str]:
    """Parse pool address from eth_call result."""
    if not result or len(result) < 66:
        return None
    tail = result[2:][-40:]
    if set(tail) == {"0"}:
        return None
    return "0x" + tail.lower()


# Rate limit error patterns
_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "usage limit",
    "too many requests",
    "429",
    "throttled",
    "quota exceeded",
]


def _is_rate_limit_error(error: Exception) -> bool:
    """Check if an error is a rate limit error."""
    msg = str(error).lower()
    return any(pattern in msg for pattern in _RATE_LIMIT_PATTERNS)


class MarketDiscovery:
    """
    On-chain market discovery for VERITAS.

    Discovers pools by querying factory contracts directly.
    This ensures the registry reflects current chain state.

    Usage:
        discovery = MarketDiscovery(rpc, registry)
        pools = discovery.discover_pools(tokens, venues=["uniswap_v3", "sushi"])
    """

    def __init__(self, rpc: RPC, registry: PoolRegistry, chain_id: int = 42161, resilient_rpc: ResilientRPC = None):
        self.rpc = rpc
        self.resilient_rpc = resilient_rpc
        self.registry = registry
        self.chain_id = chain_id
        self._stats = {
            "pairs_queried": 0,
            "pools_found": 0,
            "pools_registered": 0,
            "rpc_errors": 0,
            "rate_limits": 0,
        }

    def discover_pools(
        self,
        tokens: List[str],
        venues: Optional[List[str]] = None,
        fee_tiers: Optional[List[int]] = None,
    ) -> List[PoolMetadata]:
        """
        Discover pools for given tokens across specified venues.

        Args:
            tokens: List of token addresses to discover pools for
            venues: List of venue names (None = all)
            fee_tiers: V3 fee tiers to query (None = default)

        Returns:
            List of newly discovered PoolMetadata objects
        """
        venues = venues or list(FACTORIES.keys())
        fee_tiers = fee_tiers or V3_FEE_TIERS
        discovered: List[PoolMetadata] = []
        block = self._safe_block()

        for venue_name in venues:
            factory = FACTORIES.get(venue_name)
            if not factory:
                continue

            for i, token_a in enumerate(tokens):
                for token_b in tokens[i + 1:]:
                    if factory["kind"] == "v3":
                        # V3: query each fee tier
                        for fee in fee_tiers:
                            pool = self._query_v3_pool(
                                venue_name, factory["address"],
                                token_a, token_b, fee, block
                            )
                            if pool:
                                discovered.append(pool)
                    else:
                        # V2: single query per pair
                        pool = self._query_v2_pool(
                            venue_name, factory["address"],
                            token_a, token_b, block
                        )
                        if pool:
                            discovered.append(pool)

        self._stats["pools_found"] += len(discovered)
        return discovered

    def _query_v3_pool(
        self, venue: str, factory: str,
        token_a: str, token_b: str, fee: int, block: int
    ) -> Optional[PoolMetadata]:
        """Query a V3 factory for a specific pool."""
        self._stats["pairs_queried"] += 1

        try:
            # Build getPool(address,address,uint24) call
            data = (
                "0x" + FACTORIES[venue]["selector"]
                + _pad_addr(token_a) + _pad_addr(token_b) + _u256(fee)
            )
            rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
            result = rpc.eth_call(factory, data)
            pool_addr = _parse_pool_addr(result)

            if not pool_addr:
                return None

            # Check if already registered
            existing = self.registry.get_by_address(self.chain_id, pool_addr)
            if existing:
                return None

            # Register new pool
            decimals_a = TOKEN_DECIMALS.get(token_a.lower(), 18)
            decimals_b = TOKEN_DECIMALS.get(token_b.lower(), 18)

            pool_id = PoolId(
                chain_id=self.chain_id,
                venue=venue,
                factory=factory,
                pool_address=pool_addr,
            )
            pool = PoolMetadata(
                pool_id=pool_id,
                token0=token_a,
                token1=token_b,
                decimals0=decimals_a,
                decimals1=decimals_b,
                fee=fee,
                kind="v3",
                is_live=True,
                last_updated_block=block,
                last_updated_timestamp=int(time.time()),
            )
            self.registry.register(pool)
            self._stats["pools_registered"] += 1
            return pool

        except Exception as e:
            self._stats["rpc_errors"] += 1
            if _is_rate_limit_error(e):
                self._stats["rate_limits"] += 1
            return None

    def _query_v2_pool(
        self, venue: str, factory: str,
        token_a: str, token_b: str, block: int
    ) -> Optional[PoolMetadata]:
        """Query a V2 factory for a specific pair."""
        self._stats["pairs_queried"] += 1

        try:
            # Build getPair(address,address) call
            data = (
                "0x" + FACTORIES[venue]["selector"]
                + _pad_addr(token_a) + _pad_addr(token_b)
            )
            rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
            result = rpc.eth_call(factory, data)
            pool_addr = _parse_pool_addr(result)

            if not pool_addr:
                return None

            # Check if already registered
            existing = self.registry.get_by_address(self.chain_id, pool_addr)
            if existing:
                return None

            # Register new pool
            decimals_a = TOKEN_DECIMALS.get(token_a.lower(), 18)
            decimals_b = TOKEN_DECIMALS.get(token_b.lower(), 18)

            pool_id = PoolId(
                chain_id=self.chain_id,
                venue=venue,
                factory=factory,
                pool_address=pool_addr,
            )
            pool = PoolMetadata(
                pool_id=pool_id,
                token0=token_a,
                token1=token_b,
                decimals0=decimals_a,
                decimals1=decimals_b,
                fee=3000,  # V2 default
                kind="v2",
                is_live=True,
                last_updated_block=block,
                last_updated_timestamp=int(time.time()),
            )
            self.registry.register(pool)
            self._stats["pools_registered"] += 1
            return pool

        except Exception as e:
            self._stats["rpc_errors"] += 1
            if _is_rate_limit_error(e):
                self._stats["rate_limits"] += 1
            return None

    def _safe_block(self) -> int:
        """Get current block number safely."""
        try:
            return self.rpc.eth_blockNumber()
        except Exception:
            return 0

    def fetch_reserves(self, pool: PoolMetadata) -> bool:
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
                rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
                result = rpc.eth_call(pool.pool_id.pool_address, "0x1a686502")  # liquidity()
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
                rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
                result = rpc.eth_call(pool.pool_id.pool_address, "0x0902f1ac")  # getReserves()
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

    def _query_pool_address(self, venue: str, factory_info: dict,
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
                    rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
                    result = rpc.eth_call(factory_info["address"], data)
                    addr = _parse_pool_addr(result)
                    if addr:
                        return addr
            else:
                data = (
                    "0x" + factory_info["selector"]
                    + _pad_addr(token_a) + _pad_addr(token_b)
                )
                rpc = self.resilient_rpc if self.resilient_rpc else self.rpc
                result = rpc.eth_call(factory_info["address"], data)
                return _parse_pool_addr(result)
        except Exception:
            pass
        return None

    def discover_and_refresh(self, tokens: List[str], venues: Optional[List[str]] = None,
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
        return all_pools

    def stats(self) -> dict:
        """Return discovery statistics."""
        return dict(self._stats)
