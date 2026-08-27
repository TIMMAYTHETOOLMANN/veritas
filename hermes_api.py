"""hermes_api.py — HTTP bridge for the Hermes Agent "Bots" tab.

Hermes Bots POST cron triggers here; this drops them into the SQLite queue and
returns 202 Accepted immediately. The worker consumes them asynchronously.

Endpoints:
  POST /api/v1/task             -> enqueue {task_type, payload, priority, max_retries}
  GET  /api/v1/task/{task_id}   -> task status
  GET  /api/v1/health           -> liveness
"""
import os
import logging
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import toml

from queue_master import QueueMaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "strategy.toml")

config = toml.load(CONFIG_PATH)
queue = QueueMaster(config["system"]["queue_db_path"])

app = FastAPI(title="VERITAS Hermes Bridge")


class TaskPayload(BaseModel):
    task_type: str
    payload: Optional[Dict[str, Any]] = {}
    priority: Optional[int] = 0
    max_retries: Optional[int] = 3


@app.post("/api/v1/task", status_code=202)
async def submit_task(task: TaskPayload):
    """Hermes Bots POST here. Example: {"task_type": "edge_scanner", "payload": {"limit": 5}}"""
    task_id = queue.enqueue(
        task_type=task.task_type,
        payload=task.payload or {},
        priority=task.priority,
        max_retries=task.max_retries,
    )
    return {"status": "accepted", "task_id": task_id, "message": "Task queued for execution"}


@app.get("/api/v1/task/{task_id}")
async def get_task_status(task_id: str):
    status = queue.get_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@app.get("/api/v1/health")
async def health_check():
    return {"status": "operational", "queue": "active", "pending": queue.pending_count()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)