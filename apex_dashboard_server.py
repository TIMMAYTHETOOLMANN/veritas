#!/usr/bin/env python3
"""
apex_dashboard_server.py — Live GUI backend for the Apex funding-carry engine.

Serves a real-time JSON snapshot of the Hyperliquid account + the top funding
carry opportunities so the companion HTML dashboard can render a living view
of equity, positions, P&L, funding edges, and the compounding loop — NO
fabricated data, everything pulled live from api.hyperliquid.xyz.

Read-only. Stdlib only. Polls Hyperliquid on each request (cheap, ~2 calls).

Usage:  python3 apex_dashboard_server.py          # serves on 127.0.0.1:8999
"""
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.hyperliquid.xyz/info"
PORT = 8999

ADDR_FILE = os.path.join(HERE, ".hl_master_address")
DEFAULT_ADDR = "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"


def _post(payload):
    req = urllib.request.Request(API, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def user_address():
    try:
        with open(ADDR_FILE) as f:
            a = f.read().strip()
        return a or DEFAULT_ADDR
    except Exception:
        return DEFAULT_ADDR


def build_state():
    addr = user_address()
    out = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "now_unix": time.time(),
        "address": addr,
    }
    try:
        st = _post({"type": "clearinghouseState", "user": addr})
        ms = st.get("marginSummary", {})
        positions = []
        for ap in st.get("assetPositions", []):
            p = ap["position"]
            positions.append({
                "coin": p["coin"],
                "szi": float(p["szi"]),
                "entry": float(p["entryPx"]),
                "uPnl": float(p.get("unrealizedPnl", 0) or 0),
                "liq": p.get("liquidationPx"),
                "leverage": p.get("leverage", {}),
            })
        out["account_value"] = float(ms.get("accountValue", 0) or 0)
        out["total_ntl"] = float(ms.get("totalNtlPos", 0) or 0)
        out["margin_used"] = float(ms.get("totalMarginUsed", 0) or 0)
        out["positions"] = positions
    except Exception as e:
        out["error"] = f"clearinghouseState: {str(e)[:120]}"

    # spot USDC
    try:
        sp = _post({"type": "spotClearinghouseState", "user": addr})
        spot = 0.0
        for b in sp.get("balances", []) or []:
            if b.get("coin") == "USDC":
                spot = float(b.get("total", 0) or 0)
        out["spot_usdc"] = spot
    except Exception:
        out["spot_usdc"] = 0.0

    # top funding edges (for the opportunity feed)
    try:
        d = _post({"type": "metaAndAssetCtxs"})
        meta, ctxs = d[0], d[1]
        edges = []
        for i, m in enumerate(meta.get("universe", [])):
            if i >= len(ctxs):
                continue
            c = ctxs[i]
            funding = float(c.get("funding", 0) or 0)
            mark = float(c.get("markPx", 0) or 0)
            oi = float(c.get("openInterest", 0) or 0)
            premium = float(c.get("premium", 0) or 0)
            if abs(funding) < 0.0001 or (oi * mark) < 500_000 or mark <= 0:
                continue
            edges.append({
                "name": m["name"],
                "funding": funding,
                "premium": premium,
                "oi_ntl": oi * mark,
                "maxLev": m.get("maxLeverage", 3),
                "dir": "LONG" if funding < 0 else "SHORT",
            })
        edges.sort(key=lambda x: (abs(x["funding"]) if x["funding"] < 0 else abs(x["funding"]) * 0.3), reverse=True)
        out["edges"] = edges[:12]
    except Exception as e:
        out["edges"] = []

    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/state", "/api/state"):
            self._send(build_state())
        elif path == "/health":
            self._send({"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        else:
            self._send({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


def main():
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[apex-dash] listening on http://127.0.0.1:{PORT}/state")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[apex-dash] stopped")


if __name__ == "__main__":
    main()