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
        timeout when the peer silently drops. This wrapper kills it.
        """
        q = queue.Queue()

        def _do():
            try:
                q.put(urllib.request.urlopen(req, timeout=self.timeout))
            except Exception as e:
                q.put(e)

        t = threading.Thread(target=_do, daemon=True)
        t.start()
        t.join(self.timeout + 5)
        if not q.empty():
            return q.get()
        raise RuntimeError(f"RPC timeout after {self.timeout}s: {self.url}")

    def _call(self, payload):
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = dict(payload)
        payload.setdefault("id", self._id + 1)
        self._id = max(self._id, payload.get("id", 0))
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url, data=data, headers={"Content-Type": "application/json"})
        resp = self._hard_open(req)
        if isinstance(resp, Exception):
            raise resp
        body = resp.read()
        obj = json.loads(body)
        if obj.get("error"):
            raise RuntimeError(obj["error"])
        return obj.get("result")

    def eth_call(self, to, data, block="latest"):
        return self._call({
            "jsonrpc": "2.0", "method": "eth_call",
            "params": [{"to": to, "data": data}, block],
        })

    def eth_getBalance(self, addr, block="latest"):
        return self._call({
            "jsonrpc": "2.0", "method": "eth_getBalance",
            "params": [addr, block],
        })

    def eth_getCode(self, addr, block="latest"):
        return self._call({
            "jsonrpc": "2.0", "method": "eth_getCode",
            "params": [addr, block],
        })

    def eth_blockNumber(self):
        return int(self._call({
            "jsonrpc": "2.0", "method": "eth_blockNumber", "params": []
        }), 16)

    def eth_getTransactionCount(self, addr, block="pending"):
        return int(self._call({
            "jsonrpc": "2.0", "method": "eth_getTransactionCount",
            "params": [addr, block],
        }), 16)

    def eth_gasPrice(self):
        return int(self._call({
            "jsonrpc": "2.0", "method": "eth_gasPrice", "params": []
        }), 16)

    def eth_sendRawTransaction(self, signed_hex):
        return self._call({
            "jsonrpc": "2.0", "method": "eth_sendRawTransaction",
            "params": [signed_hex],
        })

    def eth_getTransactionReceipt(self, txhash):
        return self._call({
            "jsonrpc": "2.0", "method": "eth_getTransactionReceipt",
            "params": [txhash],
        })

    def wait_for_tx(self, txhash, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.eth_getTransactionReceipt(txhash)
            if r:
                return r
            time.sleep(1.5)
        raise RuntimeError(f"tx {txhash} not mined in {timeout}s")


class ForkClient:
    """Forked read-only execution client wrapper."""

    def __init__(self, rpc: RPC):
        self.rpc = rpc
        self.snapshots = {}
        self._next = 1

    def snapshot(self, label=None):
        label = label or f"s{self._next}"
        self._next += 1
        # Some forks support evm_snapshot; ignore if unavailable.
        try:
            self.rpc._call({
                "jsonrpc": "2.0", "id": 1, "method": "evm_snapshot", "params": []
            })
        except Exception:
            pass
        self.snapshots[label] = True
        return label

    def revert(self, label=None):
        if label and label in self.snapshots:
            try:
                self.rpc._call({
                    "jsonrpc": "2.0", "id": 1, "method": "evm_revert",
                    "params": [label],
                })
            except Exception:
                pass
            self.snapshots.pop(label, None)

    def call(self, to, data, block="latest"):
        return self.rpc.eth_call(to, data, block=block)


def uint(result):
    if result in ("0x", "", None):
        return None
    return int(result, 16)
