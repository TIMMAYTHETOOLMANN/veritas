#!/usr/bin/env python3
"""Fix execution_gate.py - remove pool check."""
content = '''#!/usr/bin/env python3
"""
core/execution_gate.py - VERITAS execution gate.

No broadcast unless ALL conditions are true:
  - fresh quote
  - valid reserves (quote is valid)
  - positive expected net profit
  - profit > minimum threshold
  - gas below maximum
  - slippage below maximum
  - capital exposure below maximum
  - simulation passes
  - RPC healthy
  - candidate not already executed

Returns: PASS or REJECT(reason=...)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from core.economic_model import EconomicResult
from core.quote_engine import QuoteResult
from core.rpc_health import RPCHealthMonitor


@dataclass
class GateResult:
    """Result of the execution gate check."""
    passed: bool
    reason: Optional[str] = None
    detail: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "REJECT"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }


@dataclass
class GateConfig:
    """Configuration for the execution gate thresholds."""
    max_gas_usd: float = 1.00
    max_slippage_bps: int = 50
    max_capital_exposure_usd: float = 100.0
    min_profit_usd: float = 0.01
    max_quote_age_seconds: int = 30
    max_block_age: int = 5
    require_simulation_pass: bool = True
    require_rpc_healthy: bool = True
    require_fresh_price: bool = True
    max_consecutive_failures: int = 3


class ExecutionGate:
    """VERITAS execution gate."""

    def __init__(self, config: GateConfig, rpc_health: Optional[RPCHealthMonitor] = None):
        self.config = config
        self.rpc_health = rpc_health
        self._consecutive_failures = 0
        self._executed_candidates: set = set()

    def check(self, quote: QuoteResult, economic_result: EconomicResult,
              sim_passed: bool = False, candidate_id: str = "", block_number: int = 0) -> GateResult:
        # 1. Fresh quote
        if not quote.is_fresh(self.config.max_quote_age_seconds):
            return self._reject("stale_quote", "Quote age exceeds limit")

        # 2. Valid quote
        if not quote.is_valid:
            return self._reject("invalid_quote", "Quote invalid")

        # 3. Positive expected net profit
        if economic_result.expected_net_profit_usd <= 0:
            return self._reject("not_profitable", "Net profit <= 0")

        # 4. Profit > minimum threshold
        if economic_result.expected_net_profit_usd < self.config.min_profit_usd:
            return self._reject("below_min_profit", "Profit below minimum")

        # 5. Gas below maximum
        if economic_result.estimated_gas_usd > self.config.max_gas_usd:
            return self._reject("gas_too_high", "Gas exceeds maximum")

        # 6. Slippage below maximum
        if quote.price_impact_bps > self.config.max_slippage_bps:
            return self._reject("slippage_too_high", "Slippage exceeds maximum")

        # 7. Capital exposure below maximum
        if economic_result.input_usd > self.config.max_capital_exposure_usd:
            return self._reject("capital_exposure_too_high", "Exposure exceeds maximum")

        # 8. Simulation passes
        if self.config.require_simulation_pass and not sim_passed:
            return self._reject("simulation_required", "Simulation did not pass")

        # 9. RPC healthy
        if self.config.require_rpc_healthy and self.rpc_health:
            if not self.rpc_health.is_healthy():
                return self._reject("rpc_unhealthy", "RPC health check failed")

        # 10. Candidate not already executed
        if candidate_id and candidate_id in self._executed_candidates:
            return self._reject("already_executed", "Candidate already executed")

        # 11. Circuit breaker
        if self._consecutive_failures >= self.config.max_consecutive_failures:
            return self._reject("circuit_breaker", "Too many consecutive failures")

        # All checks passed
        self._consecutive_failures = 0
        if candidate_id:
            self._executed_candidates.add(candidate_id)
        return GateResult(passed=True)

    def record_failure(self) -> None:
        self._consecutive_failures += 1

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._executed_candidates.clear()

    def _reject(self, reason: str, detail: str) -> GateResult:
        self._consecutive_failures += 1
        return GateResult(passed=False, reason=reason, detail=detail)
'''

with open('core/execution_gate.py', 'w') as f:
    f.write(content)
print("Fixed execution_gate.py")
