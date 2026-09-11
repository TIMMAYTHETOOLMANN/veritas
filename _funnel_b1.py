#!/usr/bin/env python3
"""Funnel batch 1: routes 100-199 with JSONL checkpoint."""
import os

os.environ["FUNNEL_START"] = "100"
os.environ["FUNNEL_LIMIT"] = "100"
os.environ["FUNNEL_CHECKPOINT"] = "C:/tmp/funnel_b1.jsonl"
os.environ["FUNNEL_BUDGET_S"] = "45"

open("C:/tmp/funnel_b1.jsonl", "w").close()  # truncate for idempotent reruns

from _opportunity_funnel import main

if __name__ == "__main__":
    main()
