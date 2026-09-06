# core/rpc.py — minimal JSON-RPC client (retries, eth_call/balance/code)
import json
import time
import urllib.request
import urllib.parse as _urlparse
import threading
import queue

# ---- process-level DNS cache ------------------------------------------------
# RPC endpoints are stable; resolve once (direct, DoH fallback) and reuse.
# This immunizes the engine against local resolver flaps/hangs entirely.
import socket as _socket
import time as _time

_dns_cache = {}
_dns_ttl = 3600.0
_orig_getaddrinfo = _socket.getaddrinfo


def _resolve_once(host, timeout=5.0):
    """Direct getaddrinfo with a hard wall-clock cap (resolver can hang)."""
    q = []
    def _go():
        try:
            q.append(_orig_getaddrinfo(host, 443))
        except Exception as e:
            q.append(e)
    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(timeout)
    return q[0] if q else None


def _doh_resolve(host):
    """DNS-over-HTTPS via 1.1.1.1 (IP literal -- no DNS needed to reach it).
    Returns a getaddrinfo-shaped result or None."""
    try:
        import json as _j
        u = 'https://1.1.1.1/dns-query?name=%s&type=A' % host
        req = urllib.request.Request(
            u, headers={'accept': 'application/dns-json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _j.loads(r.read())
        for a in data.get('Answer', []):
            if a.get('type') == 1:
                ip = a['data']
                return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, '', (ip, 443))]
    except Exception:
        return None
    return None


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    # NOTE: key is (host, family, type) -- socket.create_connection requests
    # type=SOCK_STREAM, so a naive full-args key never hits the cache.
    key = (host, family, type)
    now = _time.time()
    hit = _dns_cache.get(key)
    if hit and now - hit[0] < _dns_ttl:
        return hit[1]
    res = _resolve_once(host, 5.0)
    if isinstance(res, Exception) or res is None:
        res = _doh_resolve(host)
    if res and not isinstance(res, Exception):
        _dns_cache[key] = (now, res)
        return res
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def _dns_invalidate(host):
    if not host:
        return
    for key in [k for k in _dns_cache if k[0] == host]:
        _dns_cache.pop(key, None)


def prime_dns(urls, attempts=20, delay=2.0):
    """Resolve every RPC hostname once; afterwards the run loop is fully
    served from the cache."""
    import urllib.parse as _up
    hosts = []
    for u in urls:
        try:
            h = _up.urlparse(u).hostname
            if h and h not in hosts and not h.startswith('127.'):
                hosts.append(h)
        except Exception:
            pass
    for h in hosts:
        for attempt in range(attempts):
            res = _resolve_once(h, 5.0)
            via = 'direct'
            if isinstance(res, Exception) or res is None:
                res = _doh_resolve(h)
                via = 'DoH'
            if res and not isinstance(res, Exception):
                _dns_cache[(h, 0, 1)] = (_time.time(), res)
                print(f"[dns] primed {h} ({via})", flush=True)
                break
            _time.sleep(delay)
        else:
            print(f"[dns] WARNING: {h} never resolved", flush=True)





def _dns_invalidate(host):
    """Drop cached entries for host so the next call re-resolves."""
    if not host:
        return
    for key in [k for k in _dns_cache if k[0] == host]:
        _dns_cache.pop(key, None)


def _resolve_once(host, timeout=5.0):
    """Direct getaddrinfo with a hard wall-clock cap (resolver can hang)."""
    q = []
    def _go():
        try:
            q.append(_orig_getaddrinfo(host, 443))
        except Exception as e:
            q.append(e)
    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(timeout)
    return q[0] if q else None


def _doh_resolve(host):
    """DNS-over-HTTPS via 1.1.1.1 (IP literal -- no DNS needed to reach it).
    Returns a getaddrinfo-shaped result or None."""
    try:
        import json as _j
        u = 'https://1.1.1.1/dns-query?name=%s&type=A' % host
        req = urllib.request.Request(
            u, headers={'accept': 'application/dns-json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _j.loads(r.read())
        for a in data.get('Answer', []):
            if a.get('type') == 1:
                ip = a['data']
                return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, '', (ip, 443))]
    except Exception:
        return None
    return None


def prime_dns(urls, attempts=20, delay=2.0):
    """Resolve every RPC hostname once (direct, then DoH fallback), caching
    the result so the run loop never touches the OS resolver again."""
    import urllib.parse as _up
    hosts = []
    for u in urls:
        try:
            h = _up.urlparse(u).hostname
            if h and h not in hosts and not h.startswith('127.'):
                hosts.append(h)
        except Exception:
            pass
    for h in hosts:
        for attempt in range(attempts):
            res = _resolve_once(h, 5.0)
            via = 'direct'
            if isinstance(res, Exception) or res is None:
                res = _doh_resolve(h)
                via = 'DoH'
            if res:
                _dns_cache[(h, 443, 0, 0, 0, 0)] = (_time.time(), res)
                print(f"[dns] primed {h} ({via})", flush=True)
                break
            _time.sleep(delay)
        else:
            print(f"[dns] WARNING: {h} never resolved", flush=True)



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
        last_exc = None
        for attempt in range(max(1, self.retries)):
            req = urllib.request.Request(
                self.url, data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
            resp = self._hard_open(req)
            if isinstance(resp, Exception):
                last_exc = resp
                _dns_invalidate(_urlparse.urlparse(self.url).hostname)
                time.sleep(0.4 * (attempt + 1))
                continue
            try:
                body = resp.read()
            except Exception as e:
                last_exc = e
                _dns_invalidate(_urlparse.urlparse(self.url).hostname)
                time.sleep(0.4 * (attempt + 1))
                continue
            last_exc = None
            break
        if last_exc is not None:
            raise last_exc

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



    # --- convenience aliases used by flash_hunter broadcast paths ---
    def nonce(self, addr, block="pending"):
        return self.eth_getTransactionCount(addr, block)

    def gas_price(self):
        return self.eth_gasPrice()

    def wait_receipt(self, txhash, timeout=120):
        return self.wait_for_tx(txhash, timeout)

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


class FleetRPC(RPC):
    """Round-robins calls across several endpoints (spreads per-host rate
    limits) and fails over to the next endpoint on any error. All RPC
    methods funnel through _call, so they all rotate automatically."""

    def __init__(self, urls, timeout=20, retries=2):
        self._pool = [RPC(u, timeout=timeout, retries=1) for u in urls]
        self._idx = 0
        self.url = self._pool[0].url
        self.timeout = timeout
        self.retries = retries

    def _call(self, payload):
        last = None
        for _ in range(len(self._pool)):
            rpc = self._pool[self._idx % len(self._pool)]
            self._idx += 1
            try:
                return rpc._call(payload)
            except Exception as e:
                last = e
        raise last

