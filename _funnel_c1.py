#!/usr/bin/env python3
"""Funnel batch c1: routes 40-79 with JSONL checkpoint (full-path valuation)."""
import os

os.environ["FUNNEL_START"] = "40"
os.environ["FUNNEL_LIMIT"] = "40"
os.environ["FUNNEL_CHECKPOINT"] = "C:/tmp/funnel_c1.jsonl"
os.environ["FUNNEL_BUDGET_S"] = "45"

open("C:/tmp/funnel_c1.jsonl", "w").close()  # truncate for idempotent reruns

from _opportunity_funnel import main

if __name__ == "__main__":
    main()
