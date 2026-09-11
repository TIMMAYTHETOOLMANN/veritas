#!/usr/bin/env python3
"""
Opportunity Funnel Analysis

Measures the actual search density of the VERITAS scanner.
"""
import os
import sys
import time
import argparse
import json

os.environ['PYTHONIOENCODING'] = 'utf-8'

from core.external_apis import AlchemyRPC
from core.rpc_resilience import ResilientRPC, RPCCache
from core.pool import PoolRegistry
from core.route_generator import RouteGenerator
from core.market_discovery import MarketDiscovery
from core.quote_engine_v2 import QuoteEngine
from core.economic_model import EconomicModel
from core.price_oracle import PriceOracle
from core.route_valuation import (
    CHAIN_ID,
    ETH_PRICE_USD,
    evaluate_route_economics,
    quote_full_route,
    validate_route_shape,
)


def main():
    # Batching via env vars (run config does not support CLI overrides):
    #   FUNNEL_START, FUNNEL_LIMIT, FUNNEL_CHECKPOINT, FUNNEL_BUDGET_S
    # Examples (PowerShell):
    #   $env:FUNNEL_START=0; $env:FUNNEL_LIMIT=100; $env:FUNNEL_CHECKPOINT='C:/tmp/funnel_b0.jsonl'; $env:FUNNEL_BUDGET_S=45; python _opportunity_funnel.py
    #   $env:FUNNEL_START=100; $env:FUNNEL_LIMIT=100; $env:FUNNEL_CHECKPOINT='C:/tmp/funnel_b1.jsonl'; python _opportunity_funnel.py
    env_start = os.environ.get("FUNNEL_START")
    env_limit = os.environ.get("FUNNEL_LIMIT")
    env_checkpoint = os.environ.get("FUNNEL_CHECKPOINT")
    env_budget = os.environ.get("FUNNEL_BUDGET_S")

    parser = argparse.ArgumentParser(description="VERITAS opportunity funnel")
    parser.add_argument("--start", type=int, default=None,
                        help="Route slice start index (default: 0)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max routes to quote from slice (default: all)")
    parser.add_argument("--checkpoint", type=str, default=None,
                        help="JSONL checkpoint file to append per-route economics")
    parser.add_argument("--budget-s", type=float, default=None,
                        help="Stop quoting after this many seconds (quotation phase only)")
    args = parser.parse_args()

    start_idx = args.start if args.start is not None else (int(env_start) if env_start else 0)
    limit = args.limit if args.limit is not None else (int(env_limit) if env_limit else None)
    checkpoint = args.checkpoint or env_checkpoint
    budget_s = args.budget_s if args.budget_s is not None else (float(env_budget) if env_budget else None)

    print("=" * 70)
    print("OPPORTUNITY FUNNEL ANALYSIS")
    print("=" * 70)
    
    rpc = AlchemyRPC()
    reg = PoolRegistry()
    
    # Create resilient RPC
        providers = [
            (os.getenv("ALCHEMY_ARBITRUM_URL", ""), 0),
            ("https://gateway.tenderly.co/public/arbitrum", 1),
            ("https://arbitrum.drpc.org", 2),
        ]
    resilient_rpc = ResilientRPC(providers, cache=RPCCache())
    
    # ---- Phase 1: Discovery ----
    print("\n[1] DISCOVERY")
    print("-" * 40)
    
    discovery = MarketDiscovery(rpc, reg, 42161, resilient_rpc)
    
    tokens = [
        "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",  # USDC.e
        "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  # WBTC
        "0x912CE59144191C1204E64559FE8253a0e49E6548",  # ARB
    ]
    
    full_path_valued = 0
    malformed_routes = 0

    start = time.time()
    discovered = discovery.discover_pools(
        tokens,
        venues=["uniswap_v3", "sushi_v3"],
        fee_tiers=[500, 3000],
    )
    discovery_time = time.time() - start
    
    print(f"  Pools discovered: {len(discovered)}")
    print(f"  Discovery time: {discovery_time:.1f}s")
    print(f"  Stats: {discovery.stats()}")
    
    # Register discovered pools
    for pool in discovered:
        reg.register(pool, persist=False)
    
    print(f"  Total pools in registry: {reg.count()}")
    
    # ---- Phase 2: Route Generation ----
    print("\n[2] ROUTE GENERATION")
    print("-" * 40)
    
    gen = RouteGenerator(reg, max_hops=3, max_routes_per_token=500)
    
    full_path_valued = 0
    malformed_routes = 0

    start = time.time()
    routes = gen.generate_all_routes(tokens, max_hops=3)
    route_time = time.time() - start
    
    print(f"  Routes generated: {len(routes)}")
    print(f"  Route generation time: {route_time:.1f}s")
    
    # Breakdown
    cross_venue = sum(1 for r in routes if r.is_cross_venue)
    same_venue = sum(1 for r in routes if not r.is_cross_venue)
    triangular = sum(1 for r in routes if r.is_triangular)
    
    print(f"  Cross-venue routes: {cross_venue}")
    print(f"  Same-venue routes: {same_venue}")
    print(f"  Triangular routes: {triangular}")
    
    # ---- Phase 3: Quotation ----
    print("\n[3] QUOTATION")
    print("-" * 40)
    
    engine = QuoteEngine(rpc, resilient_rpc)
    price_oracle = PriceOracle(rpc)
    funnel_economic_model = EconomicModel(price_oracle)

    quotes_attempted = 0
    quotes_successful = 0
    quotes_failed = 0
    gross_positive = 0
    net_positive = 0

    # Per-route economics collection for audit
    route_economics = []
    
    full_path_valued = 0
    malformed_routes = 0

    start = time.time()
    
    # Quote routes (default: all routes; slice via --start/--limit or
    # FUNNEL_START/FUNNEL_LIMIT env vars for batching)
    slice_start = start_idx
    if limit is not None:
        routes_to_quote = routes[slice_start:slice_start + limit]
    else:
        routes_to_quote = routes[slice_start:]
    if budget_s is not None:
        print(f"  Quotation time budget: {budget_s:.0f}s")
    print(f"  Quoting route slice: start={slice_start} count={len(routes_to_quote)} of {len(routes)}")
    
    for i, route in enumerate(routes_to_quote, start=slice_start):
        if budget_s is not None and (time.time() - start) > budget_s:
            print(f"  Quotation budget exhausted after {quotes_attempted} routes; stopping slice.")
            break
        if not route.steps:
            continue

        # --- Route-shape validation (canonical invariant) ---
        token_path = [s.token_in for s in route.steps] + ([route.steps[-1].token_out] if route.steps else [])
        venue_path = [s.venue for s in route.steps]
        shape_ok, shape_reason = validate_route_shape(route)
        if not shape_ok:
            malformed_routes += 1
            econ_entry = {
                "route_idx": i,
                "token_path": token_path,
                "venue_path": venue_path,
                "pool_addresses": [s.pool_address for s in route.steps],
                "hops": [],
                "amount_in": 10**16,
                "amount_out": 0,
                "gross_profit_usd": 0.0,
                "dex_fees_usd": 0.0,
                "flash_fee_usd": 0.0,
                "gas_cost_usd": 0.0,
                "slippage_cost_usd": 0.0,
                "total_costs_usd": 0.0,
                "net_profit_usd": 0.0,
                "roi_bps": 0.0,
                "is_gross_positive": False,
                "is_net_positive": False,
                "execution_gate": "FAIL",
                "rejection_reason": shape_reason,
            }
            route_economics.append(econ_entry)
            if checkpoint:
                with open(checkpoint, "a", encoding="utf-8") as ckpt:
                    ckpt.write(json.dumps(econ_entry) + "\n")
            continue

        # --- Full-path triangular quote: hop N out -> hop N+1 in ---
        quotes_attempted += 1
        amount_in = 10**16  # 0.01 WETH
        input_usd = amount_in / 1e18 * ETH_PRICE_USD

        try:
            final_out, hops, failure = quote_full_route(
                route, amount_in, engine, reg, resilient_rpc, CHAIN_ID
            )

            if failure is not None:
                quotes_failed += 1
                econ_entry = {
                    "route_idx": i,
                    "token_path": token_path,
                    "venue_path": venue_path,
                    "pool_addresses": [h["pool_address"] for h in hops] if hops else [s.pool_address for s in route.steps],
                    "hops": hops,
                    "amount_in": amount_in,
                    "amount_out": 0,
                    "gross_profit_usd": 0.0,
                    "dex_fees_usd": 0.0,
                    "flash_fee_usd": 0.0,
                    "gas_cost_usd": 0.0,
                    "slippage_cost_usd": 0.0,
                    "total_costs_usd": 0.0,
                    "net_profit_usd": 0.0,
                    "roi_bps": 0.0,
                    "is_gross_positive": False,
                    "is_net_positive": False,
                    "execution_gate": "FAIL",
                    "rejection_reason": failure,
                }
                route_economics.append(econ_entry)
                if checkpoint:
                    with open(checkpoint, "a", encoding="utf-8") as ckpt:
                        ckpt.write(json.dumps(econ_entry) + "\n")
                continue

            quotes_successful += 1
            full_path_valued += 1

            # final_out is denominated in the START token: the ONLY valid
            # round-trip comparison (A2 vs A0, never B1 vs A0).
            is_gross_positive = final_out > amount_in
            if is_gross_positive:
                gross_positive += 1

            pool_addrs = [h["pool_address"] for h in hops]

            # Canonical economics: same-asset round-trip mapped onto
            # EconomicModel.evaluate(). Quote outputs are post-fee, so no
            # separate DEX-fee subtraction (double-counting is forbidden).
            economic = evaluate_route_economics(
                amount_in_native=amount_in,
                input_usd=input_usd,
                final_out_native=final_out,
                hops=hops,
                economic_model=funnel_economic_model,
                gas_price_gwei=0.01,
                block_number=max(
                    (int(h.get("block_number", 0) or 0) for h in hops),
                    default=0,
                ),
            )
            gross_profit_usd = economic.gross_profit_usd
            dex_fees_usd = economic.dex_fees_usd
            flash_loan_fee_usd = economic.flash_loan_fee_usd
            estimated_gas_usd = economic.estimated_gas_usd
            estimated_slippage_usd = economic.estimated_slippage_usd
            total_costs_usd = economic.total_costs_usd
            net_profit_usd = economic.expected_net_profit_usd
            roi_bps = economic.roi_bps

            # Check if net positive.
            is_net_positive = economic.is_profitable
            if is_net_positive:
                net_positive += 1

            # Execution gate: profitable AND above min thresholds
            # (is_worth_executing), never bare is_profitable.
            execution_gate = "FAIL"
            rejection_reason = None
            if economic.is_worth_executing:
                execution_gate = "PASS"
            elif economic.is_profitable:
                reason = (economic.below_threshold_reason or "").lower()
                if "min_profit" in reason or "profit" in reason:
                    rejection_reason = (
                        f"below_min_profit: net=${net_profit_usd:.4f} "
                        f"< ${funnel_economic_model.min_profit_usd:.4f}"
                    )
                else:
                    rejection_reason = (
                        f"below_min_roi: {roi_bps:.2f}bps "
                        f"< {funnel_economic_model.min_roi_bps:.2f}bps"
                    )
            else:
                rejection_reason = f"not_profitable: net=${net_profit_usd:.4f}"

            # Record per-route economics.
            econ_entry = {
                "route_idx": i,
                "token_path": token_path,
                "venue_path": venue_path,
                "pool_addresses": pool_addrs,
                "pool_address": pool_addrs[0] if pool_addrs else None,
                "fee_tier": hops[0]["fee_tier"] if hops else None,
                "hops": hops,
                "amount_in": amount_in,
                "amount_out": final_out,
                "gross_profit_usd": gross_profit_usd,
                "dex_fees_usd": dex_fees_usd,
                "flash_fee_usd": flash_loan_fee_usd,
                "gas_cost_usd": estimated_gas_usd,
                "slippage_cost_usd": estimated_slippage_usd,
                "total_costs_usd": total_costs_usd,
                "net_profit_usd": net_profit_usd,
                "roi_bps": roi_bps,
                "is_gross_positive": is_gross_positive,
                "is_net_positive": is_net_positive,
                "execution_gate": execution_gate,
                "rejection_reason": rejection_reason,
            }
            route_economics.append(econ_entry)
            if checkpoint:
                with open(checkpoint, "a", encoding="utf-8") as ckpt:
                    ckpt.write(json.dumps(econ_entry) + "\n")
        except Exception as e:
            quotes_failed += 1
            econ_entry = {
                "route_idx": i,
                "token_path": [s.token_in for s in route.steps] + ([route.steps[-1].token_out] if route.steps else []),
                "venue_path": [s.venue for s in route.steps],
                "pool_addresses": [s.pool_address for s in route.steps],
                "hops": [],
                "amount_in": 10**16,
                "amount_out": 0,
                "gross_profit_usd": 0.0,
                "dex_fees_usd": 0.0,
                "flash_fee_usd": 0.0,
                "gas_cost_usd": 0.0,
                "slippage_cost_usd": 0.0,
                "total_costs_usd": 0.0,
                "net_profit_usd": 0.0,
                "roi_bps": 0.0,
                "is_gross_positive": False,
                "is_net_positive": False,
                "execution_gate": "FAIL",
                "rejection_reason": f"exception: {e}",
            }
            route_economics.append(econ_entry)
            if checkpoint:
                with open(checkpoint, "a", encoding="utf-8") as ckpt:
                    ckpt.write(json.dumps(econ_entry) + "\n")
    
    quote_time = time.time() - start
    
    print(f"  Routes quoted: {quotes_attempted}")
    print(f"  Quotes successful: {quotes_successful}")
    print(f"  Quotes failed: {quotes_failed}")
    print(f"  Quote time: {quote_time:.1f}s")
    print(f"  Avg quote latency: {(quote_time / quotes_attempted * 1000) if quotes_attempted > 0 else 0:.1f}ms")
    
    # ---- Summary ----
    print("\n" + "=" * 70)
    print("OPPORTUNITY FUNNEL SUMMARY")
    print("=" * 70)
    print(f"  Pools discovered:              {len(discovered)}")
    print(f"  Routes generated:              {len(routes)}")
    print(f"  Routes quoted:                 {quotes_attempted}")
    print(f"  Quotes successful:             {quotes_successful}")
    print(f"  Quote success rate:            {(quotes_successful / quotes_attempted * 100) if quotes_attempted > 0 else 0:.1f}%")
    print(f"  Gross-positive routes:         {gross_positive}")
    print(f"  Net-positive routes (after fees):           {net_positive}")
    print(f"  Full-path valued routes:       {full_path_valued}")
    print(f"  Malformed routes:              {malformed_routes}")
    print(f"  Total time:                    {discovery_time + route_time + quote_time:.1f}s")

    # ---- Per-route economics (audit) ----
    print("\n" + "=" * 70)
    print("PER-ROUTE ECONOMICS")
    print("=" * 70)
    for e in route_economics:
        hops_txt = ""
        if e.get("hops"):
            hops_txt = " hops=[" + ",".join(
                f"{h['hop']}:{h['venue']}:{h['pool_address'][:10]}:{h['amount_in']}->{h['amount_out']}(src={h.get('source', '?')})"
                for h in e["hops"]
            ) + "]"
        elif e.get("pool_addresses"):
            hops_txt = " pools=[" + ",".join(a[:10] for a in e["pool_addresses"]) + "]"
        print(f"  route={e['route_idx']} "
              f"fee_tier={e.get('fee_tier')} "
              f"in={e['amount_in']} out={e['amount_out']} "
              f"gross=${e['gross_profit_usd']:.4f} "
              f"dex=${e['dex_fees_usd']:.4f} "
              f"flash=${e['flash_fee_usd']:.4f} "
              f"gas=${e['gas_cost_usd']:.4f} "
              f"slip=${e['slippage_cost_usd']:.4f} "
              f"costs=${e['total_costs_usd']:.4f} "
              f"net=${e['net_profit_usd']:.4f} "
              f"roi={e['roi_bps']:.2f}bps "
              f"gate={e['execution_gate']} "
              f"{hops_txt} "
              f"{('reason=' + e['rejection_reason']) if e['rejection_reason'] else ''}")

    # ---- Best route ----
    net_survivors = [e for e in route_economics if e["is_net_positive"]]
    if net_survivors:
        best = max(net_survivors, key=lambda e: e["net_profit_usd"])
        print("\n" + "=" * 70)
        print("BEST ROUTE")
        print("=" * 70)
        print(f"  Route index:      {best['route_idx']}")
        print(f"  Token path:       {' -> '.join(best['token_path'])}")
        print(f"  Venue path:       {' -> '.join(best['venue_path'])}")
        for addr in best.get("pool_addresses", [best.get("pool_address")] if best.get("pool_address") else []):
            if addr:
                print(f"  Pool address:     {addr}")
        print(f"  Fee tier:         {best.get('fee_tier')}")
        for h in best.get("hops", []):
            print(f"  Hop {h['hop']}: {h['venue']} {h['pool_address']} "
                  f"{h['token_in'][:10]}->{h['token_out'][:10]} "
                  f"{h['amount_in']}->{h['amount_out']} (src={h.get('source', '?')} auth={h.get('authoritative', False)})")
        print(f"  Amount in:        {best['amount_in']} (${best['amount_in'] / 1e18 * 2500.0:.2f})")
        print(f"  Amount out:       {best['amount_out']} (${best['amount_out'] / 1e18 * 2500.0:.2f})")
        print(f"  Gross profit:     ${best['gross_profit_usd']:.4f}")
        print(f"  DEX fees:         ${best['dex_fees_usd']:.4f}")
        print(f"  Flash loan fee:   ${best['flash_fee_usd']:.4f}")
        print(f"  Gas cost:         ${best['gas_cost_usd']:.4f}")
        print(f"  Slippage cost:    ${best['slippage_cost_usd']:.4f}")
        print(f"  Total costs:      ${best['total_costs_usd']:.4f}")
        print(f"  Net profit:       ${best['net_profit_usd']:.4f}")
        print(f"  ROI:              {best['roi_bps']:.2f} bps")
        print(f"  Execution gate:   {best['execution_gate']}"
              f"{(' (' + best['rejection_reason'] + ')') if best['rejection_reason'] else ''}")
    else:
        print("\nBEST ROUTE: none (no net-positive routes)")

    # ---- ROI distribution ----
    print("\n" + "=" * 70)
    print("ROI DISTRIBUTION (net-positive routes)")
    print("=" * 70)
    if net_survivors:
        rois = sorted(e["roi_bps"] for e in net_survivors)
        n = len(rois)
        median = rois[n // 2] if n % 2 == 1 else (rois[n // 2 - 1] + rois[n // 2]) / 2
        above_5 = sum(1 for r in rois if r >= 5)
        above_50 = sum(1 for r in rois if r >= 50)
        above_100 = sum(1 for r in rois if r >= 100)
        gate_pass = sum(1 for e in net_survivors if e["execution_gate"] == "PASS")
        print(f"  Count:            {n}")
        print(f"  Min ROI:          {rois[0]:.2f} bps")
        print(f"  Median ROI:       {median:.2f} bps")
        print(f"  Max ROI:          {rois[-1]:.2f} bps")
        print(f"  Above 5 bps:      {above_5} routes (execution threshold)")
        print(f"  Above 50 bps:     {above_50} routes")
        print(f"  Above 100 bps:    {above_100} routes")
        print(f"  Gate PASS:        {gate_pass} routes (net>0 AND profit>=$0.01 AND roi>=5bps)")
    else:
        print("  Count:            0")

    # ---- Rejection reason histogram ----
    from collections import Counter
    reasons = Counter(
        e["rejection_reason"].split(":")[0]
        for e in route_economics
        if e["rejection_reason"]
    )
    print("\n" + "=" * 70)
    print("REJECTION REASON HISTOGRAM")
    print("=" * 70)
    if reasons:
        for reason, count in reasons.most_common():
            print(f"  {reason}: {count}")
    else:
        print("  (no rejections)")


if __name__ == "__main__":
    main()
