#!/usr/bin/env python3
"""
core/route_valuation.py — VERITAS canonical full-path route valuation.

Single shared implementation used by BOTH the forensic funnel
(_opportunity_funnel.py) and the production engine (veritas_engine.py).

Pipeline per route:
  validate_route_shape()
    -> quote_full_route() hop-by-hop (hop N out becomes hop N+1 in)
    -> per-hop provenance gate (authoritative quotes only)
    -> same-asset final comparison (A_final vs A_initial, never B vs A)
    -> canonical EconomicModel.evaluate()
    -> threshold classification via is_worth_executing

Canonical invariant: different-token amounts are never economically comparable.

DEX-fee accounting note (see core/economic_model.py): QuoterV2 / CPMM quote
outputs are POST-fee, so EconomicModel.evaluate() sets dex_fees_usd = 0.0.
Callers must NOT separately subtract pool fee tiers on top, or fees are
double-counted.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Failure-reason prefixes. These double as rejection-histogram keys
# (consumers split on the first ":").
REASON_MALFORMED = "route_malformed"
REASON_CHAIN_BREAK = "chain_break"
REASON_NO_POOL = "no_pool_for_step"
REASON_NO_LIQUIDITY = "no_liquidity_at_step"
REASON_QUOTE_FAILED = "quote_failed_at_step"
REASON_NON_AUTHORITATIVE = "non_authoritative_quote_at_step"

# Shared economic assumptions (mirror core/economic_model.py defaults).
# Canonical thresholds live in EconomicModel; these exist only for the
# forensic funnel's display-level economics and batch scripts.
CHAIN_ID = 42161
ETH_PRICE_USD = 2500.0

# V3 slot0() selector used for the lightweight liveness probe.
_SLOT0_SELECTOR = "0x3850c7bd"


def validate_route_shape(route) -> Tuple[bool, Optional[str]]:
    """Check triangular closure and hop continuity.

    Returns (ok, reason). A route must start and end on the same token
    with every hop's output feeding the next hop's input.
    """
    steps = getattr(route, "steps", None) or []
    if not steps:
        return False, f"{REASON_MALFORMED}: empty steps"
    first_in = steps[0].token_in or ""
    last_out = steps[-1].token_out or ""
    if first_in.lower() != last_out.lower():
        return False, f"{REASON_MALFORMED}: start != end token"
    for k in range(len(steps) - 1):
        out_k = steps[k].token_out or ""
        in_next = steps[k + 1].token_in or ""
        if out_k.lower() != in_next.lower():
            return False, f"{REASON_MALFORMED}: chain_break at step {k}->{k + 1}"
    return True, None


def resolve_step_pool(step, registry, chain_id: int):
    """Resolve the pool for a single route step.

    Prefers the pool address baked into the step; falls back to the
    pair+venue lookup. Returns None if no pool is found.
    """
    addr = (getattr(step, "pool_address", None) or "").strip()
    if addr and addr != "0x":
        pool = registry.get_by_address(chain_id, addr)
        if pool is not None:
            return pool
    pools = registry.get_pools_for_pair_and_venue(
        step.token_in, step.token_out, step.venue
    )
    return pools[0] if pools else None


def quote_full_route(
    route,
    amount_in_native: int,
    quote_engine,
    registry,
    resilient_rpc,
    chain_id: int,
) -> Tuple[Optional[int], List[Dict[str, Any]], Optional[str]]:
    """Quote every hop of a route in sequence.

    amount_out of hop N becomes amount_in of hop N+1, so the final amount
    is denominated in the START token and directly comparable to the
    initial input.

    Provenance gate: any hop whose quote is not authoritative
    (spot-price fallback) fails the whole route with
    ``non_authoritative_quote_at_step``. Fallback numbers are diagnostics,
    never economics inputs.

    Returns (final_amount_out, hop_records, failure_reason).
    On success failure_reason is None.
    """
    hop_records: List[Dict[str, Any]] = []
    current_amount = amount_in_native
    current_token = route.steps[0].token_in

    for idx, step in enumerate(route.steps):
        if (step.token_in or "").lower() != (current_token or "").lower():
            return (
                None,
                hop_records,
                f"{REASON_CHAIN_BREAK}: step {idx} expects {step.token_in} "
                f"got {current_token}",
            )

        pool = resolve_step_pool(step, registry, chain_id)
        if pool is None:
            return (
                None,
                hop_records,
                f"{REASON_NO_POOL}: step {idx} {step.venue} "
                f"{(step.token_in or '')[:10]}->{(step.token_out or '')[:10]}",
            )

        if pool.kind == "v3" and pool.liquidity == 0:
            try:
                slot0 = resilient_rpc.eth_call(
                    pool.pool_id.pool_address, _SLOT0_SELECTOR
                )
                if slot0 and len(slot0) >= 66:
                    sqrt_px = int(slot0[2:66], 16)
                    if sqrt_px > 0:
                        pool.liquidity = 1
            except Exception:
                return None, hop_records, f"{REASON_NO_LIQUIDITY}: step {idx}"

        try:
            quote = quote_engine.quote(
                step.token_in, step.token_out, current_amount, pool
            )
        except Exception as e:
            return None, hop_records, f"{REASON_QUOTE_FAILED}: step {idx}: {e}"

        if not quote.success or quote.amount_out <= 0:
            return (
                None,
                hop_records,
                f"{REASON_QUOTE_FAILED}: step {idx}: {quote.error}",
            )

        hop_record = {
            "hop": idx,
            "venue": step.venue,
            "pool_address": pool.pool_id.pool_address,
            "fee_tier": pool.fee,
            "token_in": step.token_in,
            "token_out": step.token_out,
            "amount_in": current_amount,
            "amount_out": quote.amount_out,
            "confidence": quote.confidence,
            "source": quote.source,
            "authoritative": quote.authoritative,
            "gas_estimate": quote.gas_estimate,
            "price_impact_bps": quote.price_impact_bps,
            "block_number": quote.block_number,
        }
        hop_records.append(hop_record)

        if not quote.authoritative:
            return (
                None,
                hop_records,
                f"{REASON_NON_AUTHORITATIVE}: step {idx} "
                f"source={quote.source}",
            )

        current_amount = quote.amount_out
        current_token = step.token_out

    return current_amount, hop_records, None


def start_token_decimals(route, first_hop_pool, default: int = 18) -> int:
    """Resolve the start token's decimals from the first-hop pool metadata."""
    try:
        start = (route.steps[0].token_in or "").lower()
        if (first_hop_pool.token0 or "").lower() == start:
            return int(first_hop_pool.decimals0 or default)
        if (first_hop_pool.token1 or "").lower() == start:
            return int(first_hop_pool.decimals1 or default)
    except Exception:
        pass
    return default


