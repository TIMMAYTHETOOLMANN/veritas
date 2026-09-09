#!/usr/bin/env python3
"""Run scan with timing."""
import sys
import time

old_stderr = sys.stderr
sys.stderr = StringIO()

try:
    from io import StringIO
    import veritas_engine
    
    start = time.time()
    e = veritas_engine.VeritasEngine()
    
    init_start = time.time()
    e.initialize()
    print(f"Initialize: {time.time() - init_start:.1f}s", flush=True)
    
    scan_start = time.time()
    r = e.scan_once()
    print(f"Scan: {time.time() - scan_start:.1f}s", flush=True)
    
    print(r.generate_why_zero_report())
    
except Exception as ex:
    import traceback
    traceback.print_exc()

sys.stderr = old_stderr
