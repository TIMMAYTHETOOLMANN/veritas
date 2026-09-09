#!/usr/bin/env python3
"""Simple scan runner."""
import veritas_engine

e = veritas_engine.VeritasEngine()
e.initialize()
r = e.scan_once()
print(r.generate_why_zero_report())
