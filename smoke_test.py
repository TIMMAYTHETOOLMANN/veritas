"""smoke_test.py — bounded end-to-end test of the VCQ stack.

Enqueues a health_check, drives the worker loop for a few ticks manually
(instead of worker.run()'s infinite loop), then asserts the task succeeded.
Run: python3 smoke_test.py
"""
import time
from queue_master import QueueMaster
from model_gateway import ModelGateway
import toml

cfg = toml.load("strategy.toml")
q = QueueMaster(cfg["system"]["queue_db_path"])
gw = ModelGateway(cfg.get("model", {}), cfg.get("veritas", {}))

task_id = q.enqueue("health_check", {}, max_retries=1)
print("enqueued", task_id)

# Drive the same steps worker.run() performs, for a few ticks.
for _ in range(10):
    q.reap_stale_running_tasks(timeout_seconds=60)
    task = q.claim_pending_task(worker_id="smoke")
    if not task:
        time.sleep(1)
        continue
    print("claimed", task["id"], task["task_type"])
    result = gw.execute(task_type=task["task_type"],
                        payload=task["payload"],
                        timeout=15)
    print("result status:", result.get("status"))
    if result.get("status") == "success":
        q.update_task_result(task["id"], succeeded=True, result=result.get("data"))
    else:
        q.update_task_result(task["id"], succeeded=False, error=result.get("error"))
    break

s = q.get_status(task_id)
print("FINAL STATUS:", s["status"])
print("RESULT DATA:", s["result"])
assert s["status"] == "succeeded", f"expected succeeded, got {s['status']}"
print("SMOKE TEST PASSED")