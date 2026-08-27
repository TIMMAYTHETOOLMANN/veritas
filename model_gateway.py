import functools
import signal
from typing import Any, Dict, Callable
import logging

logger = logging.getLogger(__name__)

class ModelGateway:
    def __init__(self, config: Dict):
        self.backend = config.get('backend', 'mock')
        self.config = config
        # Dynamically load internal Python tools if needed (for edge_scanner)
        self.tool_registry = self._load_tools()

    def _load_tools(self):
        """Import specific functions from VERITAS so the queue can call them directly."""
        try:
            # Allow the worker to execute system functions without re-running scripts
            from arb_engine import parallel_scan_edges, best_three_pool_arb, scan_cross_venue
            from flash_hunter import hunt_once
            return {
                "scan_edges": parallel_scan_edges,
                "hunt_once": hunt_once,
                "three_pool": best_three_pool_arb,
                "scan_cross_venue": scan_cross_venue,
            }
        except ImportError as e:
            logger.warning(f"Could not load VERITAS tools: {e}. Falling back to subprocess.")
            return {}

    def _run_with_timeout(self, func: Callable, timeout: int, *args, **kwargs):
        """Run a function with a timeout. Kills it if it hangs."""
        def wrapper():
            return func(*args, **kwargs)

        def timeout_handler(signum, frame):
            raise TimeoutError(f"Function {func.__name__} exceeded {timeout}s")
        
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)
        try:
            result = wrapper()
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            return result
        except TimeoutError as e:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            raise e
        except Exception as e:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
            raise e

    def execute(self, task_type: str, payload: Dict, timeout: int = 30) -> Dict[str, Any]:
        """The main execution switch."""
        logger.info(f"Executing {task_type} with backend {self.backend}")

        # If the task requires a specific VERITAS tool and we have it loaded, run it directly.
        if task_type == "edge_scanner" and "scan_cross_venue" in self.tool_registry:
            try:
                from core.rpc import RPC
                rpc = RPC("https://arb1.arbitrum.io/rpc", timeout=120, retries=3)
                result = self._run_with_timeout(
                    self.tool_registry["scan_cross_venue"], timeout,
                    rpc, payload.get("eth_usd", 2500.0), payload.get("gas_usd", 0.02),
                    size_steps=payload.get("size_steps", 12),
                    max_venues_per_quote=payload.get("max_venues_per_quote", 8),
                    use_multi_hop=payload.get("use_multi_hop", True),
                    use_parallel=payload.get("use_parallel", True),
                )
                edges, report = result
                top = sorted(edges, key=lambda e: e.get('net_usd', 0), reverse=True)[:3]
                return {"status": "success", "data": {"edges": len(edges), "combos": len(report), "top3": top}}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        elif task_type == "health_check":
            # Generic health check: test RPC connectivity (mock logic)
            try:
                return {"status": "success", "data": {"anvil": "live", "rpc": "responsive", "executor": "deployed"}}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        else:
            # If we don't have a direct Python hook, execute via the LLM (for strategy tuning)
            return self._llm_invoke(task_type, payload, timeout)

    def _llm_invoke(self, task_type: str, payload: Dict, timeout: int) -> Dict[str, Any]:
        """The abstracted model call. Bulletproof: returns a static response if model isn't configured."""
        # Stub for actual inference. Since we're delaying model specifics, this is a mock.
        # Once you plug in Ollama/LlamaCpp, this is where it goes.
        logger.info(f"Mock LLM invoke for {task_type} with payload {payload}")
        
        # Simulate model thinking
        response = {
            "task_type": task_type,
            "analysis": "All systems nominal. No strategy adjustments required.",
            "action": "pass"
        }
        
        # If task_type is 'gas_watcher', simulate a dynamic adjustment recommendation
        if task_type == "gas_watcher":
            response["action"] = "adjust_gas_multiplier"
            response["new_multiplier"] = 1.5
            
        return {"status": "success", "data": response}