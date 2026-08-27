import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)

class QueueMaster:
    def __init__(self, db_path: str = "veritas_queue.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT DEFAULT 'pending', -- pending, running, succeeded, failed, cancelled
                    priority INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    attempt_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    result TEXT,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status_created 
                ON tasks(status, created_at)
            """)
            conn.commit()

    def enqueue(self, task_type: str, payload: Dict[str, Any], priority: int = 0, max_retries: int = 3) -> str:
        """Add a new task to the queue. Returns task_id."""
        task_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tasks (id, task_type, payload, priority, max_retries, status) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, task_type, json.dumps(payload), priority, max_retries, 'pending')
            )
            conn.commit()
        logger.info(f"Enqueued task {task_id} of type {task_type}")
        return task_id

    def claim_pending_task(self, worker_id: str = "default") -> Optional[Dict[str, Any]]:
        """Atomically claim a pending task. Returns None if queue is empty."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            # Use SELECT FOR UPDATE style via exclusive transaction to prevent double-fetch
            conn.execute("BEGIN EXCLUSIVE")
            
            row = conn.execute("""
                SELECT id, task_type, payload, max_retries, attempt_count
                FROM tasks
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            """).fetchone()
            
            if not row:
                conn.execute("COMMIT")
                return None

            task = dict(row)
            # Mark as running
            conn.execute("""
                UPDATE tasks 
                SET status = 'running', 
                    started_at = CURRENT_TIMESTAMP,
                    attempt_count = attempt_count + 1
                WHERE id = ?
            """, (task['id'],))
            conn.execute("COMMIT")
            
            task['payload'] = json.loads(task['payload'])
            return task

    def update_task_result(self, task_id: str, succeeded: bool, result: Any = None, error: str = None):
        """Update task status. If failed and retries left, re-queue."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            if succeeded:
                conn.execute("""
                    UPDATE tasks 
                    SET status = 'succeeded', 
                        completed_at = CURRENT_TIMESTAMP,
                        result = ?
                    WHERE id = ?
                """, (json.dumps(result) if result else None, task_id))
                conn.commit()
                logger.info(f"Task {task_id} succeeded.")
            else:
                # Check if we can retry
                row = conn.execute("SELECT attempt_count, max_retries FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if row and row[0] < row[1]:
                    # Requeue
                    conn.execute("""
                        UPDATE tasks 
                        SET status = 'pending', 
                            error = ?,
                            started_at = NULL
                        WHERE id = ?
                    """, (error, task_id))
                    logger.warning(f"Task {task_id} failed. Re-queuing (attempt {row[0]+1}/{row[1]})")
                else:
                    conn.execute("""
                        UPDATE tasks 
                        SET status = 'failed', 
                            completed_at = CURRENT_TIMESTAMP,
                            error = ?
                        WHERE id = ?
                    """, (error, task_id))
                    logger.error(f"Task {task_id} permanently failed: {error}")
                conn.commit()

    def reap_stale_running_tasks(self, timeout_seconds: int = 60):
        """Safety net: tasks stuck in 'running' for too long get reverted to pending."""
        cutoff = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tasks 
                SET status = 'pending', started_at = NULL, attempt_count = attempt_count + 1
                WHERE status = 'running' AND started_at < ?
            """, (cutoff,))
            affected = cursor.rowcount
            conn.commit()
            if affected:
                logger.warning(f"Reaped {affected} stale running tasks.")

    def get_status(self, task_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None