def evaluate_route_economics(
    amount_in_native: int,
    input_usd: float,
    final_out_native: int,
    hops: List[Dict[str, Any]],
    economic_model,
    gas_price_gwei: float = 0.01,
    block_number: int = 0,
):
    """Map a completed full-path quote onto the canonical EconomicModel.

    Same-asset ratio math: ``gross_output_usd = input_usd * final / initial``.
    Both amounts are in the start token's native units, so this holds for any
    decimals. DEX fees are NOT subtracted here: quote outputs are post-fee
    (see economic_model.py); separate subtraction would double-count.

    Gas units are summed across hops; slippage is the summed hop price
    impacts in bps (conservative aggregation).
    """
    if amount_in_native > 0:
        gross_output_usd = input_usd * (final_out_native / amount_in_native)
    else:
        gross_output_usd = 0.0
    total_gas_units = sum(int(h.get("gas_estimate", 0) or 0) for h in hops)
    if total_gas_units <= 0:
        total_gas_units = 350000
    slippage_bps = int(
        round(sum(float(h.get("price_impact_bps", 0) or 0) for h in hops))
    )
    return economic_model.evaluate(
        input_usd=input_usd,
        gross_output_usd=gross_output_usd,
        flash_loan_amount_usd=input_usd,
        estimated_gas_units=total_gas_units,
        gas_price_gwei=gas_price_gwei,
        slippage_bps=slippage_bps,
        block_number=block_number,
    )
