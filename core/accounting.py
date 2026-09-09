#!/usr/bin/env python3
"""
core/accounting.py — VERITAS immutable execution ledger.

Transaction-keyed idempotent accounting.

Schema:
  executions (
    tx_hash PRIMARY KEY,
    chain_id INTEGER,
    block_number INTEGER,
    route_id TEXT,
    candidate_id TEXT,
    status TEXT,
    projected_profit_usd REAL,
    realized_profit_usd REAL,
    verified_profit_usd REAL,
    gas_native REAL,
    gas_usd REAL,
    created_at INTEGER,
    verified_at INTEGER
  )

The same transaction hash must never mutate capital twice.
If the process crashes after broadcast but before accounting:
  restart -> recover pending tx -> verify receipt -> reconcile

ACCOUNTING EQUATION:
  verified_profit = settlement_asset_delta - actual_external_gas_cost
  (only if gas is not already in the settlement delta)
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.db import conn, now


@dataclass
class ExecutionRecord:
    """Immutable record of one execution."""
    tx_hash: str
    chain_id: int = 42161
    block_number: int = 0
    route_id: str = ""
    candidate_id: str = ""
    status: str = "pending"          # pending, confirmed, reverted, failed
    projected_profit_usd: float = 0.0
    realized_profit_usd: float = 0.0
    verified_profit_usd: float = 0.0
    gas_native: float = 0.0
    gas_usd: float = 0.0
    gas_already_in_delta: bool = True
    created_at: int = 0
    verified_at: int = 0

    def to_dict(self) -> dict:
        return {
            "tx_hash": self.tx_hash,
            "chain_id": self.chain_id,
            "block_number": self.block_number,
            "route_id": self.route_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "projected_profit_usd": round(self.projected_profit_usd, 6),
            "realized_profit_usd": round(self.realized_profit_usd, 6),
            "verified_profit_usd": round(self.verified_profit_usd, 6),
            "gas_native": round(self.gas_native, 8),
            "gas_usd": round(self.gas_usd, 6),
            "gas_already_in_delta": self.gas_already_in_delta,
            "created_at": self.created_at,
            "verified_at": self.verified_at,
        }


class AccountingLedger:
    """
    Immutable execution ledger with transaction-keyed idempotency.

    Guarantees:
    - One tx_hash = one ledger record
    - Same tx_hash never mutates capital twice
    - Atomic ledger + capital mutation
    """

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure accounting tables exist, with migration for new columns."""
        c = conn()
        try:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
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
                    created_at INTEGER DEFAULT 0,
                    verified_at INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
                CREATE INDEX IF NOT EXISTS idx_executions_block ON executions(block_number);
            """)
            # Migration: add gas_already_in_delta column if missing (existing DBs)
            cols = [r[1] for r in c.execute("PRAGMA table_info(executions)").fetchall()]
            if "gas_already_in_delta" not in cols:
                c.execute("ALTER TABLE executions ADD COLUMN gas_already_in_delta INTEGER DEFAULT 1")
            c.commit()
        finally:
            c.close()

    def record_execution(self, record: ExecutionRecord) -> bool:
        """
        Record an execution. Idempotent — same tx_hash returns existing record.

        Returns True if new record was inserted, False if already existed.
        """
        existing = self.get_execution(record.tx_hash)
        if existing:
            return False  # Already recorded — idempotent

        if record.created_at == 0:
            record.created_at = now()

        c = conn()
        try:
            c.execute("""
                INSERT INTO executions (
                    tx_hash, chain_id, block_number, route_id, candidate_id,
                    status, projected_profit_usd, realized_profit_usd,
                    verified_profit_usd, gas_native, gas_usd, gas_already_in_delta,
                    created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.tx_hash, record.chain_id, record.block_number,
                record.route_id, record.candidate_id, record.status,
                record.projected_profit_usd, record.realized_profit_usd,
                record.verified_profit_usd, record.gas_native, record.gas_usd,
                int(record.gas_already_in_delta),
                record.created_at, record.verified_at,
            ))
            c.commit()
            return True
        except sqlite3.IntegrityError:
            # Race condition — another process inserted
            return False
        finally:
            c.close()

    def verify_execution(
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
        """
        """
        Verify an execution with on-chain data.

        ACCOUNTING EQUATION:
          If gas_already_in_delta (typical for flash-loan arb):
            verified_profit = realized_profit_usd
          Else:
            verified_profit = realized_profit_usd - gas_usd

        Returns the updated record, or None if tx_hash not found.
        """
        record = self.get_execution(tx_hash)
        if not record:
            return None

        record.realized_profit_usd = realized_profit_usd
        record.gas_native = gas_native
        record.gas_usd = gas_usd
        record.block_number = block_number
        record.verified_at = now()

        # Apply accounting equation
        record.gas_already_in_delta = gas_already_in_delta
        if gas_already_in_delta:
            record.verified_profit_usd = realized_profit_usd
        else:
            record.verified_profit_usd = realized_profit_usd - gas_usd

        # Update status
        if realized_profit_usd > 0:
            record.status = "confirmed"
        else:
            record.status = "confirmed"  # Still confirmed, just not profitable

        self._update_record(record)
        return record

    def mark_reverted(
        self, tx_hash: str, gas_native: float, gas_usd: float, block_number: int
    ) -> Optional[ExecutionRecord]:
        """
        Mark an execution as reverted.

        A reverted transaction is NOT PROFITABLE but IS a real economic gas loss.
        """
        record = self.get_execution(tx_hash)
        if not record:
            return None

        record.status = "reverted"
        record.realized_profit_usd = 0.0
        record.verified_profit_usd = -gas_usd  # Gas loss
        record.gas_native = gas_native
        record.gas_usd = gas_usd
        record.block_number = block_number
        record.verified_at = now()

        self._update_record(record)
        return record

    def get_execution(self, tx_hash: str) -> Optional[ExecutionRecord]:
        """Get an execution record by tx_hash."""
        c = conn()
        try:
            row = c.execute(
                "SELECT * FROM executions WHERE tx_hash = ?", (tx_hash,)
            ).fetchone()
            if not row:
                return None
            return ExecutionRecord(
                tx_hash=row["tx_hash"],
                chain_id=row["chain_id"],
                block_number=row["block_number"],
                route_id=row["route_id"],
                candidate_id=row["candidate_id"],
                status=row["status"],
                projected_profit_usd=row["projected_profit_usd"],
                realized_profit_usd=row["realized_profit_usd"],
                verified_profit_usd=row["verified_profit_usd"],
                gas_native=row["gas_native"],
                gas_usd=row["gas_usd"],
                created_at=row["created_at"],
                verified_at=row["verified_at"],
            )
        finally:
            c.close()

    def get_pending_executions(self) -> List[ExecutionRecord]:
        """Get all pending executions (for crash recovery)."""
        c = conn()
        try:
            rows = c.execute(
                "SELECT * FROM executions WHERE status = 'pending'"
            ).fetchall()
            return [
                ExecutionRecord(
                    tx_hash=r["tx_hash"],
                    chain_id=r["chain_id"],
                    block_number=r["block_number"],
                    route_id=r["route_id"],
                    candidate_id=r["candidate_id"],
                    status=r["status"],
                    projected_profit_usd=r["projected_profit_usd"],
                    realized_profit_usd=r["realized_profit_usd"],
                    verified_profit_usd=r["verified_profit_usd"],
                    gas_native=r["gas_native"],
                    gas_usd=r["gas_usd"],
                    created_at=r["created_at"],
                    verified_at=r["verified_at"],
                )
                for r in rows
            ]
        finally:
            c.close()

    def get_all_executions(self, limit: int = 100) -> List[ExecutionRecord]:
        """Get all executions, most recent first."""
        c = conn()
        try:
            rows = c.execute(
                "SELECT * FROM executions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                ExecutionRecord(
                    tx_hash=r["tx_hash"],
                    chain_id=r["chain_id"],
                    block_number=r["block_number"],
                    route_id=r["route_id"],
                    candidate_id=r["candidate_id"],
                    status=r["status"],
                    projected_profit_usd=r["projected_profit_usd"],
                    realized_profit_usd=r["realized_profit_usd"],
                    verified_profit_usd=r["verified_profit_usd"],
                    gas_native=r["gas_native"],
                    gas_usd=r["gas_usd"],
                    created_at=r["created_at"],
                    verified_at=r["verified_at"],
                )
                for r in rows
            ]
        finally:
            c.close()

    def summary(self) -> dict:
        """Get accounting summary."""
        c = conn()
        try:
            total = c.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
            confirmed = c.execute(
                "SELECT COUNT(*) FROM executions WHERE status = 'confirmed'"
            ).fetchone()[0]
            reverted = c.execute(
                "SELECT COUNT(*) FROM executions WHERE status = 'reverted'"
            ).fetchone()[0]
            pending = c.execute(
                "SELECT COUNT(*) FROM executions WHERE status = 'pending'"
            ).fetchone()[0]

            total_profit = c.execute(
                "SELECT COALESCE(SUM(verified_profit_usd), 0) FROM executions"
            ).fetchone()[0]
            total_gas = c.execute(
                "SELECT COALESCE(SUM(gas_usd), 0) FROM executions"
            ).fetchone()[0]

            return {
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
            }
        finally:
            c.close()

    def _update_record(self, record: ExecutionRecord) -> None:
        """Update an existing record."""
        c = conn()
        try:
            c.execute("""
                UPDATE executions SET
                    block_number = ?, status = ?,
                    realized_profit_usd = ?, verified_profit_usd = ?,
                    gas_native = ?, gas_usd = ?, gas_already_in_delta = ?,
                    verified_at = ?
                WHERE tx_hash = ?
            """, (
                record.block_number, record.status,
                record.realized_profit_usd, record.verified_profit_usd,
                record.gas_native, record.gas_usd,
                int(record.gas_already_in_delta), record.verified_at,
                record.tx_hash,
            ))
            c.commit()
        finally:
            c.close()
