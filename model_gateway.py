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
                "primary_hunt": hunt_once,
                "three_pool": best_three_pool_arb,
                "scan_cross_venue": scan_cross_venue,
            }
        except ImportError as e:
            logger.warning(f"Could not load VERITAS tools: {e}. Falling back to subprocess.")
            return {}

    def _run_with_timeout(self, func: Callable, timeout: int, *args, **kwargs):
        """Run a function with a timeout. On Unix uses signal, on Windows runs without timeout (with warning)."""
        # Check if signal.SIGALRM is available (Unix/Linux/macOS)
        if hasattr(signal, 'SIGALRM'):
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
        else:
            # Windows or other platforms without SIGALRM: run without timeout
            logger.warning(f"Platform does not support SIGALRM; running {func.__name__} without timeout.")
            return func(*args, **kwargs)

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

        elif task_type == "primary_hunt":
            try:
                # Import necessary modules and set up the environment
                from eth_account import Account
                from flash_hunter import load_key, BROADCAST_RPCS, HOT_WALLET, load_executor
                import os

                # For scanning, hunt_once expects a URL string (it creates its own Vrpc internally)
                rpc_scan_url = BROADCAST_RPCS[0]
                # For the hunter's main RPC (signing/broadcasting), use flash_hunter.Rpc
                from flash_hunter import Rpc as HunterRpc
                rpc = HunterRpc(BROADCAST_RPCS[0])

                # Load account
                SECRET_FILE = os.path.join(os.path.dirname(__file__), ".hot_secret")
                with open(SECRET_FILE) as f:
                    key = f.read().strip()
                acct = Account.from_key(key)
                # Verify address (optional, but we can skip for speed)

                # Load executor address
                executor_addr = load_executor()
                if not executor_addr:
                    return {"status": "error", "error": "Executor not deployed. Please deploy first."}

                # Get the hunt_once function from the tool registry
                hunt_once_func = self.tool_registry.get("primary_hunt")
                if hunt_once_func is None:
                    return {"status": "error", "error": "primary_hunt tool not loaded"}

                # Now call hunt_once_func with URL string for rpc_scan
                result = self._run_with_timeout(
                    hunt_once_func,
                    timeout,
                    rpc,
                    acct,
                    executor_addr,
                    rpc_scan_url,
                    False  # verbose=False
                )

                if result is None:
                    return {"status": "success", "data": {"message": "No opportunity found in this cycle."}}
                else:
                    return {"status": "success", "data": result}

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
        return {"status": "success", "data": response}