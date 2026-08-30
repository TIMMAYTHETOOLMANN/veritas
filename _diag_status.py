#!/usr/bin/env python3
"""Comprehensive system status diagnostic for VERITAS."""
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
OUT = os.path.join(HERE, "_diag_status_out.txt")
WALLET = "0x1a0d467974e70e3c1a2b7b84fec21183fc4eb60f"

lines = []
def emit(s=""):
    lines.append(s)

def rpc(method, params):
    req = urllib.request.Request(
        "https://arb1.arbitrum.io/rpc",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("result")

def eth_balance(addr):
    r = rpc("eth_getBalance", [addr, "latest"])
    return int(r, 16) / 1e18 if r else None

emit("=" * 60)
emit(f"VERITAS SYSTEM STATUS — {time.strftime('%Y-%m-%d %H:%M:%S')}")
emit("=" * 60)
emit("")

# 1. Engine processes
emit("1. FLASH HUNTER ENGINE PROCESSES")
emit("-" * 40)
try:
    out = subprocess.run(["powershell", "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
        "Where-Object {$_.CommandLine -like '*flash_hunter*'} | "
        "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    pids = [l.strip() for l in out.splitlines() if l.strip().isdigit()]
    emit(f"   flash_hunter.py PIDs: {', '.join(pids) if pids else 'NONE'}")
    emit(f"   Status: {'RUNNING' if pids else 'DOWN'}")
except Exception as e:
    emit(f"   process check failed: {e}")
emit("")

# 1b. All python processes
emit("1b. ALL PYTHON PROCESSES")
try:
    out = subprocess.run(["powershell", "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
        "Select-Object ProcessId, CommandLine | Format-List"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    emit(out[:3000] if out else "   none")
except Exception as e:
    emit(f"   failed: {e}")
emit("")

# 2. Log freshness
emit("2. FLASH HUNTER LOG")
emit("-" * 40)
try:
    with open("flash_hunter.log", "r", encoding="utf-8", errors="replace") as f:
        all_lines = [l for l in f.read().splitlines() if l.strip()]
    emit(f"   total log lines: {len(all_lines)}")
    if all_lines:
        last = json.loads(all_lines[-1]) if all_lines else {}
        emit(f"   last entry: {last}")
    # count events
    counts = {}
    for l in all_lines:
        try:
            e = json.loads(l).get("event", "?")
            counts[e] = counts.get(e, 0) + 1
        except Exception:
            pass
    emit(f"   event counts: {counts}")
    # find any broadcast/sim events
    broadcast_events = [l for l in all_lines if '"broadcast"' in l]
    sim_pass = [l for l in all_lines if '"PASS"' in l]
    emit(f"   broadcast events: {len(broadcast_events)}")
    emit(f"   PASS events: {len(sim_pass)}")
    if broadcast_events:
        emit("   last broadcast:")
        emit("   " + broadcast_events[-1])
except Exception as e:
    emit(f"   log read failed: {e}")
emit("")

# 3. Executor
emit("3. EXECUTOR CONTRACTS")
emit("-" * 40)
for fname in [".executor_address", ".executor_v2_address", ".executor_v3_address"]:
    if os.path.exists(fname):
        with open(fname) as f:
            addr = f.read().strip()
        try:
            code = rpc("eth_getCode", [addr, "latest"])
            has_code = bool(code and code != "0x")
            b = eth_balance(addr)
            emit(f"   {fname}: {addr} code={'YES' if has_code else 'NO'} eth={b}")
        except Exception as e:
            emit(f"   {fname}: {addr} [rpc error: {e}]")
    else:
        emit(f"   {fname}: NOT PRESENT")
emit("")

# 4. Hot wallet
emit("4. HOT WALLET")
emit("-" * 40)
try:
    b = eth_balance(WALLET)
    emit(f"   {WALLET}")
    emit(f"   ETH: {b}")
except Exception as e:
    emit(f"   wallet check failed: {e}")
emit("")

# 5. Pool registry
emit("5. POOL REGISTRY (veritas.db)")
emit("-" * 40)
try:
    conn = sqlite3.connect("veritas.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM pools")
    pools = c.fetchone()[0]
    c.execute("SELECT venue, kind, COUNT(*) FROM pools GROUP BY venue, kind ORDER BY 3 DESC")
    venues = c.fetchall()
    conn.close()
    emit(f"   total pools: {pools}")
    for v, k, n in venues:
        emit(f"     {v} {k}: {n}")
except Exception as e:
    emit(f"   registry failed: {e}")
emit("")

# 5b. WETH-paired pools with depth
try:
    conn = sqlite3.connect("veritas.db")
    c = conn.cursor()
    c.execute("""SELECT COUNT(*) FROM pools WHERE (token0='0x82af49447d8a07e3bd95bd0d56f35241523fbab1' OR token1='0x82af49447d8a07e3bd95bd0d56f35241523fbab1') AND usd_depth > 0""")
    weth_pools = c.fetchone()[0]
    c.execute("""SELECT COUNT(*) FROM pools WHERE usd_depth > 0""")
    with_depth = c.fetchone()[0]
    conn.close()
    emit(f"   WETH-paired with depth: {weth_pools}")
    emit(f"   total with depth: {with_depth}")
except Exception as e:
    emit(f"   registry detail failed: {e}")
emit("")

# 6. Vetted targets
emit("6. VETTED TARGETS (vetted_targets.jsonl)")
emit("-" * 40)
try:
    with open("vetted_targets.jsonl", "r", encoding="utf-8", errors="replace") as f:
        v_lines = [l for l in f.read().splitlines() if l.strip()]
    emit(f"   total target lines: {len(v_lines)}")
    if v_lines:
        last_target = json.loads(v_lines[-1])
        emit(f"   last target: {json.dumps(last_target)[:500]}")
except Exception as e:
    emit(f"   vetted failed: {e}")
emit("")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written to", OUT)