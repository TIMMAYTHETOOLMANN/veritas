import time
import logging
import signal
import toml
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from queue_master import QueueMaster
from model_gateway import ModelGateway

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Worker:
    def __init__(self, config_path: str = "strategy.toml"):
        self.config = toml.load(config_path)
        self.queue = QueueMaster(self.config['system']['queue_db_path'])
        self.gateway = ModelGateway(self.config['model'])
        self.running = True
        # signal.SIGALRM is only available on Unix. Guard against Windows.
        self._timeout_ok = hasattr(signal, "SIGALRM")
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def shutdown(self, signum, frame):
        logger.info("Shutdown signal received. Finishing current task and exiting.")
        self.running = False

    def run(self):
        logger.info("VERITAS Worker started. Awaiting tasks...")
        while self.running:
            try:
                # 1. Reclaim tasks that were running but lost (e.g., worker crash)
                self.queue.reap_stale_running_tasks(timeout_seconds=60)

                # 2. Claim a pending task
                task = self.queue.claim_pending_task(worker_id="main")
                if not task:
                    time.sleep(5)  # No tasks, sleep briefly
                    continue

                # 3. Execute the task
                logger.info(f"Processing task {task['id']} of type {task['task_type']}")
                task_timeout = self.config['tasks'].get(task['task_type'], {}).get('timeout_seconds', 30)
                
                try:
                    result = self.gateway.execute(
                        task_type=task['task_type'],
                        payload=task['payload'],
                        timeout=task_timeout
                    )
                    
                    if result.get('status') == 'success':
                        self.queue.update_task_result(task['id'], succeeded=True, result=result.get('data'))
                    else:
                        self.queue.update_task_result(task['id'], succeeded=False, error=result.get('error'))
                        
                except Exception as e:
                    logger.exception(f"Unexpected worker error on task {task['id']}")
                    self.queue.update_task_result(task['id'], succeeded=False, error=str(e))

            except Exception as e:
                logger.exception("Critical error in worker main loop. Restarting loop...")
                time.sleep(10)

        logger.info("Worker shut down gracefully.")

if __name__ == "__main__":
    worker = Worker()
    worker.run()