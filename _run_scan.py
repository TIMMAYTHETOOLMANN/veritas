#!/usr/bin/env python3
"""Run scan and capture output."""
import sys
import time

start = time.time()

try:
    import veritas_engine
    e = veritas_engine.VeritasEngine()
    e.initialize()
    r = e.scan_once()
    print(r.generate_why_zero_report())
except Exception as ex:
    import traceback
    traceback.print_exc()

elapsed = time.time() - start
print(f"\nScan completed in {elapsed:.1f}s")
