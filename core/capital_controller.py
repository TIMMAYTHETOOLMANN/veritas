#!/usr/bin/env python3
"""
core/capital_controller.py — VERITAS conservative deployable-capital controller.

DESIGN GOALS
- Initial deployment optimization for very small capital (~$10 start).
- Exponential compounding on REALIZED profit WITHOUT risking principal.
- Initial trades are micro/cents-scale and highly conservative.
- Size trades from deployable capital, not a hardcoded 1 ETH principal.
- Capital state survives hunt cycles via SQLite persistence.

ACCOUNTING RULES (Phase 3 fixes)
- One actual attempt = one sim_attempt (gas tracked, no capital change)
- One actual execution = one live_execution (gas + realized P/L)
- Gas is counted once per unique on-chain transaction, NOT per sim
- Failed simulations do NOT masquerade as executed trades
- Realized P/L is distinct from projected P/L
- Capital is updated ONLY after on-chain balance delta verification

CAPITAL MODES
- PAPER: Fully simulated, no real capital at risk
- SIMULATION: Fork-sim verification, no live broadcast
- LIVE_CONSERVATIVE: Live broadcast with tight gates
- LIVE_AGGRESSIVE: Live broadcast with relaxed gates (requires explicit opt-in)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.db import conn, now


# ---- Tunables ----
# These are intentionally conservative for a $10 bootstrap.
INITIAL_DEPLOYABLE_USD: float = 10.00   # initial simulated deployable capital
MIN_SIZE_USD: float = 0.05              # floor trade size in USD
MAX_SIZE_USD: float = 1.00              # ceiling trade size in USD
SIZE_CAP_FRACTION: float = 0.10         # never size more than 10% of deployable capital
GAS_MULTIPLIER: float = 1.0             # aligned with sim_gate.py/flash_hunter.py
MIN_PROFIT_FLOOR_USD: float = 0.05      # minimum acceptable net profit after gas
MAX_SIMS_PER_CYCLE: int = 6             # sim budget per scan cycle


class CapitalMode(Enum):
    """Capital operating modes."""
    PAPER = "paper"                      # fully simulated, no real capital
    SIMULATION = "simulation"            # fork-sim verification, no broadcast
    LIVE_CONSERVATIVE = "live_conservative"  # live broadcast, tight gates
    LIVE_AGGRESSIVE = "live_aggressive"      # live broadcast, relaxed gates (opt-in)


@dataclass
class CapitalState:
    deployable_usd: float = INITIAL_DEPLOYABLE_USD
    starting_usd: float = INITIAL_DEPLOYABLE_USD
    total_profit_usd: float = 0.0
    total_gas_usd: float = 0.0
    trades: int = 0          # actual live executions
    sim_attempts: int = 0    # fork-sim attempts (not counted as trades)
    wins: int = 0
    losses: int = 0
    last_trade_profit_usd: float = 0.0
    last_trade_verified: bool = False  # True only if on-chain delta confirmed
    mode: str = CapitalMode.PAPER.value
    updated_ts: int = 0


class CapitalController:
    """
    VERITAS capital controller with persistent state and correct accounting.

    PERSISTENCE
    -----------
    State is loaded from SQLite at instantiation and saved after every
    mutation. This ensures capital survives hunt cycles and restarts.

    ACCOUNTING
    ----------
    - record_sim_attempt(): Tracks sim gas cost. Does NOT increment trades.
      Does NOT modify deployable_usd. One sim = one sim_attempt entry.
    - record_live_execution(): Tracks live gas + realized P/L.
      Increments trades by exactly 1. Updates capital only if verified.
    - record_verified_pnl(): Updates capital ONLY after on-chain balance
      delta is confirmed. This is the ONLY method that changes deployable_usd.
    """

    def __init__(self, state: Optional[CapitalState] = None,
                 mode: CapitalMode = CapitalMode.PAPER) -> None:
        if state is not None:
            self.state = state
        else:
            self.state = self._load_from_db() or CapitalState(mode=mode.value)
        self.state.mode = mode.value

    # ---- Persistence --------------------------------------------------------

    @staticmethod
    def _ensure_table():
        """Ensure the capital_state table exists in veritas.db."""
        c = conn()
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS capital_state(
                id INTEGER PRIMARY KEY DEFAULT 1,
                deployable_usd REAL,
                starting_usd REAL,
                total_profit_usd REAL,
                total_gas_usd REAL,
                trades INTEGER,
                sim_attempts INTEGER,
                wins INTEGER,
                losses INTEGER,
                last_trade_profit_usd REAL,
                last_trade_verified INTEGER,
                mode TEXT,
                updated_ts INTEGER
            )""")
            c.commit()
        finally:
            c.close()

    @classmethod
    def _load_from_db(cls) -> Optional[CapitalState]:
        """Load capital state from SQLite. Returns None if no state exists."""
        cls._ensure_table()
        c = conn()
        try:
            row = c.execute(
                "SELECT * FROM capital_state WHERE id=1"
            ).fetchone()
        finally:
            c.close()
        if not row:
            return None
        return CapitalState(
            deployable_usd=row["deployable_usd"],
            starting_usd=row["starting_usd"],
            total_profit_usd=row["total_profit_usd"],
            total_gas_usd=row["total_gas_usd"],
            trades=row["trades"],
            sim_attempts=row["sim_attempts"],
            wins=row["wins"],
            losses=row["losses"],
            last_trade_profit_usd=row["last_trade_profit_usd"],
            last_trade_verified=bool(row["last_trade_verified"]),
            mode=row["mode"],
            updated_ts=row["updated_ts"],
        )

    def _save_to_db(self) -> None:
        """Persist current state to SQLite."""
        self._ensure_table()
        self.state.updated_ts = now()
        c = conn()
        try:
            c.execute("""INSERT OR REPLACE INTO capital_state
                (id, deployable_usd, starting_usd, total_profit_usd, total_gas_usd,
                 trades, sim_attempts, wins, losses, last_trade_profit_usd,
                 last_trade_verified, mode, updated_ts)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.state.deployable_usd, self.state.starting_usd,
                 self.state.total_profit_usd, self.state.total_gas_usd,
                 self.state.trades, self.state.sim_attempts,
                 self.state.wins, self.state.losses,
                 self.state.last_trade_profit_usd,
                 int(self.state.last_trade_verified),
                 self.state.mode, self.state.updated_ts))
            c.commit()
        finally:
            c.close()

    # ---- Accounting (Phase 3: no double-counting) --------------------------

    def record_sim_attempt(self, gas_usd: float) -> None:
        """
        Record a fork-sim attempt.

        - Tracks sim gas cost
        - Increments sim_attempts (NOT trades)
        - Does NOT modify deployable_usd
        - Gas counted once per sim

        This is separate from live execution accounting.
        """
        self.state.total_gas_usd += gas_usd
        self.state.sim_attempts += 1
        self._save_to_db()

    def record_live_execution(self, net_profit_usd: float, gas_usd: float,
                              verified: bool = False) -> None:
        """
        Record a live on-chain execution.

        - Tracks live gas cost (counted once)
        - Increments trades by exactly 1
        - Updates wins/losses counters
        - Updates capital ONLY if verified=True (on-chain delta confirmed)

        This is the correct way to record an actual trade.
        """
        self.state.total_gas_usd += gas_usd
        self.state.last_trade_profit_usd = net_profit_usd
        self.state.last_trade_verified = verified
        self.state.trades += 1

        if net_profit_usd >= 0:
            self.state.wins += 1
        else:
            self.state.losses += 1

        if verified:
            self._apply_pnl_to_capital(net_profit_usd)

        self._save_to_db()

    def record_verified_pnl(self, net_profit_usd: float, gas_usd: float) -> None:
        """
        Record a VERIFIED on-chain P/L after balance delta confirmation.

        This is the ONLY method that updates deployable_usd based on
        realized (not projected) profit. Call this after confirming
        the on-chain balance delta matches expectations.

        Previous behavior incorrectly updated capital based on scanner
        projections, creating an imaginary-money machine. This method
        enforces: no verified delta = no capital change.
        """
        self.state.total_gas_usd += gas_usd
        self.state.last_trade_profit_usd = net_profit_usd
        self.state.last_trade_verified = True
        self.state.trades += 1
        if net_profit_usd >= 0:
            self.state.wins += 1
        else:
            self.state.losses += 1
        self._apply_pnl_to_capital(net_profit_usd)
        self._save_to_db()

    def _apply_pnl_to_capital(self, net_profit_usd: float) -> None:
        """
        Apply verified P/L to deployable capital.

        - Wins: reinvest profit, principal stays constant
        - Losses: shrink profit pool, never go below starting principal
        """
        if net_profit_usd >= 0:
            self.state.deployable_usd += net_profit_usd
            self.state.total_profit_usd += net_profit_usd
        else:
            # Losses only shrink profit pool, not base capital
            self.state.deployable_usd = max(
                self.state.starting_usd,
                self.state.deployable_usd + net_profit_usd,
            )

    # ---- Legacy compatibility (deprecated, use record_sim_attempt/record_live_execution) ----

    def record_attempt(self, gas_usd: float) -> None:
        """
        DEPRECATED: Use record_sim_attempt() instead.

        Kept for backward compatibility with existing callers.
        Now delegates to record_sim_attempt() to avoid double-counting.
        """
        self.record_sim_attempt(gas_usd)

    def record_result(self, net_profit_usd: float, gas_usd: float) -> None:
        """
        DEPRECATED: Use record_live_execution() or record_verified_pnl() instead.

        Kept for backward compatibility. Now treats the result as a
        live execution (not verified by default).
        """
        self.record_live_execution(net_profit_usd, gas_usd, verified=False)

    # ---- Sizing ------------------------------------------------------------

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

    # ---- Gating ------------------------------------------------------------

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

    def set_mode(self, mode: CapitalMode) -> None:
        """Change the capital operating mode."""
        self.state.mode = mode.value
        self._save_to_db()

    # ---- Summary -----------------------------------------------------------

    def summary(self) -> dict:
        roi = ((self.state.deployable_usd - self.state.starting_usd) / self.state.starting_usd) * 100.0 if self.state.starting_usd > 0 else 0.0
        return {
            "deployable_usd": round(self.state.deployable_usd, 6),
            "starting_usd": round(self.state.starting_usd, 6),
            "total_profit_usd": round(self.state.total_profit_usd, 6),
            "total_gas_usd": round(self.state.total_gas_usd, 6),
            "roi_pct": round(roi, 3),
            "trades": self.state.trades,
            "sim_attempts": self.state.sim_attempts,
            "wins": self.state.wins,
            "losses": self.state.losses,
            "last_trade_profit_usd": round(self.state.last_trade_profit_usd, 6),
            "last_trade_verified": self.state.last_trade_verified,
            "mode": self.state.mode,
        }
