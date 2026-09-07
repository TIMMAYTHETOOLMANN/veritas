#!/usr/bin/env python3
"""
veritas_dashboard_server.py — VERITAS live heartbeat dashboard backend.

Serves a real-time JSON snapshot of the VERITAS flash-loan arbitrage engine
for the companion HTML GUI (veritas_heartbeat_dashboard.html).

Reads THE ACTUAL sources of truth — no fabricated data:
  - flash_hunter.log   (JSONL: capital_state, cycle, zk_scan, sim, broadcast, ...)
  - hunter_stdout.log  (the live "[HH:MM:SS] cross-scan ... heartbeat ..." lines)
  - .hot_secret        (derives wallet address — never exposed raw)

Only stdlib. Polls file mtimes / re-reads on each request; no stateful crawl.
"""

import json
import os
import time
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE, "flash_hunter.log")
STDOUT_FILE = os.path.join(BASE, "hunter_stdout.log")
SECRET_FILE = os.path.join(BASE, ".hot_secret")
EXEC_FILE = os.path.join(BASE, ".executor_address")
EXEC_V2_FILE = os.path.join(BASE, ".executor_v2_address")
EXEC_ZK_FILE = os.path.join(BASE, ".executor_zk_address")

PORT = 8999


def read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def wallet_from_secret():
    """Derive the hot wallet address from .hot_secret (priv key) without exposing it."""
    sk = read_file(SECRET_FILE).strip()
    if not sk:
        return None
    sk = sk[2:] if sk.startswith("0x") else sk
    try:
        from eth_utils import to_checksum_address
        from eth_account import Account
        acct = Account.from_key(sk)
        return acct.address
    except Exception:
        return None


def tail_lines(path, n):
    """Return the last n non-empty lines of a file (O(1) memory-ish)."""
    txt = read_file(path)
    if not txt:
        return []
    lines = txt.splitlines()
    return lines[-n:] if len(lines) > n else lines


def parse_log_events(path, max_events=20000):
    """Parse JSONL log into a list of events + first/last ts."""
    events = []
    first_ts = last_ts = None
    txt = read_file(path)
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        events.append(e)
    return events


def build_state():
    """Assemble the live snapshot consumed by the GUI."""
    events = parse_log_events(LOG_FILE)
    stdout = read_file(STDOUT_FILE)

    # --- capital / P&L aggregate (latest capital_state wins) ---
    cap = {}
    for e in events:
        if e.get("event") == "capital_state":
            cap = e

    # --- recent scan lines from stdout (live cadence + ETH px + edges) ---
    scan_lines = []
    heartbeat_line = None
    for ln in reversed(stdout.splitlines()):
        if "heartbeat:" in ln and heartbeat_line is None:
            heartbeat_line = ln
        if "cross-scan:" in ln or "scan:" in ln:
            scan_lines.append(ln.strip())
        if len(scan_lines) >= 30:
            break
    scan_lines.reverse()

    # --- event history for the trade feed ---
    feed = []
    interesting = {"cycle", "zk_scan", "sim", "broadcast", "zk_proof",
                   "executor_zk_deployed", "cycle_error", "deploy", "broadcast_refused"}
    for e in reversed(events):
        ev = e.get("event")
        if ev in interesting:
            feed.append(e)
        if len(feed) >= 100:
            break
    feed.reverse()

    # --- cycle metrics ---
    cycle_count = sum(1 for e in events if e.get("event") == "cycle")
    zk_scan_count = sum(1 for e in events if e.get("event") == "zk_scan")
    error_count = sum(1 for e in events if e.get("event") == "cycle_error")

    last_cycle = None
    for e in reversed(events):
        if e.get("event") == "cycle":
            last_cycle = e
            break

    # timestamps
    last_ts = events[-1].get("ts") if events else None

    # wallet + executor addresses
    wallet = wallet_from_secret()
    exec_addr = read_file(EXEC_FILE).strip() or None
    exec_v2 = read_file(EXEC_V2_FILE).strip() or None
    exec_zk = read_file(EXEC_ZK_FILE).strip() or None

    # ETH price from scan lines (last "ETH $XXXX")
    eth_usd = None
    for ln in scan_lines:
        if "ETH $" in ln:
            try:
                eth_usd = float(ln.split("ETH $")[1].split(")")[0].split()[0])
            except Exception:
                pass
            break

    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "now_unix": time.time(),
        "capital": cap,
        "wallet": wallet,
        "executor": exec_addr,
        "executor_v2": exec_v2,
        "executor_zk": exec_zk,
        "eth_usd": eth_usd,
        "scan_lines": scan_lines[-20:],
        "heartbeat_line": heartbeat_line,
        "feed": feed[-80:],
        "counts": {
            "total_events": len(events),
            "cycles": cycle_count,
            "zk_scans": zk_scan_count,
            "errors": error_count,
        },
        "last_cycle": last_cycle,
        "last_ts": last_ts,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
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
        pass  # silence request spam


def main():
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[dashboard] VERITAS heartbeat server listening on http://127.0.0.1:{PORT}/state")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] stopped")


if __name__ == "__main__":
    main()