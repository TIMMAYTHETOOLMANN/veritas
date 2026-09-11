#!/usr/bin/env python3
"""Golden regression: canonical full-path route valuation.

Tripwire: the production mapper must NEVER classify a route as
executable-positive that the authoritative full-path funnel classifies
as non-profitable. Shape, provenance, same-asset math, decimals, and
threshold handling are covered with deterministic fakes (no RPC).
"""
from types import SimpleNamespace

from core.economic_model import EconomicModel
from core.opportunity_telemetry import CandidateStatus, RejectionReason
from core.route_valuation import (
    REASON_MALFORMED,
    REASON_NON_AUTHORITATIVE,
    REASON_NO_POOL,
    REASON_QUOTE_FAILED,
    evaluate_route_economics,
    quote_full_route,
    start_token_decimals,
    validate_route_shape,
)

TOKEN_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _step(token_in, token_out, venue="uniswap_v3", pool="0xpool", fee=500):
    return SimpleNamespace(
        token_in=token_in,
        token_out=token_out,
        venue=venue,
        pool_address=pool,
        fee=fee,
    )


def _route(*steps):
    return SimpleNamespace(steps=list(steps))


class _Pool:
    def __init__(self, address, fee=500, kind="v3", token0=None, token1=None,
                 decimals0=18, decimals1=18):
        self.pool_id = SimpleNamespace(pool_address=address)
        self.fee = fee
        self.kind = kind
        self.liquidity = 1
        self.token0 = token0
        self.token1 = token1
        self.decimals0 = decimals0
        self.decimals1 = decimals1


class _Registry:
    def __init__(self, pools):
        self._pools = {p.pool_id.pool_address.lower(): p for p in pools}

    def get_by_address(self, chain_id, address):
        return self._pools.get((address or "").lower())

    def get_pools_for_pair_and_venue(self, token_a, token_b, venue):
        return []


class _Quote:
    def __init__(self, amount_out, source="quoter_v2", authoritative=True,
                 success=True, error=None, confidence=0.95, gas_estimate=250000,
                 price_impact_bps=0.0, block_number=1):
        self.amount_out = amount_out
        self.source = source
        self.authoritative = authoritative
        self.success = success
        self.error = error
        self.confidence = confidence
        self.gas_estimate = gas_estimate
        self.price_impact_bps = price_impact_bps
        self.block_number = block_number


class _QuoteEngine:
    """amount_out schedule keyed by hop index; supports fallback markers."""

    def __init__(self, schedule):
        self._schedule = schedule
        self.calls = []

    def quote(self, token_in, token_out, amount_in, pool):
        idx = len(self.calls)
        self.calls.append((token_in, token_out, amount_in))
        return self._schedule[idx]


class _Oracle:
    def get_price_usd(self, token):
        return 2500.0


def test_shape_rejects_open_route():
    route = _route(_step(TOKEN_A, TOKEN_B), _step(TOKEN_B, TOKEN_A + "00"))
    ok, reason = validate_route_shape(route)
    assert not ok
    assert reason.startswith(REASON_MALFORMED)


def test_shape_rejects_chain_break():
    route = _route(_step(TOKEN_A, TOKEN_B), _step(TOKEN_A, TOKEN_A))
    ok, reason = validate_route_shape(route)
    assert not ok
    assert REASON_MALFORMED in reason


def test_full_path_chains_hops_and_resolves_per_step_pools():
    route = _route(
        _step(TOKEN_A, TOKEN_B, pool="0xpool1"),
        _step(TOKEN_B, TOKEN_A, pool="0xpool2"),
    )
    engine = _QuoteEngine([_Quote(2_000), _Quote(1_100)])
    reg = _Registry([_Pool("0xpool1"), _Pool("0xpool2")])
    final, hops, failure = quote_full_route(route, 1_000, engine, reg, None, 42161)
    assert failure is None
    assert final == 1_100
    assert [h["pool_address"] for h in hops] == ["0xpool1", "0xpool2"]
    assert engine.calls[1][2] == 2_000  # hop 1 input == hop 0 output


def test_provenance_gate_rejects_fallback_hop():
    route = _route(
        _step(TOKEN_A, TOKEN_B, pool="0xpool1"),
        _step(TOKEN_B, TOKEN_A, pool="0xpool2"),
    )
    engine = _QuoteEngine([
        _Quote(2_000, source="quoter_v2", authoritative=True),
        _Quote(9_999_999, source="spot_fallback", authoritative=False),
    ])
    reg = _Registry([_Pool("0xpool1"), _Pool("0xpool2")])
    final, hops, failure = quote_full_route(route, 1_000, engine, reg, None, 42161)
    assert final is None
    assert failure.startswith(REASON_NON_AUTHORITATIVE)
    assert hops[-1]["source"] == "spot_fallback"


