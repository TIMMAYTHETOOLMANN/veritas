#!/usr/bin/env python3
"""
core/scan_result.py — VERITAS backward-compatible scan result wrapper.

PURPOSE
-------
Wraps the scanner output so that:
  1. Existing consumers (flash_hunter.py, dry_run.py, check_edges_tmp.py)
     continue to work via the `edges` field — zero breakage.
  2. New diagnostic consumers get full lifecycle telemetry via
     `candidates`, `rejections`, `statistics`, and `why_zero_report`.

This is the contract that lets us upgrade discovery without touching
the execution/simulation infrastructure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.opportunity_telemetry import Candidate, RejectionReason
from core.ranking import rank_candidates, explain_best, DEFAULT_WEIGHTS


@dataclass
class ScanResult:
    """
    Full result of one scan cycle.

    Backward compatibility:
      scan.edges  -> List[Dict]  (same shape as the old return value)
      len(scan)   -> number of edges (truthy when edges exist)

    New diagnostics:
      scan.candidates     -> every candidate that entered the pipeline
      scan.rejections     -> {RejectionReason: count}
      scan.statistics     -> aggregate counters
      scan.why_zero_report -> human-readable explanation string
    """
    edges: List[Dict[str, Any]] = field(default_factory=list)
    candidates: List[Candidate] = field(default_factory=list)
    rejections: Dict[str, int] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    scan_metadata: Dict[str, Any] = field(default_factory=dict)

    # ---- backward-compat helpers -------------------------------------------

    def __bool__(self) -> bool:
        return len(self.edges) > 0

    def __len__(self) -> int:
        return len(self.edges)

    def __iter__(self):
        return iter(self.edges)

    def __getitem__(self, idx):
        return self.edges[idx]

    # ---- diagnostic report -------------------------------------------------

    def generate_why_zero_report(self) -> str:
        """
        Produce a human-readable explanation of what the scan saw and why
        no edges (or few edges) passed.  This is the primary tool for
        distinguishing "VERITAS is blind" from "the market is efficient".
        """
        lines: List[str] = []
        sep = "-" * 56
        lines.append(f"VERITAS SCAN — block {self.scan_metadata.get('block_number', '?')} {sep}")

        # Discovery phase
        pairs_discovered = self.statistics.get('pairs_discovered', 0)
        quotes_attempted = self.statistics.get('quotes_attempted', 0)
        valid_quotes = self.statistics.get('valid_quotes', 0)
        candidates_count = len(self.candidates)

        lines.append(f"Pairs discovered:          {pairs_discovered:>6}")
        lines.append(f"Quotes attempted:          {quotes_attempted:>6}")
        lines.append(f"Valid quotes:              {valid_quotes:>6}")
        lines.append(f"Cross-venue candidates:     {candidates_count:>6}")
        lines.append("")

        # Rejection breakdown
        if self.rejections:
            lines.append("Rejected:")
            # Sort by count descending for readability
            sorted_rejections = sorted(
                self.rejections.items(), key=lambda kv: kv[1], reverse=True
            )
            for reason, count in sorted_rejections:
                lines.append(f"  {reason + ':':<30} {count:>6}")
            lines.append("")

        # Simulation phase
        sim_candidates = self.statistics.get('simulation_candidates', 0)
        sim_passes = self.statistics.get('simulation_passes', 0)
        lines.append(f"Simulation candidates:      {sim_candidates:>6}")
        lines.append(f"Simulation passes:           {sim_passes:>6}")
        lines.append("")

        # Best observed candidate (even if rejected)
        best = self._best_candidate()
        if best:
            lines.append("BEST OBSERVED")
            lines.append(f"  {best.route}")
            lines.append(f"  Buy:  {best.buy_venue}")
            lines.append(f"  Sell: {best.sell_venue}")
            lines.append(f"  Size: ${best.input_amount:>8.2f}")
            lines.append(f"  Gross: ${best.gross_profit_usd:>8.4f}")
            lines.append(f"  DEX fees:    -${best.swap_fees_usd:>8.4f}")
            lines.append(f"  Aave fee:    -${best.flash_loan_fee_usd:>8.4f}")
            lines.append(f"  Gas:         -${best.estimated_gas_usd:>8.4f}")
            lines.append(f"  Safety:      -${best.safety_margin_usd:>8.4f}")
            lines.append(f"  Net:         ${best.net_profit_usd:>+8.4f}")
            if best.rejection_reason:
                lines.append(f"  Status: REJECTED ({best.rejection_reason.value})")
            else:
                lines.append(f"  Status: {best.status.value}")
            lines.append("")

        # Final status
        if self.edges:
            lines.append(f"STATUS: {len(self.edges)} EDGE(S) READY FOR SIMULATION")
        elif best and best.net_profit_usd > 0:
            lines.append("STATUS: CANDIDATES EXIST BUT BELOW EXECUTION THRESHOLD")
        elif best:
            lines.append("STATUS: NO PROFITABLE OPPORTUNITY (efficient market or fee wall)")
        elif self.statistics.get("valid_quotes", 0) > 0:
            lines.append("STATUS: QUOTES OBTAINED BUT NO CROSS-VENUE OPPORTUNITY (check venue coverage)")
        else:
            lines.append("STATUS: NO QUOTES OBTAINED (check RPC / liquidity / pair discovery)")

        return "\n".join(lines)

    def _best_candidate(self) -> Optional[Candidate]:
        """Return the candidate with the highest net_profit_usd."""
        if not self.candidates:
            return None
        return max(self.candidates, key=lambda c: c.net_profit_usd)

    @property
    def ranked_candidates(self) -> list:
        """Return candidates ranked by opportunity score (highest first)."""
        return rank_candidates(self.candidates)

    @property
    def best_opportunity(self):
        """Return the single best scored opportunity."""
        ranked = self.ranked_candidates
        return ranked[0] if ranked else None

    def record_rejection(self, reason: RejectionReason) -> None:
        """Increment the counter for a given rejection reason."""
        key = reason.value
        self.rejections[key] = self.rejections.get(key, 0) + 1

    def to_legacy_tuple(self):
        """
        Return the old-style (edges, report) tuple for consumers that
        haven't been migrated yet.
        """
        report = [
            {
                "token": c.token_pair[1] if len(c.token_pair) > 1 else "",
                "size_weth": c.input_amount,
                "net_margin": c.net_profit_usd,
                "gross_profit": c.gross_profit_usd,
                "buy_venue": c.buy_venue,
                "sell_venue": c.sell_venue,
            }
            for c in self.candidates
        ]
        return self.edges, report
