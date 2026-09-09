#!/usr/bin/env python3
"""
core/error_taxonomy.py — VERITAS error classification taxonomy.

Every failure must become data. This module provides a structured way to
classify, log, and track errors across the entire pipeline.

Forbidden pattern:
    except Exception:
        pass

Allowed pattern:
    except Exception as e:
        error = ErrorTaxonomy.classify(e)
        candidate.reject(error.to_rejection_reason())
        telemetry.record_error(error)
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ErrorCategory(Enum):
    """Top-level error categories."""
    RPC = "rpc"
    DATA = "data"
    ECONOMIC = "economic"
    SIMULATION = "simulation"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    ACCOUNTING = "accounting"
    SYSTEM = "system"


class ErrorCode(Enum):
    """
    Specific error codes for every failure mode.

    These map to rejection reasons and telemetry events.
    """
    # RPC errors
    RPC_TIMEOUT = "rpc_timeout"
    RPC_ERROR = "rpc_error"
    RPC_RATE_LIMITED = "rpc_rate_limited"
    RPC_UNAVAILABLE = "rpc_unavailable"

    # Data errors
    INVALID_POOL = "invalid_pool"
    INVALID_TOKEN = "invalid_token"
    INVALID_DECIMALS = "invalid_decimals"
    STALE_DATA = "stale_data"
    QUOTE_FAILURE = "quote_failure"
    INSUFFICIENT_LIQUIDITY = "insufficient_liquidity"
    PRICE_FAILURE = "price_failure"

    # Economic errors
    GAS_FAILURE = "gas_failure"
    SLIPPAGE_FAILURE = "slippage_failure"
    ECONOMIC_FAILURE = "economic_failure"

    # Simulation errors
    SIMULATION_FAILURE = "simulation_failure"
    SIMULATION_REVERT = "simulation_revert"

    # Execution errors
    NONCE_FAILURE = "nonce_failure"
    BROADCAST_FAILURE = "broadcast_failure"
    REVERTED = "reverted"

    # Verification errors
    VERIFICATION_FAILURE = "verification_failure"
    BALANCE_MISMATCH = "balance_mismatch"

    # Accounting errors
    ACCOUNTING_FAILURE = "accounting_failure"
    DOUBLE_COUNTING = "double_counting"

    # System errors
    CONFIG_ERROR = "config_error"
    DATABASE_ERROR = "database_error"
    UNKNOWN = "unknown"


# Mapping from error codes to categories
CODE_TO_CATEGORY: dict = {
    # RPC
    ErrorCode.RPC_TIMEOUT: ErrorCategory.RPC,
    ErrorCode.RPC_ERROR: ErrorCategory.RPC,
    ErrorCode.RPC_RATE_LIMITED: ErrorCategory.RPC,
    ErrorCode.RPC_UNAVAILABLE: ErrorCategory.RPC,
    # Data
    ErrorCode.INVALID_POOL: ErrorCategory.DATA,
    ErrorCode.INVALID_TOKEN: ErrorCategory.DATA,
    ErrorCode.INVALID_DECIMALS: ErrorCategory.DATA,
    ErrorCode.STALE_DATA: ErrorCategory.DATA,
    ErrorCode.QUOTE_FAILURE: ErrorCategory.DATA,
    ErrorCode.INSUFFICIENT_LIQUIDITY: ErrorCategory.DATA,
    ErrorCode.PRICE_FAILURE: ErrorCategory.DATA,
    # Economic
    ErrorCode.GAS_FAILURE: ErrorCategory.ECONOMIC,
    ErrorCode.SLIPPAGE_FAILURE: ErrorCategory.ECONOMIC,
    ErrorCode.ECONOMIC_FAILURE: ErrorCategory.ECONOMIC,
    # Simulation
    ErrorCode.SIMULATION_FAILURE: ErrorCategory.SIMULATION,
    ErrorCode.SIMULATION_REVERT: ErrorCategory.SIMULATION,
    # Execution
    ErrorCode.NONCE_FAILURE: ErrorCategory.EXECUTION,
    ErrorCode.BROADCAST_FAILURE: ErrorCategory.EXECUTION,
    ErrorCode.REVERTED: ErrorCategory.EXECUTION,
    # Verification
    ErrorCode.VERIFICATION_FAILURE: ErrorCategory.VERIFICATION,
    ErrorCode.BALANCE_MISMATCH: ErrorCategory.VERIFICATION,
    # Accounting
    ErrorCode.ACCOUNTING_FAILURE: ErrorCategory.ACCOUNTING,
    ErrorCode.DOUBLE_COUNTING: ErrorCategory.ACCOUNTING,
    # System
    ErrorCode.CONFIG_ERROR: ErrorCategory.SYSTEM,
    ErrorCode.DATABASE_ERROR: ErrorCategory.SYSTEM,
    ErrorCode.UNKNOWN: ErrorCategory.SYSTEM,
}


@dataclass
class ClassifiedError:
    """A fully classified error with all metadata."""
    code: ErrorCode
    category: ErrorCategory
    message: str
    timestamp: float = field(default_factory=time.time)
    block_number: int = 0
    candidate_id: Optional[str] = None
    traceback_str: Optional[str] = None
    recoverable: bool = False

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "category": self.category.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "block_number": self.block_number,
            "candidate_id": self.candidate_id,
            "recoverable": self.recoverable,
        }

    def to_rejection_reason(self):
        """Convert to a RejectionReason for opportunity telemetry."""
        from core.opportunity_telemetry import RejectionReason
        mapping = {
            ErrorCode.INVALID_POOL: RejectionReason.INVALID_PAIR,
            ErrorCode.INVALID_TOKEN: RejectionReason.INVALID_PAIR,
            ErrorCode.INVALID_DECIMALS: RejectionReason.DECIMAL_NORMALIZATION_ERROR,
            ErrorCode.STALE_DATA: RejectionReason.STALE_QUOTE,
            ErrorCode.QUOTE_FAILURE: RejectionReason.NO_QUOTE,
            ErrorCode.INSUFFICIENT_LIQUIDITY: RejectionReason.NO_LIQUIDITY,
            ErrorCode.PRICE_FAILURE: RejectionReason.NO_QUOTE,
            ErrorCode.GAS_FAILURE: RejectionReason.GAS_REJECTION,
            ErrorCode.SLIPPAGE_FAILURE: RejectionReason.SAFETY_MARGIN_REJECTION,
            ErrorCode.ECONOMIC_FAILURE: RejectionReason.FEE_REJECTION,
            ErrorCode.SIMULATION_FAILURE: RejectionReason.SIMULATION_REJECTION,
            ErrorCode.SIMULATION_REVERT: RejectionReason.SIMULATION_REJECTION,
            ErrorCode.NONCE_FAILURE: RejectionReason.EXECUTION_RISK_REJECTION,
            ErrorCode.BROADCAST_FAILURE: RejectionReason.EXECUTION_RISK_REJECTION,
            ErrorCode.REVERTED: RejectionReason.SIMULATION_REJECTION,
            ErrorCode.VERIFICATION_FAILURE: RejectionReason.EXECUTION_RISK_REJECTION,
            ErrorCode.BALANCE_MISMATCH: RejectionReason.EXECUTION_RISK_REJECTION,
            ErrorCode.ACCOUNTING_FAILURE: RejectionReason.EXECUTION_RISK_REJECTION,
            ErrorCode.DOUBLE_COUNTING: RejectionReason.EXECUTION_RISK_REJECTION,
            ErrorCode.RPC_TIMEOUT: RejectionReason.NO_QUOTE,
            ErrorCode.RPC_ERROR: RejectionReason.NO_QUOTE,
            ErrorCode.RPC_RATE_LIMITED: RejectionReason.NO_QUOTE,
            ErrorCode.RPC_UNAVAILABLE: RejectionReason.NO_QUOTE,
        }
        return mapping.get(self.code, RejectionReason.NO_OPPORTUNITY)


class ErrorTaxonomy:
    """
    Central error classification system.

    Usage:
        try:
            result = rpc.eth_call(...)
        except Exception as e:
            error = ErrorTaxonomy.classify(e, candidate_id="abc123")
            telemetry.record_error(error)
    """

    @staticmethod
    def classify(
        exception: Exception,
        candidate_id: Optional[str] = None,
        block_number: int = 0,
        capture_traceback: bool = True,
    ) -> ClassifiedError:
        """Classify an exception into a structured error."""
        msg = str(exception).lower()
        exc_type = type(exception).__name__

        # Determine error code
        code = ErrorTaxonomy._determine_code(msg, exc_type)

        # Determine if recoverable
        recoverable = code in {
            ErrorCode.RPC_TIMEOUT,
            ErrorCode.RPC_RATE_LIMITED,
            ErrorCode.STALE_DATA,
            ErrorCode.QUOTE_FAILURE,
        }

        tb = None
        if capture_traceback:
            tb = traceback.format_exc()

        return ClassifiedError(
            code=code,
            category=CODE_TO_CATEGORY.get(code, ErrorCategory.SYSTEM),
            message=str(exception),
            block_number=block_number,
            candidate_id=candidate_id,
            traceback_str=tb,
            recoverable=recoverable,
        )

    @staticmethod
    def _determine_code(msg: str, exc_type: str) -> ErrorCode:
        """Determine the error code from exception message and type."""
        # RPC errors
        if "timeout" in msg or "timed out" in msg:
            return ErrorCode.RPC_TIMEOUT
        if "rate limit" in msg or "429" in msg or "too many requests" in msg:
            return ErrorCode.RPC_RATE_LIMITED
        if "connection" in msg or "unreachable" in msg or "refused" in msg:
            return ErrorCode.RPC_UNAVAILABLE
        if "jsonrpc" in msg or "rpc" in msg:
            return ErrorCode.RPC_ERROR

        # Data errors
        if "invalid pool" in msg or "pool not found" in msg:
            return ErrorCode.INVALID_POOL
        if "invalid token" in msg or "token not found" in msg:
            return ErrorCode.INVALID_TOKEN
        if "decimals" in msg:
            return ErrorCode.INVALID_DECIMALS
        if "stale" in msg or "expired" in msg or "old" in msg:
            return ErrorCode.STALE_DATA
        if "quote" in msg and ("fail" in msg or "error" in msg or "unavailable" in msg):
            return ErrorCode.QUOTE_FAILURE
        if "liquidity" in msg:
            return ErrorCode.INSUFFICIENT_LIQUIDITY
        if "price" in msg and ("fail" in msg or "unavailable" in msg):
            return ErrorCode.PRICE_FAILURE

        # Economic errors
        if "gas" in msg and ("fail" in msg or "too high" in msg or "exceeds" in msg):
            return ErrorCode.GAS_FAILURE
        if "slippage" in msg:
            return ErrorCode.SLIPPAGE_FAILURE

        # Simulation errors
        if "revert" in msg:
            return ErrorCode.SIMULATION_REVERT
        if "simulation" in msg and ("fail" in msg or "error" in msg):
            return ErrorCode.SIMULATION_FAILURE

        # Execution errors
        if "nonce" in msg:
            return ErrorCode.NONCE_FAILURE
        if "broadcast" in msg or "send" in msg and "fail" in msg:
            return ErrorCode.BROADCAST_FAILURE

        # Verification errors
        if "balance" in msg and ("mismatch" in msg or "unexpected" in msg):
            return ErrorCode.BALANCE_MISMATCH
        if "verification" in msg and "fail" in msg:
            return ErrorCode.VERIFICATION_FAILURE

        # Accounting errors
        if "double" in msg or "duplicate" in msg:
            return ErrorCode.DOUBLE_COUNTING
        if "accounting" in msg:
            return ErrorCode.ACCOUNTING_FAILURE

        # System errors
        if "config" in msg:
            return ErrorCode.CONFIG_ERROR
        if "database" in msg or "sqlite" in msg or "db" in msg:
            return ErrorCode.DATABASE_ERROR

        return ErrorCode.UNKNOWN

    @staticmethod
    def make_error(
        code: ErrorCode,
        message: str,
        candidate_id: Optional[str] = None,
        block_number: int = 0,
        recoverable: bool = False,
    ) -> ClassifiedError:
        """Create a classified error directly (without an exception)."""
        return ClassifiedError(
            code=code,
            category=CODE_TO_CATEGORY.get(code, ErrorCategory.SYSTEM),
            message=message,
            block_number=block_number,
            candidate_id=candidate_id,
            recoverable=recoverable,
        )
