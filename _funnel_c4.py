#!/usr/bin/env python3
"""Funnel batch c4: routes 160-199 with JSONL checkpoint (full-path valuation)."""
import os

os.environ["FUNNEL_START"] = "160"
os.environ["FUNNEL_LIMIT"] = "40"
os.environ["FUNNEL_CHECKPOINT"] = "C:/tmp/funnel_c4.jsonl"
os.environ["FUNNEL_BUDGET_S"] = "45"

open("C:/tmp/funnel_c4.jsonl", "w").close()  # truncate for idempotent reruns

from _opportunity_funnel import main

if __name__ == "__main__":
    main()
