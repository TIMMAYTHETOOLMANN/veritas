#!/usr/bin/env python3
"""Funnel batch c5: routes 200-239 with JSONL checkpoint (full-path valuation)."""
import os

os.environ["FUNNEL_START"] = "200"
os.environ["FUNNEL_LIMIT"] = "40"
os.environ["FUNNEL_CHECKPOINT"] = "C:/tmp/funnel_c5.jsonl"
os.environ["FUNNEL_BUDGET_S"] = "45"

open("C:/tmp/funnel_c5.jsonl", "w").close()  # truncate for idempotent reruns

from _opportunity_funnel import main

if __name__ == "__main__":
    main()
