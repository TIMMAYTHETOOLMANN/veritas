#!/usr/bin/env python3
"""
core/economic_model.py — VERITAS canonical economic model.

Every opportunity must calculate:
  gross_output
  - input
  - DEX fees
  - flash loan fee
  - expected gas
  - expected slippage
  - safety margin
  = expected_net_profit

This is the ONE canonical economic model. All modules consume it.
No module may independently subtract the same cost twice.

ACCOUNTING EQUATION (documented mathematically):
  verified_profit = settlement_asset_delta - actual_external_gas_cost

  Where:
    - settlement_asset_delta is the net change in the settlement asset
      (e.g., WETH balance change of the executor/profit-bearing account)
    - actual_external_gas_cost is subtracted ONLY IF gas is not already
      represented in the settlement delta

  For flash-loan-based arb:
    settlement_asset_delta = executor_WETH_after - executor_WETH_before
    This delta ALREADY includes gas costs (paid from executor balance).
    Therefore: verified_profit = settlement_asset_delta (no double subtraction).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from core.price_oracle import PriceOracle


# ---- Fee constants (Arbitrum) ----
AAVE_FLASH_LOAN_FEE_BPS = 5       # 0.05% = 5 basis points
AAVE_FLASH_LOAN_FEE_DIVISOR = 10_000

# DEX fees are encoded in the pool fee tier (e.g., 3000 = 0.3%)
# These are handled by the QuoterV2 / CPMM math, not subtracted here.

# Default safety margin: percentage of expected profit to withhold
DEFAULT_SAFETY_MARGIN_BPS = 100    # 1.0% = 100 basis points

# Minimum viable profit thresholds
DEFAULT_MIN_PROFIT_USD = 0.01     # 1 cent — micro-capital mode
DEFAULT_MIN_ROI_BPS = 5           # 0.05% minimum ROI


@dataclass
class EconomicResult:
    """
    Complete economic analysis of an opportunity.

    This is the single source of truth for whether an opportunity
    is profitable and worth executing.
    """
    # Inputs
    input_usd: float = 0.0
    gross_output_usd: float = 0.0

    # Cost breakdown
    dex_fees_usd: float = 0.0
    flash_loan_fee_usd: float = 0.0
    estimated_gas_usd: float = 0.0
    estimated_slippage_usd: float = 0.0
    safety_margin_usd: float = 0.0

    # Results
    gross_profit_usd: float = 0.0
    total_costs_usd: float = 0.0
    expected_net_profit_usd: float = 0.0

    # Metrics
    roi_bps: float = 0.0           # return on investment in basis points
    margin_bps: float = 0.0        # profit margin in basis points
    cost_ratio: float = 0.0        # costs / gross_output

    # Classification
    is_profitable: bool = False
    is_worth_executing: bool = False
    below_threshold_reason: Optional[str] = None

    # Provenance
    timestamp: float = field(default_factory=time.time)
    block_number: int = 0

    def to_dict(self) -> dict:
        return {
            "input_usd": round(self.input_usd, 6),
            "gross_output_usd": round(self.gross_output_usd, 6),
            "gross_profit_usd": round(self.gross_profit_usd, 6),
            "dex_fees_usd": round(self.dex_fees_usd, 6),
            "flash_loan_fee_usd": round(self.flash_loan_fee_usd, 6),
            "estimated_gas_usd": round(self.estimated_gas_usd, 6),
            "estimated_slippage_usd": round(self.estimated_slippage_usd, 6),
            "safety_margin_usd": round(self.safety_margin_usd, 6),
            "total_costs_usd": round(self.total_costs_usd, 6),
            "expected_net_profit_usd": round(self.expected_net_profit_usd, 6),
            "roi_bps": round(self.roi_bps, 2),
            "margin_bps": round(self.margin_bps, 2),
            "is_profitable": self.is_profitable,
            "is_worth_executing": self.is_worth_executing,
            "below_threshold_reason": self.below_threshold_reason,
        }


class EconomicModel:
    """
    Canonical economic model for VERITAS opportunity evaluation.

    Usage:
        model = EconomicModel(price_oracle)
        result = model.evaluate(
            input_usd=100.0,
            gross_output_usd=100.50,
            flash_loan_amount_usd=100.0,
            estimated_gas_units=350000,
            gas_price_gwei=0.01,
            slippage_bps=10,
        )
    """

    def __init__(
        self,
        price_oracle: PriceOracle,
        flash_loan_fee_bps: int = AAVE_FLASH_LOAN_FEE_BPS,
        safety_margin_bps: int = DEFAULT_SAFETY_MARGIN_BPS,
        min_profit_usd: float = DEFAULT_MIN_PROFIT_USD,
        min_roi_bps: float = DEFAULT_MIN_ROI_BPS,
        gas_multiplier: float = 1.0,
    ):
        self.price_oracle = price_oracle
        self.flash_loan_fee_bps = flash_loan_fee_bps
        self.safety_margin_bps = safety_margin_bps
        self.min_profit_usd = min_profit_usd
        self.min_roi_bps = min_roi_bps
        self.gas_multiplier = gas_multiplier

    def evaluate(
        self,
        input_usd: float,
        gross_output_usd: float,
        flash_loan_amount_usd: float,
        estimated_gas_units: int,
        gas_price_gwei: float,
        slippage_bps: int = 0,
        block_number: int = 0,
    ) -> EconomicResult:
        """
        Evaluate the complete economics of an opportunity.

        Args:
            input_usd: Input amount in USD
            gross_output_usd: Gross output before any costs
            flash_loan_amount_usd: Amount borrowed via flash loan
            estimated_gas_units: Estimated gas units for the transaction
            gas_price_gwei: Gas price in gwei
            slippage_bps: Expected slippage in basis points
            block_number: Current block for provenance

        Returns:
            EconomicResult with complete cost breakdown
        """
        result = EconomicResult()
        result.input_usd = input_usd
        result.gross_output_usd = gross_output_usd
        result.block_number = block_number
        result.timestamp = time.time()

        # Gross profit
        result.gross_profit_usd = gross_output_usd - input_usd

        # DEX fees: already accounted for in the quote output
        # (QuoterV2 returns the actual output after pool fees)
        result.dex_fees_usd = 0.0

        # Flash loan fee
        result.flash_loan_fee_usd = (
            flash_loan_amount_usd * self.flash_loan_fee_bps / AAVE_FLASH_LOAN_FEE_DIVISOR
        )

        # Gas cost
        gas_native = estimated_gas_units * gas_price_gwei / 1e9  # in ETH/native
        eth_price = self.price_oracle.get_price_usd(
            "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
        ) or 2500.0
        result.estimated_gas_usd = gas_native * eth_price * self.gas_multiplier

        # Slippage cost
        result.estimated_slippage_usd = input_usd * slippage_bps / 10_000.0

        # Safety margin (percentage of expected profit)
        if result.gross_profit_usd > 0:
            result.safety_margin_usd = result.gross_profit_usd * self.safety_margin_bps / 10_000.0
        else:
            result.safety_margin_usd = 0.0

        # Total costs
        result.total_costs_usd = (
            result.dex_fees_usd
            + result.flash_loan_fee_usd
            + result.estimated_gas_usd
            + result.estimated_slippage_usd
            + result.safety_margin_usd
        )

        # Net profit
        result.expected_net_profit_usd = result.gross_profit_usd - result.total_costs_usd

        # ROI
        if input_usd > 0:
            result.roi_bps = (result.expected_net_profit_usd / input_usd) * 10_000.0
            result.margin_bps = (result.gross_profit_usd / input_usd) * 10_000.0
        else:
            result.roi_bps = 0.0
            result.margin_bps = 0.0

        # Cost ratio
        if gross_output_usd > 0:
            result.cost_ratio = result.total_costs_usd / gross_output_usd
        else:
            result.cost_ratio = float('inf')

        # Classification
        result.is_profitable = result.expected_net_profit_usd > 0

        # Worth executing: profitable AND above thresholds
        if result.is_profitable:
            if result.expected_net_profit_usd < self.min_profit_usd:
                result.is_worth_executing = False
                result.below_threshold_reason = (
                    f"below_min_profit: ${result.expected_net_profit_usd:.4f} < "
                    f"${self.min_profit_usd:.4f}"
                )
            elif result.roi_bps < self.min_roi_bps:
                result.is_worth_executing = False
                result.below_threshold_reason = (
                    f"below_min_roi: {result.roi_bps:.2f}bps < {self.min_roi_bps:.2f}bps"
                )
            else:
                result.is_worth_executing = True
        else:
            result.below_threshold_reason = (
                f"not_profitable: net=${result.expected_net_profit_usd:.4f}"
            )

        return result

    def evaluate_actual(
        self,
        settlement_delta_usd: float,
        actual_gas_usd: float,
        gas_already_in_delta: bool = True,
    ) -> float:
        """
        Calculate verified profit from actual on-chain results.

        ACCOUNTING EQUATION:
          If gas is already in the settlement delta (typical for flash-loan arb
          where the executor pays gas from its own balance):
            verified_profit = settlement_delta_usd

          If gas is NOT in the settlement delta:
            verified_profit = settlement_delta_usd - actual_gas_usd

        Args:
            settlement_delta_usd: Net change in settlement asset (USD)
            actual_gas_usd: Actual gas cost (USD)
            gas_already_in_delta: Whether gas is already included in the delta

        Returns:
            Verified profit in USD
        """
        if gas_already_in_delta:
            return settlement_delta_usd
        else:
            return settlement_delta_usd - actual_gas_usd

    def flash_loan_fee(self, amount_usd: float) -> float:
        """Calculate the flash loan fee for a given amount."""
        return amount_usd * self.flash_loan_fee_bps / AAVE_FLASH_LOAN_FEE_DIVISOR

    def gas_cost_usd(
        self, gas_units: int, gas_price_gwei: float, eth_price_usd: float = 2500.0
    ) -> float:
        """Calculate gas cost in USD."""
        gas_native = gas_units * gas_price_gwei / 1e9
        return gas_native * eth_price_usd * self.gas_multiplier
