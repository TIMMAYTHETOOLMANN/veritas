#!/bin/bash
# funding_monitor_once.sh — cron watchdog for the SKR funding-carry position.
# Runs funding_monitor.py --once; silent (empty stdout) when healthy, so the cron
# watchdog stays quiet. Emits stdout only on an exit signal (which also auto-closes).
cd /c/Users/timot/OneDrive/Documents/VERITAS || exit 1
python3 funding_monitor.py --coin SKR --entry-funding -0.001 --once 2>&1 | grep -E "EXIT SIGNAL|CLOSE RESULT" || true