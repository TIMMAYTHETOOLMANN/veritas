#!/usr/bin/env python3
"""
VERITAS Active Monitor — runs continuously until $30 profit pool reached.
Monitors: carry_engine, flash_hunter, HL equity, realized P&L, process health.
Alerts on: process death, emergency stops, equity drops, scan failures.
"""
import os
import sys
import time
import json
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyperliquid.info import Info

# =============== CONFIG ===============
TARGET_PROFIT_USD = 30.0
CHECK_INTERVAL_SEC = 60
HL_ADDR = "0x1a0d467974E70e3c1a2b7b84Fec21183Fc4eB60f"
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hyperliquid")
CARRY_LOG = os.path.join(LOG_DIR, "carry_engine.log")
HUNTER_LOG = os.path.join(LOG_DIR, "flash_hunter.log")
MONITOR_LOG = os.path.join(LOG_DIR, "active_monitor.log")

# =============== HELPERS ===============
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(MONITOR_LOG, "a") as f:
        f.write(line + "\n")

def get_hl_state():
    info = Info(skip_ws=True)
    st = info.user_state(HL_ADDR)
    equity = float(st['marginSummary']['accountValue'])
    positions = []
    for ap in st.get('assetPositions', []):
        p = ap['position']
        if float(p.get('szi', 0)) != 0:
            positions.append({
                'coin': p['coin'],
                'size': float(p['szi']),
                'entry': float(p['entryPx']),
                'uPnL': float(p['unrealizedPnl']),
                'liq': float(p['liquidationPx']),
            })
    return equity, positions

def parse_carry_log():
    """Parse last few lines of carry_engine.log for session_realized, emergency stops, etc."""
    if not os.path.exists(CARRY_LOG):
        return {}
    with open(CARRY_LOG, "r") as f:
        lines = f.readlines()
    # Get last 50 lines
    recent = lines[-50:]
    session_realized = 0.0
    emergency_stop = False
    last_heartbeat = None
    last_position = None
    for line in recent:
        if "HEARTBEAT:" in line:
            last_heartbeat = line.strip()
            # Extract session_realized
            import re
            m = re.search(r"session_realized=\$([\d.]+)", line)
            if m:
                session_realized = float(m.group(1))
        if "emergency stop" in line.lower():
            emergency_stop = True
        if "ENTER" in line and ("LONG" in line or "SHORT" in line):
            last_position = line.strip()
    return {
        "session_realized": session_realized,
        "emergency_stop": emergency_stop,
        "last_heartbeat": last_heartbeat,
        "last_position": last_position,
    }

def parse_hunter_log():
    """Parse flash_hunter.log for last scan, ETH price, edges, errors."""
    if not os.path.exists(HUNTER_LOG):
        return {}
    with open(HUNTER_LOG, "r") as f:
        lines = f.readlines()
    recent = lines[-30:]
    last_scan = None
    eth_usd = None
    edges = 0
    error = False
    for line in reversed(recent):
        if "cross-scan:" in line and eth_usd is None:
            import re
            m = re.search(r"ETH \$([\d.]+)", line)
            if m:
                eth_usd = float(m.group(1))
            m = re.search(r"(\d+) edges", line)
            if m:
                edges = int(m.group(1))
        if "error" in line.lower() or "exception" in line.lower() or "traceback" in line.lower():
            error = True
    return {
        "last_scan": last_scan,
        "eth_usd": eth_usd,
        "edges": edges,
        "error": error,
    }

def check_processes():
    """Check if carry_engine.py and flash_hunter.py --run are running."""
    import psutil
    carry_pids = []
    hunter_pids = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = ' '.join(p.info['cmdline'] or [])
            if 'carry_engine.py' in cmd:
                carry_pids.append(p.info['pid'])
            if 'flash_hunter.py' in cmd and '--run' in cmd:
                hunter_pids.append(p.info['pid'])
        except:
            pass
    return carry_pids, hunter_pids