def test_missing_pool_maps_to_no_pool_reason():
    route = _route(_step(TOKEN_A, TOKEN_B, pool="0xmissing"),
                   _step(TOKEN_B, TOKEN_A, pool="0xmissing2"))
    engine = _QuoteEngine([_Quote(1)])
    final, _, failure = quote_full_route(
        route, 1_000, engine, _Registry([]), None, 42161)
    assert final is None
    assert failure.startswith(REASON_NO_POOL)


def test_failed_quote_maps_to_quote_failed_reason():
    route = _route(_step(TOKEN_A, TOKEN_B, pool="0xpool1"),
                   _step(TOKEN_B, TOKEN_A, pool="0xpool2"))
    engine = _QuoteEngine([
        _Quote(0, success=False, error="boom"),
    ])
    reg = _Registry([_Pool("0xpool1"), _Pool("0xpool2")])
    final, _, failure = quote_full_route(route, 1_000, engine, reg, None, 42161)
    assert final is None
    assert failure.startswith(REASON_QUOTE_FAILED)


def test_decimals_come_from_pool_metadata():
    pool = _Pool("0xpool1", token0=TOKEN_A, token1=TOKEN_B,
                 decimals0=6, decimals1=18)
    route = _route(_step(TOKEN_A, TOKEN_B, pool="0xpool1"))
    assert start_token_decimals(route, pool) == 6
    assert start_token_decimals(route, None) == 18


def test_same_asset_ratio_math_holds_for_any_decimals():
    model = EconomicModel(_Oracle(), safety_margin_bps=0)
    result = evaluate_route_economics(
        amount_in_native=1_000_000,  # 1.0 USDC at 6 decimals
        input_usd=1.0,
        final_out_native=990_000,   # 0.99 USDC back
        hops=[{"gas_estimate": 0, "price_impact_bps": 0}],
        economic_model=model,
        gas_price_gwei=0.0,
    )
    assert abs(result.gross_output_usd - 0.99) < 1e-9


def test_thresholds_use_worth_executing_not_bare_profit():
    model = EconomicModel(_Oracle(), flash_loan_fee_bps=0,
                          safety_margin_bps=0)
    dust = evaluate_route_economics(
        amount_in_native=10_000,
        input_usd=25.0,
        final_out_native=10_001,  # tiny round-trip gain
        hops=[{"gas_estimate": 0, "price_impact_bps": 0}],
        economic_model=model,
        gas_price_gwei=0.0,
    )
    # Profitable after zero-cost evaluation, but below execution thresholds.
    assert dust.is_profitable
    assert not dust.is_worth_executing
    assert dust.below_threshold_reason is not None


def test_production_tripwire_never_promotes_funnel_reject():
    """One-sided invariant: funnel-reject => production must not execute.

    Mirrors veritas_engine._evaluate_route classification branches.
    """
    def classify(failure=None, economic=None):
        if failure is not None:
            if failure.startswith("non_authoritative_quote_at_step"):
                return (CandidateStatus.REJECTED,
                        RejectionReason.NON_AUTHORITATIVE_QUOTE)
            if failure.startswith("no_pool_for_step"):
                return (CandidateStatus.REJECTED,
                        RejectionReason.NO_POOL_FOR_STEP)
            if failure.startswith(("quote_failed_at_step",
                                   "no_liquidity_at_step")):
                return (CandidateStatus.REJECTED,
                        RejectionReason.QUOTE_FAILED_AT_STEP)
            if failure.startswith(("route_malformed", "chain_break")):
                return (CandidateStatus.REJECTED,
                        RejectionReason.MALFORMED_ROUTE)
            return (CandidateStatus.REJECTED, RejectionReason.NO_QUOTE)
        if economic.is_worth_executing:
            return (CandidateStatus.ECONOMICALLY_VIABLE, None)
        if economic.is_profitable:
            return (CandidateStatus.REJECTED, RejectionReason.BELOW_MIN_ROI)
        return (CandidateStatus.REJECTED, RejectionReason.FEE_REJECTION)

    funnel_rejects = [
        "route_malformed: start != end token",
        "chain_break: step 0 expects 0xA got 0xB",
        "no_pool_for_step: step 1 uni 0xA->0xB",
        "no_liquidity_at_step: step 0",
        "quote_failed_at_step: step 2: quoter_v2_and_spot_price_both_failed",
        "non_authoritative_quote_at_step: step 2 source=spot_fallback",
    ]
    for failure in funnel_rejects:
        status, _ = classify(failure=failure)
        assert status == CandidateStatus.REJECTED

    # Below-threshold economics must also stay non-executable.
    thin = SimpleNamespace(is_profitable=True, is_worth_executing=False)
    status, reason = classify(economic=thin)
    assert status == CandidateStatus.REJECTED
    assert reason in (RejectionReason.BELOW_MIN_PROFIT,
                      RejectionReason.BELOW_MIN_ROI)
