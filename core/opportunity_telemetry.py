#!/usr/bin/env python3
"""
core/opportunity_telemetry.py — VERITAS opportunity lifecycle model.

Every candidate that enters the discovery pipeline is tracked from
DISCOVERED through to EXECUTABLE or REJECTED.  This lets us answer:

  - "Did we miss opportunities, or is the market just efficient?"
  - "Where exactly do candidates die?"
  - "What is the best currently observable opportunity?"

The model is intentionally verbose.  Collapsing these distinctions into
a single edges_returned=0 is exactly the failure mode we are fixing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class CandidateStatus(Enum):
    """Lifecycle stages for a candidate opportunity."""
    DISCOVERED = "discovered"                    # pair found, reserves read
    CANDIDATE = "candidate"                      # cross-venue pair constructed
    ECONOMICALLY_VIABLE = "economically_viable"  # gross > 0 after fees
    SIMULATION_PASS = "simulation_pass"          # fork sim confirmed profit
    EXECUTABLE = "executable"                    # passed all gates, ready to broadcast
    REJECTED = "rejected"                        # failed a gate (see rejection_reason)


class RejectionReason(Enum):
    """Explicit reasons a candidate can fail.  Never collapse these."""
    NO_LIQUIDITY = "no_liquidity"
    NO_QUOTE = "no_quote"
    STALE_QUOTE = "stale_quote"
    INVALID_PAIR = "invalid_pair"
    MALFORMED_RESERVES = "malformed_reserves"
    DECIMAL_NORMALIZATION_ERROR = "decimal_error"
    INSUFFICIENT_SPREAD = "insufficient_spread"
    FEE_REJECTION = "fee_rejection"
    GAS_REJECTION = "gas_rejection"
    SAFETY_MARGIN_REJECTION = "safety_margin_rejection"
    SIZING_REJECTION = "sizing_rejection"
    SIMULATION_REJECTION = "simulation_rejection"
    EXECUTION_RISK_REJECTION = "execution_risk_rejection"
    NO_OPPORTUNITY = "no_opportunity"  # genuinely efficient market


@dataclass
class Candidate:
    """
    Full lifecycle record for one candidate opportunity.

    Fields are populated incrementally as the candidate progresses
    through the pipeline.  Early-stage fields (pair, venues) are
    populated at DISCOVERED; late-stage fields (net_profit, status)
    are populated at ECONOMICALLY_VIABLE or beyond.
    """
    # Identity
    token_pair: Tuple[str, str] = ("", "")
    buy_venue: str = ""
    sell_venue: str = ""
    route: str = ""

    # Economics (populated during ECONOMIC_ANALYSIS)
    input_amount: float = 0.0
    output_amount: float = 0.0
    implied_spread_bps: float = 0.0
    gross_profit_usd: float = 0.0
    swap_fees_usd: float = 0.0
    flash_loan_fee_usd: float = 0.0
    estimated_gas_usd: float = 0.0
    estimated_slippage_usd: float = 0.0
    safety_margin_usd: float = 0.0
    net_profit_usd: float = 0.0

    # Lifecycle
    status: CandidateStatus = CandidateStatus.DISCOVERED
    rejection_reason: Optional[RejectionReason] = None

    # Provenance
    timestamp: float = field(default_factory=time.time)
    block_number: int = 0
    quote_source: str = ""
    confidence: float = 0.0

    # Size-curve analysis (populated during SIZE_OPTIMIZATION)
    size_curve: Optional[list] = None  # [(size_usd, net_profit), ...]
    optimal_size_usd: float = 0.0
    peak_net_profit_usd: float = 0.0
    min_profitable_size_usd: float = 0.0
    max_profitable_size_usd: float = 0.0

    # Simulation results (populated during SIMULATION)
    sim_gas_used: int = 0
    sim_profit_weth: float = 0.0
    sim_profit_usd: float = 0.0
    sim_gate: str = ""  # "PASS" or "FAIL"

    def reject(self, reason: RejectionReason) -> None:
        """Mark this candidate as rejected with an explicit reason."""
        self.status = CandidateStatus.REJECTED
        self.rejection_reason = reason

    def is_viable(self) -> bool:
        """True if the candidate passed all economic gates."""
        return self.status in (
            CandidateStatus.ECONOMICALLY_VIABLE,
            CandidateStatus.SIMULATION_PASS,
            CandidateStatus.EXECUTABLE,
        )

    def summary(self) -> dict:
        """Return a compact dict for logging / JSON serialization."""
        return {
            "route": self.route,
            "buy_venue": self.buy_venue,
            "sell_venue": self.sell_venue,
            "input_usd": round(self.input_amount, 4),
            "gross_profit_usd": round(self.gross_profit_usd, 6),
            "net_profit_usd": round(self.net_profit_usd, 6),
            "status": self.status.value,
            "rejection_reason": self.rejection_reason.value if self.rejection_reason else None,
            "block": self.block_number,
            "confidence": round(self.confidence, 3),
        }
