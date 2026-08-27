import toml
import uvicorn
import sqlite3
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from queue_master import QueueMaster
from model_gateway import ModelGateway

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="VERITAS Controller API", version="0.1.0")

# Load configuration
CONFIG = toml.load("strategy.toml")
queue = QueueMaster(CONFIG['system']['queue_db_path'])
gateway = ModelGateway(CONFIG['model'])

# Pydantic models for API
class TaskCreate(BaseModel):
    task_type: str
    payload: Dict[str, Any]
    priority: int = 0

class TaskResponse(BaseModel):
    task_id: str
    status: str
    detail: Optional[str] = None

class TaskStatusResponse(BaseModel):
    id: str
    task_type: str
    payload: Dict[str, Any]
    status: str
    priority: int
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    attempt_count: int
    max_retries: int
    result: Optional[Dict] = None
    error: Optional[str] = None

@app.on_event("startup")
async def startup_event():
    logger.info("VERITAS API started.")

@app.get("/")
async def root():
    return {"message": "VERITAS Controller API is running."}

@app.post("/tasks", response_model=TaskResponse)
async def create_task(task: TaskCreate, background_tasks: BackgroundTasks):
    task_id = queue.enqueue(
        task_type=task.task_type,
        payload=task.payload,
        priority=task.priority
    )
    return TaskResponse(task_id=task_id, status="pending", detail="Task enqueued.")

@app.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    task = queue.get_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(**task)

@app.get("/tasks", response_model=list[TaskStatusResponse])
async def list_tasks(limit: int = 50):
    # For simplicity, we'll fetch all and limit in memory. In production, use pagination.
    with sqlite3.connect(queue.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [TaskStatusResponse(**dict(row)) for row in rows]

@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    with sqlite3.connect(queue.db_path) as conn:
        conn.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (task_id,))
        conn.commit()
    return {"message": f"Task {task_id} cancelled if it was pending."}

@app.get("/health")
async def health_check():
    # Simple health check: can we touch the DB?
    try:
        with sqlite3.connect(queue.db_path) as conn:
            conn.execute("SELECT 1")
        return {"status": "healthy"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("hermes_api:app", host="0.0.0.0", port=8000, reload=True)