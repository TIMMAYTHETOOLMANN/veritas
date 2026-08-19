# core/rpc.py — minimal JSON-RPC client (retries, eth_call/balance/code)
import json, time, urllib.request

class RPC:
    def __init__(self, url, timeout=20, retries=3, user_agent=None):
        self.url, self.timeout, self.retries = url, timeout, retries
        self._id = 0
        # Browser-like UA so public/archive RPC gateways (drpc, publicnode,
        # tenderly) behind Cloudflare bot-wall don't 403 (http error 1010)
        # on high-volume eth_call dif. Veritas is a read-only client.
        self._ua = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

    def call(self, method, params):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    self.url, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json",
                             "User-Agent": self._ua})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    out = json.loads(r.read())
                if "error" in out:
                    raise RuntimeError(f"rpc {method}: {out['error']}")
                return out["result"]
            except Exception:
                if attempt == self.retries - 1:
                    raise
                time.sleep(1.5 * (attempt + 1))

    def eth_call(self, to, data, block="latest"):
        return self.call("eth_call", [{"to": to, "data": data}, block])

    def get_balance(self, addr, block="latest"):
        return int(self.call("eth_getBalance", [addr, block]), 16)

    def get_code(self, addr, block="latest"):
        return self.call("eth_getCode", [addr, block])

def uint(result):
    if result in ("0x", "", None): return None
    return int(result, 16)