def alert(msg):
    """Critical alert - log prominently."""
    log(f"🚨 ALERT: {msg}")

# =============== MAIN LOOP ===============
def main():
    log(f"=== VERITAS ACTIVE MONITOR STARTED (target: ${TARGET_PROFIT_USD}) ===")
    
    consecutive_errors = 0
    last_carry_check = None
    last_hunter_check = None
    
    while True:
        try:
            # --- 1. Process health ---
            carry_pids, hunter_pids = check_processes()
            if not carry_pids:
                alert("carry_engine.py NOT RUNNING — restarting...")
                subprocess.Popen(
                    ["python3", "carry_engine.py"],
                    cwd=os.path.join(os.path.dirname(__file__), "hyperliquid"),
                    stdout=open(CARRY_LOG, "a"),
                    stderr=subprocess.STDOUT,
                )
                time.sleep(5)
            if not hunter_pids:
                alert("flash_hunter.py --run NOT RUNNING — restarting...")
                subprocess.Popen(
                    ["python3", "flash_hunter.py", "--run"],
                    cwd=os.path.dirname(__file__),
                    stdout=open(HUNTER_LOG, "a"),
                    stderr=subprocess.STDOUT,
                )
                time.sleep(5)
            
            # --- 2. HL State ---
            equity, positions = get_hl_state()
            
            # --- 3. Carry Engine Log ---
            carry = parse_carry_log()
            
            # --- 4. Flash Hunter Log ---
            hunter = parse_hunter_log()
            
            # --- 5. Profit Calculation ---
   
            # Total profit = current equity - initial deposit (15.66)
            # This is always accurate and real-time; session_realized lags position cycling
            initial_deposit = 15.66
            total_profit = equity - initial_deposit
            
            # --- 6. Status Line ---
            pos_str = ", ".join([f"{p['coin']} {p['size']:.0f}@{p['entry']:.4f} uPnL={p['uPnL']:.2f}" for p in positions]) or "FLAT"
            hunter_eth = hunter.get('eth_usd')
            hunter_str = f"ETH=${hunter_eth:.0f} edges={hunter.get('edges',0)}" if hunter_eth else "no recent scan"
            
            log(f"PROFIT: ${total_profit:.2f}/${TARGET_PROFIT_USD} | Equity: ${equity:.2f} | Realized: ${carry.get('session_realized', 0.0):.2f} | Pos: {pos_str} | Hunter: {hunter_str} | Carry PIDs: {len(carry_pids)} Hunter PIDs: {len(hunter_pids)}")
            
            # --- 7. Anomaly Checks ---
            # Emergency stop detection
            if carry.get('emergency_stop') and last_carry_check != carry.get('last_heartbeat'):
                alert(f"Emergency stop detected: {carry.get('last_heartbeat')}")
                last_carry_check = carry.get('last_heartbeat')
            
            # Hunter stale ETH price (stale V3 quoter fallback)
            if hunter_eth and hunter_eth > 10000:
                alert(f"Hunter using stale ETH price: ${hunter_eth:.0f}")
            
            # Hunter scan failure
            if hunter.get('error'):
                alert("Hunter log shows error/exception")
            
            # Process death detection (redundant with #1 but explicit)
            if not carry_pids:
                alert("carry_engine process DIED")
            if not hunter_pids:
                alert("flash_hunter process DIED")
            
            # --- 8. Target Check ---
            if total_profit >= TARGET_PROFIT_USD:
                log(f"🎯 TARGET REACHED: ${total_profit:.2f} >= ${TARGET_PROFIT_USD}")
                break
            
            consecutive_errors = 0
            
        except Exception as e:
            consecutive_errors += 1
            alert(f"Monitor error #{consecutive_errors}: {e}")
            if consecutive_errors >= 5:
                alert("Too many consecutive errors — monitor stopping")
                break
        
        time.sleep(CHECK_INTERVAL_SEC)
    
    log("=== MONITOR STOPPED ===")

if __name__ == "__main__":
    main()