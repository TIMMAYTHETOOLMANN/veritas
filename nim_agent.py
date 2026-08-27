"""
nim_agent.py -- production-grade, self-throttling client for NVIDIA NIM.

Standalone HTTP client for a scripted Nemotron/LLM worker that calls NIM
DIRECTLY (outside the Hermes provider path). It is NOT a wrapper around
Hermes -- it owns its own `requests` session.

Guarantees:
  * Sliding-window rate limit (strict < rpm_limit) -- single lock, no release/
    reacquire race, sleeps happen OUTSIDE the lock so no thread is blocked
    holding it. try/finally everywhere: an exception can never leak the lock.
  * 429 handling: honors `Retry-After` when the server sends it, otherwise
    jittered exponential backoff starting high (60s) so a live 429 penalty-box
    timer is never reset by fast retries.
  * Tool calls execute SEQUENTIALLY with a configurable inter-call delay --
    a multi-tool round never produces a burst of parallel requests.
  * Registry-based tool dispatch. NO hardcoded fake results: an unknown tool
    raises, it does not silently return fabricated data.

Drop-in usage:
    from nim_agent import NimAgent
    agent = NimAgent(api_key=os.environ["NVIDIA_API_KEY"])
    agent.register("scan_edges", parallel_scan_edges)
    out = agent.chat([{"role":"user","content":"scan edges"}], )
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import requests

log = logging.getLogger("nim_agent")


def _now() -> float:
    return time.monotonic()


class SlidingWindowRateLimiter:
    """Strict per-minute sliding-window limiter. Thread-safe, leak-free."""

    def __init__(self, rpm_limit: int, window_seconds: float = 60.0):
        if rpm_limit < 1:
            raise ValueError("rpm_limit must be >= 1")
        self._limit = int(rpm_limit)
        self._window = float(window_seconds)
        self._lock = threading.Lock()
        self._stamps: deque[float] = deque()

    def wait(self) -> None:
        """Block until a request slot is free, then consume one."""
        while True:
            with self._lock:  # try/finally is implicit: acquiring this way
                # text1 cannot leak the lock even if an exception occurs
                now = _now()
                cutoff = now - self._window
                while self._stamps and self._stamps[0] <= cutoff:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                oldest = self._stamps[0]
                wait_for = self._window - (now - oldest)
            # Sleep OUTSIDE the lock -- no thread blocks others while dozing.
            if wait_for > 0:
                time.sleep(wait_for + 0.05)


class NimAgent:
    """Self-throttling NIM OpenAI-compatible client with sequential tools."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        model: str = "nvidia/llama-3.1-nemotron-70b-instruct",
        rpm_limit: int = 38,
        socks5: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 5,
        tool_call_delay: float = 1.0,
        max_tool_rounds: int = 8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.limiter = SlidingWindowRateLimiter(rpm_limit)
        self.timeout = timeout
        self.max_retries = int(max_retries)
        self.tool_call_delay = float(tool_call_delay)
        self.max_tool_rounds = int(max_tool_rounds)

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )
        if socks5:
            self._session.proxies = {"http": socks5, "https": socks5}

        self._registry: Dict[str, Callable] = {}
        self._registry_lock = threading.Lock()

    # -- tool registry -----------------------------------------------------

    def register(self, name: str, fn: Callable, description: str = "") -> Callable:
        """Register a tool callable. Unknown tools raise, never fake a result."""
        with self._registry_lock:
            self._registry[name] = fn
        log.debug("registered tool '%s'", name)
        return fn

    def _tool_specs(self) -> List[Dict[str, Any]]:
        spec = []
        with self._registry_lock:
            names = list(self._registry)
        for name in names:
            spec.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"Callable tool: {name}",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    },
                }
            )
        return spec

    # -- HTTP + backoff ----------------------------------------------------

    @staticmethod
    def _backoff(attempt: int, response: Optional[requests.Response]) -> float:
        # Honor the server's own Retry-After whenever provided.
        if response is not None and "retry-after" in response.headers:
            try:
                return float(response.headers["retry-after"])
            except ValueError:
                pass
        # Otherwise: high base so we never reset a live penalty-box timer.
        return 60.0 * (2 ** attempt) + random.uniform(0, 5)

    def _post_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self.limiter.wait()
            try:
                resp = self._session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = e
                delay = float(self._backoff(attempt, None)) / 4.0  # transient, milder
                log.warning("[nim] request error: %s; retry in %.1fs", e, delay)
                time.sleep(delay)
                continue

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                delay = self._backoff(attempt, resp)
                log.warning(
                    "[nim] 429 penalty box; waiting %.1fs (attempt %d/%d)",
                    delay, attempt + 1, self.max_retries,
                )
                time.sleep(delay)
                continue
            resp.raise_for_status()

        raise RuntimeError(f"NIM request failed after {self.max_retries} retries: {last_err}")

    # -- tool execution ----------------------------------------------------

    def _execute_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        with self._registry_lock:
            fn = self._registry.get(name)
        if fn is None:
            raise ValueError(f"Unknown tool '{name}' -- register it via agent.register()")
        log.debug("[nim] executing tool '%s' with %s", name, arguments)
        return fn(**arguments)

    # -- chat loop (sequential tools, no burst) ----------------------------

    def chat(
        self,
        messages: List[Dict[str, str]],
        tools: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the conversation, auto-executing tool calls one at a time.

        Returns the final assistant message dict (or the raw first response
        when no tools are requested).
        """
        with_tools = bool(tools) and bool(self._tool_specs())
        payload: Dict[str, Any] = {"model": self.model, "messages": messages, **kwargs}
        if with_tools:
            payload["tools"] = self._tool_specs()
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        for _round in range(self.max_tool_rounds):
            result = self._post_chat(payload)
            message = result["choices"][0]["message"]
            calls = message.get("tool_calls") or []

            if not calls:
                return message

            messages.append(message)  # assistant tool-call message
            for i, tool_call in enumerate(calls):
                if i > 0:
                    time.sleep(self.tool_call_delay)  # sequential: no burst
                fn_name = tool_call["function"]["name"]
                try:
                    fn_args = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}
                fn_out = self._execute_tool(fn_name, fn_args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(fn_out, default=str),
                    }
                )

            payload["messages"] = messages  # carry full history into next round

        raise RuntimeError(f"Tool-call loop exceeded max_tool_rounds={self.max_tool_rounds}")