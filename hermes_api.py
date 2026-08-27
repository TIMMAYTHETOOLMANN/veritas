from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn
import logging
import toml
from queue_master import QueueMaster
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)
app = FastAPI(title="VERITAS Hermes Bridge")

# Load config
config = toml.load("strategy.toml")
queue = QueueMaster(config['system']['queue_db_path'])

class TaskPayload(BaseModel):
    task_type: str
    payload: Optional[dict] = {}
    priority: Optional[int] = 0
    max_retries: Optional[int] = 3

@app.post("/api/v1/task", status_code=202)
async def submit_task(task: TaskPayload):
    """
    Hermes Bots POST to this endpoint.
    Example payload: {"task_type": "edge_scanner", "payload": {"limit": 5}}
    """
    task_id = queue.enqueue(
        task_type=task.task_type,
        payload=task.payload,
        priority=task.priority,
        max_retries=task.max_retries
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
    return {"status": "operational", "queue": "active"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)