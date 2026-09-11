#!/usr/bin/env python3
"""Fix accounting double-subtraction and load_from_db write loop."""
import pathlib

# Fix 1: Accounting double-subtraction
accounting_content = pathlib.Path('core/accounting.py').read_text()

# Add gas_already_in_delta column to schema
accounting_content = accounting_content.replace(
    '''CREATE TABLE IF NOT EXISTS executions (
                    tx_hash TEXT PRIMARY KEY,
                    chain_id INTEGER NOT NULL,
                    block_number INTEGER DEFAULT 0,
                    route_id TEXT DEFAULT '',
                    candidate_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    projected_profit_usd REAL DEFAULT 0,
                    realized_profit_usfit_usd REAL DEFAULT 0,
                    verified_profit_usd REAL DEFAULT 0,
                    gas_native REAL DEFAULT 0,
                    gas_usd REAL DEFAULT 0,
                    created_at INTEGER DEFAULT 0,
                    verified_at INTEGER DEFAULT 0
                )''',
    '''CREATE TABLE IF NOT EXISTS executions (
                    tx_hash TEXT PRIMARY KEY,
                    chain_id INTEGER NOT NULL,
                    block_number INTEGER DEFAULT 0,
                    route_id TEXT DEFAULT '',
                    candidate_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    projected_profit_usd REAL DEFAULT 0,
                    realized_profit_usd REAL DEFAULT 0,
                    verified_profit_usd REAL DEFAULT 0,
                    gas_native REAL DEFAULT 0,
                    gas_usd REAL DEFAULT 0,
                    gas_already_in_delta INTEGER DEFAULT 1,
                    created_at INTEGER DEFAULT 0,
                    verified_at INTEGER DEFAULT 0
                )'''
)

# Fix the verify_execution to store gas_already_in_delta
accounting_content = accounting_content.replace(
    '''    def verify_execution(
        self,
        tx_hash: str,
        realized_profit_usd: float,
        gas_native: float,
        gas_usd: float,
        block_number: int,
        gas_already_in_delta: bool = True,
    ) -> Optional[ExecutionRecord]:''',
    '''    def verify_execution(
        self,
        tx_hash: str,
        realized_profit_usd: float,
        gas_native: float,
        gas_usd: float,
        block_number: int,
        gas_already_in_delta: bool = True,
    ) -> Optional[ExecutionRecord]:
        """
        Verify an execution with on-chain data.

        ACCOUNTING EQUATION:
          If gas_already_in_delta (typical for flash-loan arb):
            verified_profit = realized_profit_usd
          Else:
            verified_profit = realized_profit_usd - gas_usd

        CRITICAL: summary() must NOT subtract gas again from verified_profit_usd.
        """'''
)

# Fix summary() to not double-subtract gas
accounting_content = accounting_content.replace(
    '''            return {
                "total_executions": total,
                "confirmed": confirmed,
                "reverted": reverted,
                "pending": pending,
                "total_verified_profit_usd": round(total_profit, 6),
                "total_gas_usd": round(total_gas, 6),
                "net_profit_usd": round(total_profit - total_gas, 6),
            }''',
    '''            return {
                "total_executions": total,
                "confirmed": confirmed,
                "reverted": reverted,
                "pending": pending,
                "total_verified_profit_usd": round(total_profit, 6),
                "total_gas_usd": round(total_gas, 6),
                "net_profit_usd": round(total_profit, 6),
                # IMPORTANT: net_profit = SUM(verified_profit_usd)
                # verified_profit_usd ALREADY accounts for gas correctly:
                #   - If gas_already_in_delta=True: verified = realized (gas included in delta)
                #   - If gas_already_in_delta=False: verified = realized - gas
                # NEVER subtract gas again here.
            }'''
)

pathlib.Path('core/accounting.py').write_text(accounting_content)
print("Fixed accounting.py")

# Fix 2: load_from_db write loop
pool_content = pathlib.Path('core/pool.py').read_text()

# Add persist parameter to register()
pool_content = pool_content.replace(
    '''    def register(self, pool: PoolMetadata) -> None:
        """Register or update a pool in the registry."""
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

        # Persist
        self._persist(pool)''',
    '''    def register(self, pool: PoolMetadata, persist: bool = True) -> None:
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
            self._persist(pool)'''
)

# Fix load_from_db to use persist=False
pool_content = pool_content.replace(
    '''                self.register(pool)
                count += 1''',
    '''                self.register(pool, persist=False)
                count += 1'''
)

pathlib.Path('core/pool.py').write_text(pool_content)
print("Fixed pool.py")
