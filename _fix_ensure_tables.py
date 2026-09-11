#!/usr/bin/env python3
"""Fix _ensure_tables to include gas_already_in_delta and migration."""
import pathlib

content = pathlib.Path('core/accounting.py').read_text()

# Replace the _ensure_tables method entirely
old_method = '''    def _ensure_tables(self):
        """Ensure accounting tables exist."""
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
            c.commit()
        finally:
            c.close()'''

new_method = '''    def _ensure_tables(self):
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
                    gas_already_in_delta INTEGER DEFAULT 1,
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
            c.close()'''

content = content.replace(old_method, new_method)

pathlib.Path('core/accounting.py').write_text(content)
print("Fixed _ensure_tables with migration")
