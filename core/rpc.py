# core/rpc.py — minimal JSON-RPC client (retries, eth_call/balance/code)
import json
import time
import urllib.request
import threading
import queue


class RPC:
    def __init__(self, url, timeout=20, retries=3, user_agent=None):
        self.url, self.timeout, self.retries = url, timeout, retries
        self._id = 0
        # Browser-like UA so public/archive RPC gateways (drpc, publicnode,
        # tenderly) behind Cloudflare bot-wall don't 403 (http error 1010)
        # on high-volume eth_call diff. Veritas is a read-only client.
        self._ua = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

    def _hard_open(self, req):
        """Run urllib open in a daemon thread with a hard wall-clock cap.

        On Windows, urllib TLS can stall indefinitely past the socket
        timeout (a known block in the Python SSL/TLS stack). The
        urlopen(timeout=...) does NOT reliably fire in that case. A
        daemon thread + queue.get(timeout) guarantees we return within
        `self.timeout` seconds no matter what the socket does, so a
        stalled gateway can never freeze the whole hunter.
        """
        q = queue.Queue(maxsize=1)

        def _worker():
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    q.put(("ok", r.read()))
            except Exception as e:
                q.put(("err", e))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        try:
            kind, payload = q.get(timeout=self.timeout + 2)
        except queue.Empty:
            raise TimeoutError(f"rpc hard-timeout after {self.timeout}s: {self.url}")
        if kind == "err":
            raise payload
        return payload

    def _call_once(self, method, params):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": self._ua})
        out = json.loads(self._hard_open(req))
        if "error" in out:
            raise RuntimeError(f"rpc {method}: {out['error']}")
        return out["result"]

    def call(self, method, params):
        """Retrying RPC call with a hard wall-clock ceiling per attempt.

        Windows urllib can block in TLS/read beyond socket timeouts. Keeping the
        cap here (rather than only in callers) prevents any direct `call()` path
        from wedging a hunter process forever.
        """
        last_error = None
        for attempt in range(self.retries):
            result = queue.Queue(maxsize=1)

            def _run():
                try:
                    result.put_nowait(("ok", self._call_once(method, params)))
                except Exception as exc:
                    result.put_nowait(("err", exc))

            threading.Thread(target=_run, daemon=True).start()
            try:
                status, value = result.get(timeout=self.timeout)
                if status == "ok":
                    return value
                raise value
            except Exception as exc:
                last_error = exc
                if attempt != self.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
        raise last_error

    def call_hard(self, method, params, hard_timeout=8.0):
        """call() under a WALL-CLOCK ceiling. urllib on Windows can hang in
        TLS read far past socket timeouts; run in a daemon thread and raise
        TimeoutError if it exceeds `hard_timeout`.
        """
        q = queue.Queue(maxsize=1)

        def _run():
            try:
                q.put_nowait(("ok", self.call(method, params)))
            except Exception as e:
                q.put_nowait(("err", e))

        threading.Thread(target=_run, daemon=True).start()
        st, v = q.get(timeout=hard_timeout)  # queue.Empty on stall
        if st == "err":
            raise v
        return v

    def eth_call(self, to, data, block="latest"):
        return self.call("eth_call", [{"to": to, "data": data}, block])

    def eth_call_hard(self, to, data, block="latest", hard_timeout=8.0):
        return self.call_hard("eth_call",
                              [{"to": to, "data": data}, block],
                              hard_timeout=hard_timeout)

    def get_balance(self, addr, block="latest"):
        return self.call("eth_getBalance", [addr, block])

    def get_code(self, addr, block="latest"):
        return self.call("eth_getCode", [addr, block])


def uint(result):
    if result in ("0x", "", None):
        return None
    return int(result, 16)