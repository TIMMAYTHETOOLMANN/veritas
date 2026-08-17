# core/state.py — progress persistence, checkpoint/recovery
import time, json, sqlite3
from core.db import conn, now

class State:
    def __init__(self):
        self.db_path = None

    def _ensure_table(self, c):
        # walker_state is in core/db.py SCHEMA; this guard makes resume()
        # safe against an old DB file that predates the schema entry.
        c.execute("""CREATE TABLE IF NOT EXISTS walker_state(
            chain_id INTEGER PRIMARY KEY, cur_block INTEGER,
            processed_count INTEGER, status TEXT, ts INTEGER)""")

    def resume(self, chain_id):
        """Return (cur_block, processed_count, status) from the last checkpoint,
        or (0, 0, "fresh") when none exists. Never raises on a missing table."""
        c = conn()
        try:
            self._ensure_table(c)
            row = c.execute(
                "SELECT cur_block, processed_count, status FROM walker_state "
                "WHERE chain_id=? ORDER BY cur_block DESC LIMIT 1",
                (chain_id,)
            ).fetchone()
        finally:
            c.close()
        if row:
            return (int(row["cur_block"]), int(row["processed_count"]), row["status"])
        return (0, 0, "fresh")

    def checkpoint(self, chain_id, cur_block, processed_count, status="ok"):
        c = conn()
        try:
            self._ensure_table(c)
            c.execute("""
                INSERT OR REPLACE INTO walker_state(chain_id, cur_block, processed_count, status, ts)
                VALUES(?, ?, ?, ?, ?)
            """, (chain_id, int(cur_block), int(processed_count), status, now()))
            c.commit()
        finally:
            c.close()

state = State()
