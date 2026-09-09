#!/usr/bin/env python3
"""
core/size_optimizer.py — VERITAS adaptive size optimizer.

Replaces the fixed probe-only approach with adaptive optimization.

Phase 1: Coarse sweep across a wide range of sizes.
Phase 2: Adaptive refinement around profitable regions.

The optimizer maximizes verified_expected_net_profit, not percentage margin.

Default coarse probe curve (USD):
  $0.01, $0.025, $0.05, $0.10, $0.25, $0.50, $1, $2, $5, $10, $25, $50, $100

Refinement: binary-search-like narrowing around the peak.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.price_oracle import PriceOracle


# Default coarse probe curve (USD)
DEFAULT_COARSE_CURVE: List[float] = [
    0.01, 0.025, 0.05, 0.10, 0.25, 0.50, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0
]

# Refinement step sizes (fraction of current range)
REFINE_FRACTIONS: List[float] = [-0.2, -0.1, 0.0, 0.1, 0.2]

# Maximum number of size evaluations per opportunity
MAX_SIZE_EVALUATIONS: int = 25


@dataclass
class SizeResult:
    """Result of evaluating a single size."""
    size_usd: float
    net_profit_usd: float
    gross_profit_usd: float
    total_costs_usd: float
    roi_bps: float
    is_profitable: bool
    gas_usd: float = 0.0
    flash_fee_usd: float = 0.0
    slippage_usd: float = 0.0


@dataclass
class SizeCurve:
    """Complete size curve analysis for an opportunity."""
    results: List[SizeResult] = field(default_factory=list)
    optimal_size_usd: float = 0.0
    peak_net_profit_usd: float = 0.0
    min_profitable_size_usd: float = 0.0
    max_profitable_size_usd: float = 0.0
    evaluations: int = 0

    @property
    def profitable_results(self) -> List[SizeResult]:
        return [r for r in self.results if r.is_profitable]

    @property
    def has_profitable(self) -> bool:
        return len(self.profitable_results) > 0

    def to_dict(self) -> dict:
        return {
            "optimal_size_usd": round(self.optimal_size_usd, 6),
            "peak_net_profit_usd": round(self.peak_net_profit_usd, 6),
            "min_profitable_size_usd": round(self.min_profitable_size_usd, 6),
            "max_profitable_size_usd": round(self.max_profitable_size_usd, 6),
            "evaluations": self.evaluations,
            "profitable_count": len(self.profitable_results),
        }


class SizeOptimizer:
    """
    Adaptive size optimizer for VERITAS opportunities.

    Usage:
        optimizer = SizeOptimizer(price_oracle)
        curve = optimizer.optimize(quote_fn, token_in, token_out, input_usd)
    """

    def __init__(
        self,
        price_oracle: PriceOracle,
        coarse_curve: Optional[List[float]] = None,
        max_evaluations: int = MAX_SIZE_EVALUATIONS,
        min_profit_usd: float = 0.01,
    ):
        self.price_oracle = price_oracle
        self.coarse_curve = coarse_curve or DEFAULT_COARSE_CURVE
        self.max_evaluations = max_evaluations
        self.min_profit_usd = min_profit_usd

    def optimize(
        self,
        quote_fn: Callable[[int], int],
        token_in: str,
        token_in_decimals: int,
        input_usd: float,
    ) -> SizeCurve:
        """
        Find the optimal trade size for an opportunity.

        Args:
            quote_fn: Function that takes amount_in (wei) and returns amount_out (wei)
            token_in: Token address for input
            token_in_decimals: Decimals of input token
            input_usd: Reference input amount in USD

        Returns:
            SizeCurve with complete analysis
        """
        curve = SizeCurve()
        evaluations = 0

        # Phase 1: Coarse sweep
        profitable_sizes: List[Tuple[float, float]] = []  # (size_usd, net_profit)

        for size_usd in self.coarse_curve:
            if evaluations >= self.max_evaluations:
                break

            result = self._evaluate_size(
                quote_fn, token_in, token_in_decimals, size_usd
            )
            curve.results.append(result)
            evaluations += 1

            if result.is_profitable:
                profitable_sizes.append((size_usd, result.net_profit_usd))

        # Phase 2: Refine around the best profitable region
        if profitable_sizes and evaluations < self.max_evaluations:
            # Find the peak
            best_size, best_prof = max(profitable_sizes, key=lambda x: x[1])

            # Find neighbors for refinement
            profitable_sizes.sort(key=lambda x: x[0])
            idx = next(
                (i for i, (s, _) in enumerate(profitable_sizes) if s == best_size),
                0,
            )

            # Determine refinement range
            lower = profitable_sizes[max(0, idx - 1)][0] if idx > 0 else best_size * 0.5
            upper = profitable_sizes[min(len(profitable_sizes) - 1, idx + 1)][0] if idx < len(profitable_sizes) - 1 else best_size * 2.0

            # Refine
            for fraction in REFINE_FRACTIONS:
                if evaluations >= self.max_evaluations:
                    break
                refined_size = best_size + (upper - lower) * fraction
                refined_size = max(0.01, refined_size)  # floor at 1 cent

                # Skip if already evaluated
                if any(abs(r.size_usd - refined_size) < 0.001 for r in curve.results):
                    continue

                result = self._evaluate_size(
                    quote_fn, token_in, token_in_decimals, refined_size
                )
                curve.results.append(result)
                evaluations += 1

        # Compute summary
        curve.evaluations = evaluations
        profitable = curve.profitable_results

        if profitable:
            best = max(profitable, key=lambda r: r.net_profit_usd)
            curve.optimal_size_usd = best.size_usd
            curve.peak_net_profit_usd = best.net_profit_usd
            curve.min_profitable_size_usd = min(r.size_usd for r in profitable)
            curve.max_profitable_size_usd = max(r.size_usd for r in profitable)

        return curve

    def _evaluate_size(
        self,
        quote_fn: Callable[[int], int],
        token_in: str,
        token_in_decimals: int,
        size_usd: float,
    ) -> SizeResult:
        """Evaluate a single trade size."""
        result = SizeResult(size_usd=size_usd, net_profit_usd=0.0,
                           gross_profit_usd=0.0, total_costs_usd=0.0, roi_bps=0.0,
                           is_profitable=False)

        try:
            # Convert USD to wei
            token_price = self.price_oracle.get_price_usd(token_in) or 2500.0
            size_float = size_usd / token_price
            amount_in = int(size_float * (10 ** token_in_decimals))

            if amount_in <= 0:
                return result

            # Get quote
            amount_out = quote_fn(amount_in)

            if amount_out <= 0:
                return result

            # Calculate output value in USD
            # For simplicity, assume output token has same price reference
            # In production, this would use the actual output token price
            output_usd = size_usd  # Placeholder — real implementation uses output token price

            # Gross profit
            result.gross_profit_usd = output_usd - size_usd

            # Flash loan fee (0.05%)
            result.flash_fee_usd = size_usd * 0.0005

            # Gas estimate (simplified)
            result.gas_usd = 0.01  # Base gas estimate

            # Slippage (simplified — would use price impact from quote)
            result.slippage_usd = size_usd * 0.001  # 10 bps default

            # Total costs
            result.total_costs_usd = (
                result.flash_fee_usd + result.gas_usd + result.slippage_usd
            )

            # Net profit
            result.net_profit_usd = result.gross_profit_usd - result.total_costs_usd

            # ROI
            if size_usd > 0:
                result.roi_bps = (result.net_profit_usd / size_usd) * 10_000.0

            # Profitable?
            result.is_profitable = result.net_profit_usd > self.min_profit_usd

        except Exception:
            # Size evaluation failed — return as unprofitable
            pass

        return result
