#!/usr/bin/env python3
"""
core/capital_controller.py — VERITAS conservative deployable-capital controller.

DESIGN GOALS
- Initial deployment optimization for very small capital (~$10 start).
- Exponential compounding on profit WITHOUT risking principal.
- Initial trades are micro/cents-scale and highly conservative.
- Size trades from deployable capital, not a hardcoded 1 ETH principal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---- Tunables ----
# These are intentionally conservative for a $10 bootstrap.
INITIAL_DEPLOYABLE_USD: float = 10.00   # initial simulated deployable capital
MIN_SIZE_USD: float = 0.05              # floor trade size in USD
MAX_SIZE_USD: float = 1.00              # ceiling trade size in USD
SIZE_CAP_FRACTION: float = 0.10         # never size more than 10% of deployable capital
GAS_MULTIPLIER: float = 2.0             # require profit > 2x estimated gas
MIN_PROFIT_FLOOR_USD: float = 0.05      # minimum acceptable net profit after gas
MAX_SIMS_PER_CYCLE: int = 6             # sim budget per scan cycle


@dataclass
class CapitalState:
    deployable_usd: float = INITIAL_DEPLOYABLE_USD
    starting_usd: float = INITIAL_DEPLOYABLE_USD
    total_profit_usd: float = 0.0
    total_gas_usd: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    last_trade_profit_usd: float = 0.0


class CapitalController:
    def __init__(self, state: Optional[CapitalState] = None) -> None:
        self.state = state or CapitalState()

    def record_attempt(self, gas_usd: float) -> None:
        self.state.total_gas_usd += gas_usd
        self.state.trades += 1

    def record_result(self, net_profit_usd: float, gas_usd: float) -> None:
        self.state.total_gas_usd += gas_usd
        self.state.last_trade_profit_usd = net_profit_usd
        if net_profit_usd >= 0:
            self.state.wins += 1
            # Reinvest profit; principal stays constant.
            self.state.deployable_usd += net_profit_usd
            self.state.total_profit_usd += net_profit_usd
        else:
            self.state.losses += 1
            # Losses only shrink profit pool, not base capital.
            self.state.deployable_usd = max(
                self.state.starting_usd,
                self.state.deployable_usd + net_profit_usd,
            )
        self.state.trades += 1

    def size_for_edge(self, edge: dict, gas_usd: float, eth_usd: float) -> dict:
        """
        Return a sized copy of `edge` for simulation/execution.
        """
        sized = dict(edge)
        # Use conservative fraction of deployable capital.
        fraction_cap = self.state.deployable_usd * SIZE_CAP_FRACTION
        candidate = min(MAX_SIZE_USD, max(MIN_SIZE_USD, fraction_cap))

        sized["target_trade_usd"] = candidate
        sized["sizing_gas_usd"] = gas_usd
        sized["sizing_eth_usd"] = eth_usd
        sized["sizing_min_profit_usd"] = MIN_PROFIT_FLOOR_USD
        sized["sizing_max_sims"] = MAX_SIMS_PER_CYCLE
        return sized

    def gate_profit(self, gross_profit_usd: float, gas_usd: float, net_margin_usd: float) -> tuple[bool, str]:
        """
        Decide whether an edge is executable under conservative rules.
        """
        required_gas = gas_usd * GAS_MULTIPLIER
        if gross_profit_usd < required_gas:
            return False, f"gas gate fail: {gross_profit_usd:.4f} < {required_gas:.4f}"
        if net_margin_usd < MIN_PROFIT_FLOOR_USD:
            return False, f"profit floor fail: {net_margin_usd:.4f} < {MIN_PROFIT_FLOOR_USD:.4f}"
        return True, "pass"

    def summary(self) -> dict:
        roi = ((self.state.deployable_usd - self.state.starting_usd) / self.state.starting_usd) * 100.0 if self.state.starting_usd > 0 else 0.0
        return {
            "deployable_usd": round(self.state.deployable_usd, 6),
            "starting_usd": round(self.state.starting_usd, 6),
            "total_profit_usd": round(self.state.total_profit_usd, 6),
            "total_gas_usd": round(self.state.total_gas_usd, 6),
            "roi_pct": round(roi, 3),
            "trades": self.state.trades,
            "wins": self.state.wins,
            "losses": self.state.losses,
            "last_trade_profit_usd": round(self.state.last_trade_profit_usd, 6),
        }
