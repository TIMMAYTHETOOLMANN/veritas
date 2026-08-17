# core/state.py — progress persistence, checkpoint/recovery
import time, json
from core.db import conn, now

class State:
    def __init__(self):
        self.db_path = None

    def resume(self, chain_id):
        """Read last checkpoint for this chain and return (cur_block, processed_count)."""
        c = conn()
        row = c.execute(
            "SELECT cur_block, processed_count, status FROM walker_state "
            "WHERE chain_id=? ORDER BY cur_block DESC LIMIT 1",
            (chain_id,)
        ).fetchone()
        c.close()
        if row:
            return (int(row["cur_block"]), int(row["processed_count"]), row["status"])
        return (0, 0, "fresh")

    def checkpoint(self, chain_id, cur_block, processed_count, status="ok"):
        c = conn()
        c.execute("""
            INSERT OR REPLACE INTO walker_state(chain_id, cur_block, processed_count, status, ts)
            VALUES(?, ?, ?, ?, ?)
        """, (chain_id, int(cur_block), int(processed_count), status, now()))
        c.commit()
        c.close()

state = State()
