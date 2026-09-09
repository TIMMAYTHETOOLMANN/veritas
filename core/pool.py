#!/usr/bin/env python3
"""
core/pool.py — VERITAS pool identity and metadata.

Every pool has a unique identity: (chain_id, venue, factory, pool_address).
Never identify a pool merely as (token_a, token_b).

This module provides:
  - PoolId: immutable unique pool identifier
  - PoolMetadata: full pool metadata including reserves, liquidity, fee
  - PoolRegistry: in-memory + persistent pool registry
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.db import conn, now


@dataclass(frozen=True)
class PoolId:
    """
    Immutable unique pool identity.

    A pool is uniquely identified by (chain_id, venue, factory, pool_address).
    Two venues containing the same token pair are NEVER the same pool.
    """
    chain_id: int
    venue: str
    factory: str
    pool_address: str

    def __str__(self) -> str:
        return f"{self.chain_id}:{self.venue}:{self.factory}:{self.pool_address}"

    def __hash__(self) -> int:
        return hash((self.chain_id, self.venue, self.factory, self.pool_address.lower()))

    def __eq__(self, other) -> bool:
        if not isinstance(other, PoolId):
            return False
        return (self.chain_id == other.chain_id
                and self.venue == other.venue
                and self.factory.lower() == other.factory.lower()
                and self.pool_address.lower() == other.pool_address.lower())


@dataclass
class PoolMetadata:
    """
    Full pool metadata for a discovered pool.

    Contains everything needed to evaluate and execute against this pool.
    """
    pool_id: PoolId
    token0: str
    token1: str
    decimals0: int = 18
    decimals1: int = 6
    fee: int = 3000          # fee tier in hundredths of a bip (e.g. 3000 = 0.3%)
    kind: str = "v2"         # "v2" or "v3"

    # Liquidity state
    reserve0: int = 0
    reserve1: int = 0
    liquidity: int = 0       # V3 liquidity (sqrt price range)

    # Block awareness
    last_updated_block: int = 0
    last_updated_timestamp: int = 0

    # Quality metrics
    usd_depth: float = 0.0
    is_live: bool = False

    # Historical tracking
    historical_edge_count: int = 0
    last_edge_timestamp: int = 0

    @property
    def token_pair(self) -> Tuple[str, str]:
        """Return (token0, tuple) — for display only, NOT for identity."""
        return (self.token0.lower(), self.token1.lower())

    @property
    def unique_key(self) -> str:
        """Unique string key for this pool."""
        return str(self.pool_id)

    def is_fresh(self, max_age_seconds: int = 180, current_block: int = 0, max_block_age: int = 50) -> bool:
        """Check if pool data is fresh enough to use."""
        age_seconds = int(time.time()) - self.last_updated_timestamp
        if age_seconds > max_age_seconds:
            return False
        if current_block > 0 and self.last_updated_block > 0:
            block_age = current_block - self.last_updated_block
            if block_age > max_block_age:
                return False
        return True

    def has_sufficient_liquidity(self, min_reserve_usd: float = 100.0) -> bool:
        """Check if pool has sufficient liquidity to consider.
        
        For V2 pools: check reserves (reserve0 > 0 AND reserve1 > 0)
        For V3 pools: check liquidity > 0
        """
        if self.kind == "v3":
            # V3 pools use liquidity, not reserves
            return self.liquidity > 0 or self.usd_depth >= min_reserve_usd
        else:
            # V2 pools use reserves
            has_reserves = self.reserve0 > 0 and self.reserve1 > 0
            has_depth = self.usd_depth >= min_reserve_usd
            return has_reserves or has_depth

    def to_dict(self) -> dict:
        return {
            "chain_id": self.pool_id.chain_id,
            "venue": self.pool_id.venue,
            "factory": self.pool_id.factory,
            "pool_address": self.pool_id.pool_address,
            "token0": self.token0,
            "token1": self.token1,
            "decimals0": self.decimals0,
            "decimals1": self.decimals1,
            "fee": self.fee,
            "kind": self.kind,
            "reserve0": self.reserve0,
            "reserve1": self.reserve1,
            "liquidity": self.liquidity,
            "last_updated_block": self.last_updated_block,
            "last_updated_timestamp": self.last_updated_timestamp,
            "usd_depth": self.usd_depth,
            "is_live": self.is_live,
        }


class PoolRegistry:
    """
    In-memory + persistent pool registry.

    Maintains a live registry of all discovered pools with their metadata.
    Provides fast lookups by pool identity, token pair, and venue.
    """

    def __init__(self):
        self._pools: Dict[str, PoolMetadata] = {}          # unique_key -> PoolMetadata
        self._by_token: Dict[str, List[str]] = {}          # token_addr -> [pool_keys]
        self._by_venue: Dict[str, List[str]] = {}          # venue -> [pool_keys]
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure pool registry tables exist in the database."""
        c = conn()
        try:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS veritas_pools (
                    pool_key TEXT PRIMARY KEY,
                    chain_id INTEGER NOT NULL,
                    venue TEXT NOT NULL,
                    factory TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    token0 TEXT NOT NULL,
                    token1 TEXT NOT NULL,
                    decimals0 INTEGER DEFAULT 18,
                    decimals1 INTEGER DEFAULT 6,
                    fee INTEGER DEFAULT 3000,
                    kind TEXT DEFAULT 'v2',
                    reserve0 TEXT DEFAULT '0',
                    reserve1 TEXT DEFAULT '0',
                    liquidity TEXT DEFAULT '0',
                    last_updated_block INTEGER DEFAULT 0,
                    last_updated_timestamp INTEGER DEFAULT 0,
                    usd_depth REAL DEFAULT 0,
                    is_live INTEGER DEFAULT 0,
                    historical_edge_count INTEGER DEFAULT 0,
                    last_edge_timestamp INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_veritas_pools_token0 ON veritas_pools(token0);
                CREATE INDEX IF NOT EXISTS idx_veritas_pools_token1 ON veritas_pools(token1);
                CREATE INDEX IF NOT EXISTS idx_veritas_pools_venue ON veritas_pools(venue);
                CREATE INDEX IF NOT EXISTS idx_veritas_pools_chain ON veritas_pools(chain_id);
            """)
            c.commit()
        finally:
            c.close()

    def register(self, pool: PoolMetadata, persist: bool = True) -> None:
        """Register or update a pool in the registry.
        
        Args:
            pool: The pool to register
            persist: If True (default), persist to database.
                     Set to False when loading from database to avoid write loop.
        """
        key = pool.unique_key
        self._pools[key] = pool

        # Index by token
        for token in [pool.token0.lower(), pool.token1.lower()]:
            if token not in self._by_token:
                self._by_token[token] = []
            if key not in self._by_token[token]:
                self._by_token[token].append(key)

        # Index by venue
        venue = pool.pool_id.venue
        if venue not in self._by_venue:
            self._by_venue[venue] = []
        if key not in self._by_venue[venue]:
            self._by_venue[venue].append(key)

        # Persist (skip when loading from DB to avoid write loop)
        if persist:
            self._persist(pool)

    def get(self, pool_id: PoolId) -> Optional[PoolMetadata]:
        """Get pool metadata by pool identity."""
        key = str(pool_id)
        return self._pools.get(key)

    def get_by_address(self, chain_id: int, pool_address: str) -> Optional[PoolMetadata]:
        """Find a pool by chain_id and pool address (searches all venues)."""
        addr_lower = pool_address.lower()
        for pool in self._pools.values():
            if (pool.pool_id.chain_id == chain_id
                    and pool.pool_id.pool_address.lower() == addr_lower):
                return pool
        return None

    def get_pools_for_token(self, token_addr: str) -> List[PoolMetadata]:
        """Get all pools containing a given token."""
        keys = self._by_token.get(token_addr.lower(), [])
        return [self._pools[k] for k in keys if k in self._pools]

    def get_pools_for_pair(self, token_a: str, token_b: str) -> List[PoolMetadata]:
        """Get all pools for a token pair across ALL venues."""
        a_keys = set(self._by_token.get(token_a.lower(), []))
        b_keys = set(self._by_token.get(token_b.lower(), []))
        common = a_keys & b_keys
        return [self._pools[k] for k in common if k in self._pools]

    def get_pools_for_pair_and_venue(
        self, token_a: str, token_b: str, venue: str
    ) -> List[PoolMetadata]:
        """Get pools for a specific token pair at a specific venue."""
        pair_pools = self.get_pools_for_pair(token_a, token_b)
        return [p for p in pair_pools if p.pool_id.venue == venue]

    def get_venue_pools(self, venue: str) -> List[PoolMetadata]:
        """Get all pools for a venue."""
        keys = self._by_venue.get(venue, [])
        return [self._pools[k] for k in keys if k in self._pools]

    def get_all_pools(self) -> List[PoolMetadata]:
        """Get all registered pools."""
        return list(self._pools.values())

    def get_live_pools(self, min_usd_depth: float = 100.0) -> List[PoolMetadata]:
        """Get all live pools with sufficient liquidity."""
        return [p for p in self._pools.values()
                if p.is_live and p.usd_depth >= min_usd_depth]

    def get_pools_with_reserves(self) -> List[PoolMetadata]:
        """Get pools that have non-zero reserves (actual on-chain liquidity)."""
        return [p for p in self._pools.values()
                if p.reserve0 > 0 and p.reserve1 > 0]

    def get_hot_pools(self, min_edge_count: int = 1) -> List[PoolMetadata]:
        """Get pools that have historically produced edges."""
        return [p for p in self._pools.values()
                if p.historical_edge_count >= min_edge_count]

    def mark_edge(self, pool_id: PoolId) -> None:
        """Record that a pool produced an edge."""
        pool = self.get(pool_id)
        if pool:
            pool.historical_edge_count += 1
            pool.last_edge_timestamp = now()
            self._persist(pool)

    def count(self) -> int:
        return len(self._pools)

    def _persist(self, pool: PoolMetadata) -> None:
        """Persist pool to database."""
        c = conn()
        try:
            c.execute("""
                INSERT OR REPLACE INTO veritas_pools (
                    pool_key, chain_id, venue, factory, pool_address,
                    token0, token1, decimals0, decimals1, fee, kind,
                    reserve0, reserve1, liquidity, last_updated_block,
                    last_updated_timestamp, usd_depth, is_live,
                    historical_edge_count, last_edge_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pool.unique_key, pool.pool_id.chain_id, pool.pool_id.venue,
                pool.pool_id.factory, pool.pool_id.pool_address,
                pool.token0, pool.token1, pool.decimals0, pool.decimals1,
                pool.fee, pool.kind,
                str(pool.reserve0), str(pool.reserve1), str(pool.liquidity),
                pool.last_updated_block, pool.last_updated_timestamp,
                pool.usd_depth, int(pool.is_live),
                pool.historical_edge_count, pool.last_edge_timestamp,
            ))
            c.commit()
        finally:
            c.close()

    def load_from_db(self, chain_id: Optional[int] = None) -> int:
        """Load pools from database into memory."""
        c = conn()
        try:
            if chain_id is not None:
                rows = c.execute(
                    "SELECT * FROM veritas_pools WHERE chain_id = ?", (chain_id,)
                ).fetchall()
            else:
                rows = c.execute("SELECT * FROM veritas_pools").fetchall()

            count = 0
            for row in rows:
                pool_id = PoolId(
                    chain_id=row["chain_id"],
                    venue=row["venue"],
                    factory=row["factory"],
                    pool_address=row["pool_address"],
                )
                pool = PoolMetadata(
                    pool_id=pool_id,
                    token0=row["token0"],
                    token1=row["token1"],
                    decimals0=row["decimals0"],
                    decimals1=row["decimals1"],
                    fee=row["fee"],
                    kind=row["kind"],
                    reserve0=int(row["reserve0"]) if row["reserve0"] else 0,
                    reserve1=int(row["reserve1"]) if row["reserve1"] else 0,
                    liquidity=int(row["liquidity"]) if row["liquidity"] else 0,
                    last_updated_block=row["last_updated_block"],
                    last_updated_timestamp=row["last_updated_timestamp"],
                    usd_depth=row["usd_depth"],
                    is_live=bool(row["is_live"]),
                    historical_edge_count=row["historical_edge_count"],
                    last_edge_timestamp=row["last_edge_timestamp"],
                )
                self.register(pool, persist=False)
                count += 1
            return count
        finally:
            c.close()
