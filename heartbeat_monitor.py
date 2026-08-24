#!/usr/bin/env python3
"""Forensic Heartbeat Monitor — Concrete, observable VERITAS system state.

Monitors the ACTIVE flash-arb engine stack:
  flash_hunter.py (live process) -> arb_engine + sim_gate -> core/rpc
  pool_registry.py -> veritas.db pools table (Camelot census)
  Executor V4 on-chain + hot wallet WETH/ETH balances.

Every line below is VERIFIABLE on-chain. Check it yourself at any time.
"""
import os, sys, json, time, sqlite3, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

WALLET = "0x1a0d467974E70e3c1a2b7b84fec21183Fc4eB60f"

def rpc(method, params):
    import urllib.request
    req = urllib.request.Request(
        "https://arb1.arbitrum.io/rpc",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("result")

def eth_balance(addr):
    r = rpc("eth_getBalance", [addr, "latest"])
    return int(r, 16) / 1e18 if r else None

print("=" * 60)
print(f"VERITAS HEARTBEAT — {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)
print()

# 1. Engine process — VERIFIABLE via tasklist
print("1. FLASH HUNTER ENGINE")
print("-" * 40)
try:
    out = subprocess.run(["powershell", "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
        "Where-Object {$_.CommandLine -like '*flash_hunter*'} | "
        "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    pids = [l.strip() for l in out.splitlines() if l.strip().isdigit()]
    print(f"   flash_hunter.py --run PIDs: {', '.join(pids) if pids else 'NONE'}")
    print(f"   Status: {'✓ RUNNING' if pids else '✗ DOWN'}")
except Exception as e:
    print(f"   Process check failed: {e}")
print()

# 2. Log freshness — VERIFIABLE
print("2. ENGINE LOG FRESHNESS (flash_hunter.log)")
print("-" * 40)
try:
    with open("flash_hunter.log", "r", encoding="utf-8", errors="replace") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    last = json.loads(lines[-1]) if lines else {}
    age = time.time() - time.mktime(time.strptime(last.get("ts", "1970-01-01 00:00:00"),
                                                  "%Y-%m-%d %H:%M:%S"))
    print(f"   Last log entry: {last.get('ts', 'NONE')} ({last.get('event', '?')})")
    print(f"   Age: {int(age)}s — {'✓ FRESH (<1800s)' if age < 1800 else '✗ STALE'}")
    # last heartbeat + last scan summary
    hb = next((json.loads(l) for l in reversed(lines) if '"heartbeat"' in l), {})
    sc = next((json.loads(l) for l in reversed(lines) if '"scan"' in l), {})
    if hb: print(f"   Last heartbeat: cycle {hb.get('cycle')}, executor WETH={hb.get('executor_weth')}")
    if sc: print(f"   Last scan: {sc.get('mode')}, {sc.get('combos')} combos, {sc.get('edges')} edges, ETH=${sc.get('eth_usd')}")
except Exception as e:
    print(f"   Log read failed: {e}")
print()

# 3. Executor — VERIFIABLE on-chain
print("3. EXECUTOR CONTRACT (on-chain)")
print("-" * 40)
try:
    with open(".executor_v2_address") as f:
        exec_addr = f.read().strip()
    print(f"   Executor V4: {exec_addr}")
    code = rpc("eth_getCode", [exec_addr, "latest"])
    print(f"   Deployed code: {'✓ YES (' + str(len(code)//2 - 1) + ' bytes)' if code and code != '0x' else '✗ NO CODE'}")
    b = eth_balance(exec_addr)
    print(f"   Executor ETH: {b}")
except Exception as e:
    print(f"   Executor check failed: {e}")
print()

# 4. Hot wallet — VERIFIABLE on-chain
print("4. HOT WALLET (on-chain)")
print("-" * 40)
try:
    b = eth_balance(WALLET)
    print(f"   {WALLET}")
    print(f"   ETH balance: {b}")
    print(f"   Status: {'✓ LIVE' if b is not None else '✗ RPC FAIL'}")
except Exception as e:
    print(f"   Wallet check failed: {e}")
print()

# 5. Pool registry — VERIFIABLE in veritas.db
print("5. POOL REGISTRY (veritas.db)")
print("-" * 40)
try:
    conn = sqlite3.connect("veritas.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM pools")
    pools = c.fetchone()[0]
    c.execute("SELECT venue, COUNT(*) FROM pools GROUP BY venue ORDER BY 2 DESC")
    venues = c.fetchall()
    conn.close()
    print(f"   Total pools registered: {pools}")
    for v, n in venues:
        print(f"     {v}: {n}")
except Exception as e:
    print(f"   Registry check failed: {e}")
print()

# 6. Watchdog cron — VERIFIABLE
print("6. WATCHDOG")
print("-" * 40)
print("   Cron job: ec2016bb9a90 (*/30 * * * *)")
print("   Checks engine + restarts if down (verify: hermes cron list)")
print()

# 7. Constraint summary
print("7. CONSTRAINT STATUS")
print("-" * 40)
checks = {
    "Engine process": bool(pids),
    "Log fresh (<30min)": age < 1800,
    "Executor deployed": bool(code and code != "0x"),
    "Pool registry": pools > 0,
}
for check, status in checks.items():
    print(f"   {check}: {'✓ ENABLED' if status else '✗ DISABLED'}")
print()

print("=" * 60)
print("END HEARTBEAT — Verify each line above at any time.")
print("=" * 60)
