#!/usr/bin/env python3
"""
core/ranking.py — VERITAS opportunity ranking and scoring.

Produces a single `opportunity_score` for each candidate while retaining
all underlying metrics. The highest-ranked opportunity should be
explainable: why it scored what it did, and what the key factors are.

RANKING FACTORS (with default weights):
  - net_profit_usd (30%): Absolute expected profit
  - net_margin_pct (20%): Profit as percentage of input
  - capital_efficiency (15%): Profit per dollar of capital deployed
  - execution_risk_inverse (10%): Lower risk = higher score
  - gas_burden_inverse (10%): Lower gas = higher score
  - liquidity_depth (5%): More liquid = higher score
  - confidence (5%): Quote confidence from scanner
  - quote_freshness (5%): More recent = higher score
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.opportunity_telemetry import Candidate


# ---- Default weights (sum to 1.0) --------------------------------------
DEFAULT_WEIGHTS: Dict[str, float] = {
    "net_profit": 0.30,
    "net_margin_pct": 0.20,
    "capital_efficiency": 0.15,
    "execution_risk_inverse": 0.10,
    "gas_burden_inverse": 0.10,
    "liquidity_depth": 0.05,
    "confidence": 0.05,
    "quote_freshness": 0.05,
}


# ---- Reference values for normalization ---------------------------------
# These define the "typical" range for each metric, used to normalize
# values to [0, 1] before applying weights.
REFERENCE = {
    "net_profit_usd": 1.0,        # $1 profit = 1.0 normalized
    "net_margin_pct": 0.5,        # 0.5% margin = 1.0 normalized
    "capital_efficiency": 0.10,   # 10% efficiency = 1.0 normalized
    "execution_risk": 0.5,        # 0.5 risk = midpoint
    "gas_burden_usd": 0.05,       # $0.05 gas = 1.0 normalized
    "liquidity_usd": 10000.0,     # $10K liquidity = 1.0 normalized
    "confidence": 1.0,            # 1.0 confidence = 1.0 normalized
    "quote_age_seconds": 60.0,    # 60s old = freshness boundary
}


@dataclass
class ScoredCandidate:
    """A candidate with its computed score and factor breakdown."""
    candidate: Candidate
    score: float
    factor_scores: Dict[str, float] = field(default_factory=dict)
    rank: int = 0

    def explain(self) -> str:
        """Return a human-readable explanation of the score."""
        lines = [
            f"RANK #{self.rank} | Score: {self.score:.4f}",
            f"  Route: {self.candidate.route}",
            f"  Buy: {self.candidate.buy_venue} -> Sell: {self.candidate.sell_venue}",
            f"  Net profit: ${self.candidate.net_profit_usd:+.4f}",
            f"  Input: ${self.candidate.input_amount:.2f}",
        ]
        # Show top contributing factors
        sorted_factors = sorted(
            self.factor_scores.items(), key=lambda kv: kv[1], reverse=True
        )
        lines.append("  Factor scores:")
        for name, score in sorted_factors:
            bar = "█" * int(score * 20)
            lines.append(f"    {name:<25} {score:.3f} {bar}")
        return "\n".join(lines)


def _normalize(value: float, reference: float) -> float:
    """
    Normalize a value to [0, 1] using the reference scale.

    Uses a sigmoid-like curve so that:
      - value = reference -> 0.5
      - value >> reference -> approaches 1.0
      - value << reference -> approaches 0.0
      - negative values -> 0.0
    """
    if value <= 0:
        return 0.0
    # Sigmoid: 1 / (1 + exp(-2 * (value/reference - 1)))
    # At value=reference, this gives 0.5
    ratio = value / reference if reference > 0 else 0.0
    try:
        return 1.0 / (1.0 + math.exp(-2.0 * (ratio - 1.0)))
    except (OverflowError, ZeroDivisionError):
        return 0.0


def _inverse_normalize(value: float, reference: float) -> float:
    """
    Normalize where LOWER is better (gas, risk).

    Returns 1.0 when value=0, approaching 0.0 as value >> reference.
    """
    if value <= 0:
        return 1.0
    ratio = value / reference if reference > 0 else 1.0
    try:
        return 1.0 / (1.0 + math.exp(2.0 * (ratio - 1.0)))
    except (OverflowError, ZeroDivisionError):
        return 0.0


def _freshness_score(timestamp: float, max_age_seconds: float = 300.0) -> float:
    """
    Score quote freshness. 1.0 = just observed, decays to 0.0 at max_age.
    """
    age = time.time() - timestamp
    if age <= 0:
        return 1.0
    if age >= max_age_seconds:
        return 0.0
    return 1.0 - (age / max_age_seconds)


def compute_score(
    candidate: Candidate,
    weights: Optional[Dict[str, float]] = None,
    references: Optional[Dict[str, float]] = None,
) -> ScoredCandidate:
    """
    Compute the opportunity score for a candidate.

    Returns a ScoredCandidate with the overall score and per-factor breakdown.
    All factors are normalized to [0, 1] before weighting.
    """
    w = weights or DEFAULT_WEIGHTS
    ref = references or REFERENCE

    factor_scores: Dict[str, float] = {}

    # 1. Net profit (higher is better)
    factor_scores["net_profit"] = _normalize(
        candidate.net_profit_usd, ref["net_profit_usd"]
    )

    # 2. Net margin % (higher is better)
    if candidate.input_amount > 0:
        margin_pct = (candidate.net_profit_usd / candidate.input_amount) * 100.0
    else:
        margin_pct = 0.0
    factor_scores["net_margin_pct"] = _normalize(
        margin_pct, ref["net_margin_pct"]
    )

    # 3. Capital efficiency (profit per dollar deployed)
    if candidate.input_amount > 0:
        efficiency = candidate.net_profit_usd / candidate.input_amount
    else:
        efficiency = 0.0
    factor_scores["capital_efficiency"] = _normalize(
        efficiency, ref["capital_efficiency"]
    )

    # 4. Execution risk inverse (lower risk = higher score)
    execution_risk = 1.0 - candidate.confidence  # risk = 1 - confidence
    factor_scores["execution_risk_inverse"] = _inverse_normalize(
        execution_risk, ref["execution_risk"]
    )

    # 5. Gas burden inverse (lower gas = higher score)
    factor_scores["gas_burden_inverse"] = _inverse_normalize(
        candidate.estimated_gas_usd, ref["gas_burden_usd"]
    )

    # 6. Liquidity depth (higher is better)
    liquidity = getattr(candidate, '_liquidity_usd', 0.0)
    factor_scores["liquidity_depth"] = _normalize(
        liquidity, ref["liquidity_usd"]
    )

    # 7. Confidence (higher is better)
    factor_scores["confidence"] = _normalize(
        candidate.confidence, ref["confidence"]
    )

    # 8. Quote freshness (more recent = higher score)
    factor_scores["quote_freshness"] = _freshness_score(candidate.timestamp)

    # Compute weighted sum
    score = sum(
        factor_scores.get(factor, 0.0) * w.get(factor, 0.0)
        for factor in w
    )

    return ScoredCandidate(
        candidate=candidate,
        score=score,
        factor_scores=factor_scores,
    )


def rank_candidates(
    candidates: List[Candidate],
    weights: Optional[Dict[str, float]] = None,
    references: Optional[Dict[str, float]] = None,
    min_score: float = 0.0,
) -> List[ScoredCandidate]:
    """
    Score and rank a list of candidates.

    Returns candidates sorted by score descending, with rank assigned.
    Candidates below min_score are excluded.
    """
    scored = [
        compute_score(c, weights, references) for c in candidates
    ]
    # Filter by minimum score
    scored = [s for s in scored if s.score >= min_score]
    # Sort by score descending
    scored.sort(key=lambda s: s.score, reverse=True)
    # Assign ranks
    for i, s in enumerate(scored, 1):
        s.rank = i
    return scored


def best_opportunity(
    candidates: List[Candidate],
    weights: Optional[Dict[str, float]] = None,
) -> Optional[ScoredCandidate]:
    """
    Return the single best opportunity from a list of candidates.

    Returns None if no candidates have a positive score.
    """
    ranked = rank_candidates(candidates, weights)
    return ranked[0] if ranked else None


def explain_best(candidates: List[Candidate]) -> str:
    """
    Return a human-readable explanation of the best opportunity.

    If no candidates exist, returns an explanation of why.
    """
    best = best_opportunity(candidates)
    if not best:
        if not candidates:
            return "No candidates to rank. The scan found no cross-venue opportunities."
        return "All candidates scored below the minimum threshold."

    return best.explain()
