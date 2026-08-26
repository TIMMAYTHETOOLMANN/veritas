#!/usr/bin/env python3
"""ops/start_engines.py — detached, session-proof engine starter.

The engines died 2026-08-24 ~18:30 because `nohup python3 ... &` children
are killed when the owning bash session closes on Windows (no setsid).
This starter uses PowerShell Start-Process -WindowStyle Hidden, which
fully detaches: the engines survive terminal/gateway/Hermes restarts.

Usage:
    python3 ops/start_engines.py          # start hunter + carry if not running
    python3 ops/start_engines.py --check  # just report status
"""
import subprocess
import sys

PY = r"C:\Users\timot\AppData\Local\Programs\Python\Python312\python.exe"
VERITAS = r"C:\Users\timot\OneDrive\Documents\VERITAS"
HUNTER = VERITAS + r"\flash_hunter.py"
CARRY = VERITAS + r"\hyperliquid\carry_engine.py"
HUNTER_OUT = VERITAS + r"\flash_hunter_run.out"
CARRY_OUT = VERITAS + r"\hyperliquid\carry_engine_run.out"

PS = (
    "$p = Start-Process -FilePath '{py}' -ArgumentList '{script}' "
    "-WorkingDirectory '{wd}' -WindowStyle Hidden -PassThru; $p.Id"
)


def running(script_name: str) -> list:
    """PIDs of python processes whose command line mentions script_name."""
    out = subprocess.run(
        ["powershell", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
         f"Where-Object {{$_.CommandLine -like '*{script_name}*'}} | "
         "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=60).stdout
    return [int(l) for l in out.split() if l.strip().isdigit()]


def start(script, wd) -> int:
    """Start fully detached, return PID. No stream redirection — engines
    self-log (flash_hunter.log / carry_engine.log), and holding stdout
    handles makes the launcher hang. Caller checks not already running."""
    cmd = PS.format(py=PY, script=script, wd=wd)
    pid = subprocess.run(["powershell", "-Command", cmd],
                         capture_output=True, text=True, timeout=60)
    return int(pid.stdout.strip())


def main():
    check_only = "--check" in sys.argv
    report = []

    for name, script, wd, out in (
        ("hunter", HUNTER, VERITAS, HUNTER_OUT),
        ("carry", CARRY, VERITAS + r"\hyperliquid", CARRY_OUT),
    ):
        pids = running(name)
        if pids:
            report.append(f"{name}: ALREADY RUNNING pid={pids}")
            continue
        if check_only:
            report.append(f"{name}: DOWN")
            continue
        try:
            pid = start(script, wd)
            report.append(f"{name}: STARTED pid={pid}")
        except Exception as e:
            report.append(f"{name}: START FAILED {e}")

    print("\n".join(report))
    if check_only:
        sys.exit(0)
    # exit 1 if anything is down or failed to start
    bad = any(("DOWN" in r or "FAILED" in r) for r in report)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
