#!/usr/bin/env python3
"""Funnel batch 3: routes 300+ with JSONL checkpoint."""
import os

os.environ["FUNNEL_START"] = "300"
os.environ["FUNNEL_LIMIT"] = "100"
os.environ["FUNNEL_CHECKPOINT"] = "C:/tmp/funnel_b3.jsonl"
os.environ["FUNNEL_BUDGET_S"] = "45"

open("C:/tmp/funnel_b3.jsonl", "w").close()  # truncate for idempotent reruns

from _opportunity_funnel import main

if __name__ == "__main__":
    main()
