#!/usr/bin/env python3
"""ops/start_engines.py — detached, session-proof engine starter.

The engines died 2026-08-24 ~18:30 because `nohup python3 ... &` children
are killed when the owning bash session closes on Windows (no setsid).
This starter uses PowerShell Start-Process -WindowStyle Hidden, which
fully detaches: the engines survive terminal/gateway/Hermes restarts.

Usage:
    python3 ops/start_engines.py          # start hunter if not running
    python3 ops/start_engines.py --check  # just report status
"""
import os
from pathlib import Path
import subprocess
import sys
import time

PY = r"C:\Users\timot\AppData\Local\Programs\Python\Python312\python.exe"
VERITAS = r"C:\Users\timot\OneDrive\Documents\VERITAS"
HUNTER = VERITAS + r"\flash_hunter.py"
HUNTER_OUT = VERITAS + r"\flash_hunter_run.out"
LIVENESS_LOGS = (
    "flash_hunter.log", "flash_hunter_run.out", "hunter_stdout.log", "hunter_full.log",
)
LIVENESS_MAX_AGE_SEC = 180

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


def newest_log_age() -> float | None:
    """Age of the newest hunter log; None means no liveness evidence exists."""
    mtimes = []
    for name in LIVENESS_LOGS:
        path = Path(VERITAS) / name
        try:
            mtimes.append(path.stat().st_mtime)
        except FileNotFoundError:
            pass
    return None if not mtimes else time.time() - max(mtimes)


def stop_pids(pids: list[int]) -> None:
    """Terminate known stale detached Python hunter processes."""
    for pid in pids:
        subprocess.run(["powershell", "-Command", f"Stop-Process -Id {pid} -Force"],
                       capture_output=True, text=True, timeout=30)


def start(script, wd) -> int:
    """Start fully detached, return PID. No stream redirection — engines
    self-log (flash_hunter.log), and holding stdout handles makes the
    launcher hang. Caller checks not already running."""
    cmd = PS.format(py=PY, script=script, wd=wd)
    pid = subprocess.run(["powershell", "-Command", cmd],
                         capture_output=True, text=True, timeout=60)
    return int(pid.stdout.strip())


def main():
    check_only = "--check" in sys.argv
    report = []

    for name, script, wd, out in (
        ("hunter", HUNTER, VERITAS, HUNTER_OUT),
    ):
        pids = running(name)
        age = newest_log_age() if name == "hunter" else None
        stale = bool(pids) and (age is None or age > LIVENESS_MAX_AGE_SEC)
        if pids and not stale:
            report.append(f"{name}: RUNNING pid={pids} newest_log_age={age:.0f}s")
            continue
        if pids and stale:
            detail = "no hunter logs" if age is None else f"newest_log_age={age:.0f}s"
            if check_only:
                report.append(f"{name}: STALE pid={pids} {detail}")
                continue
            stop_pids(pids)
            report.append(f"{name}: RESTARTING stale pid={pids} {detail}")
        if check_only:
            report.append(f"{name}: DOWN")
            continue
        try:
            pid = start(script, wd)
            report.append(f"{name}: STARTED pid={pid}")
        except Exception as e:
            report.append(f"{name}: START FAILED {e}")

    print("\n".join(report))
    sys.exit(0)


if __name__ == "__main__":
    main()