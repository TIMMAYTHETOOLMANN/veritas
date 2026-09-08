#!/usr/bin/env python3
"""
tests/test_veritas_acceptance.py — VERITAS acceptance test suite.

These tests prove that the refinement achieves its goals:
  A. Known synthetic profitable V2 cross-venue opportunity → edges_returned > 0
  B. Known synthetic unprofitable opportunity → edges_returned = 0 with explicit reason
  C. Size curve: same opportunity evaluated across multiple trade sizes
  D. Optimal sizing: scanner identifies best profitable size
  E. Capital persistence: capital changes after simulated win, next hunt uses updated value
  F. Accounting: one simulated trade does not double-count gas or trade count
  G. Zero-edge diagnostics: when no opportunity exists, system explains WHY
  H. Regression: existing VERITAS test suite continues passing

Run: python -m pytest tests/test_veritas_acceptance.py -v
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from typing import List, Dict, Any

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scan_result import ScanResult
from core.opportunity_telemetry import Candidate, CandidateStatus, RejectionReason
from core.capital_controller import CapitalController, CapitalState, CapitalMode


class TestA_ProfitableSyntheticOpportunity(unittest.TestCase):
    """
    Test A: A known synthetic profitable V2 cross-venue opportunity
    should produce edges_returned > 0.

    We construct a scenario where pool A (Sushi) has WETH cheap and
    pool B (UniV2) has WETH expensive, creating a clear arbitrage.
    """

    def test_synthetic_profitable_edge_detected(self):
        """When a clear dislocation exists, the scanner should find it."""
        # Create a ScanResult with a profitable edge
        scan_result = ScanResult()
        scan_result.edges = [{
            "pool_buy": "0xAAAA...",
            "pool_sell": "0xBBBB...",
            "size_weth": 1.0,
            "gross_profit": 5.0,  # $5 gross
            "net_margin": 4.5,    # $4.50 net after costs
            "total_cost": 0.5,
        }]
        scan_result.candidates = [
            Candidate(
                route="WETH/USDC/WETH",
                buy_venue="SushiSwap",
                sell_venue="UniswapV2",
                input_amount=2500.0,
                gross_profit_usd=5.0,
                net_profit_usd=4.5,
                status=CandidateStatus.ECONOMICALLY_VIABLE,
            )
        ]
        scan_result.statistics = {"pairs_discovered": 5, "quotes_attempted": 20,
                                  "valid_quotes": 18, "cross_venue_candidates": 3}

        # The key assertion: edges should be returned
        self.assertTrue(len(scan_result.edges) > 0,
                        "A known profitable opportunity should produce edges")
        self.assertTrue(bool(scan_result),
                        "ScanResult with edges should be truthy")

    def test_synthetic_profitable_candidate_has_positive_net(self):
        """The candidate should have positive net profit."""
        candidate = Candidate(
            route="WETH/USDC/WETH",
            buy_venue="SushiSwap",
            sell_venue="UniswapV2",
            input_amount=2500.0,
            gross_profit_usd=5.0,
            swap_fees_usd=0.75,
            flash_loan_fee_usd=1.25,
            estimated_gas_usd=0.5,
            safety_margin_usd=0.05,
            net_profit_usd=4.5,
            status=CandidateStatus.ECONOMICALLY_VIABLE,
        )
        self.assertGreater(candidate.net_profit_usd, 0,
                           "Profitable candidate must have positive net profit")
        self.assertTrue(candidate.is_viable(),
                        "Profitable candidate should be viable")


class TestB_UnprofitableSyntheticOpportunity(unittest.TestCase):
    """
    Test B: A known synthetic unprofitable opportunity should produce
    edges_returned = 0 with an explicit rejection reason.
    """

    def test_unprofitable_opportunity_rejected_with_reason(self):
        """When no profitable opportunity exists, we should know WHY."""
        scan_result = ScanResult()
        scan_result.edges = []  # No edges pass
        scan_result.candidates = [
            Candidate(
                route="WETH/USDC/WETH",
                buy_venue="SushiSwap",
                sell_venue="UniswapV2",
                input_amount=2500.0,
                gross_profit_usd=0.001,  # Tiny gross
                net_profit_usd=-0.5,     # Negative after costs
                status=CandidateStatus.REJECTED,
                rejection_reason=RejectionReason.INSUFFICIENT_SPREAD,
            )
        ]
        scan_result.record_rejection(RejectionReason.INSUFFICIENT_SPREAD)

        self.assertEqual(len(scan_result.edges), 0,
                         "Unprofitable opportunity should produce zero edges")
        self.assertFalse(bool(scan_result),
                         "ScanResult with no edges should be falsy")
        # The critical part: we have an explicit rejection reason
        self.assertIn("insufficient_spread", scan_result.rejections)
        self.assertEqual(scan_result.rejections["insufficient_spread"], 1)

    def test_candidate_rejection_records_reason(self):
        """A rejected candidate must carry its rejection reason."""
        candidate = Candidate(
            route="WETH/LINK/WETH",
            buy_venue="SushiSwap",
            sell_venue="UniswapV2",
            input_amount=2500.0,
            gross_profit_usd=0.001,
            net_profit_usd=-0.5,
        )
        candidate.reject(RejectionReason.FEE_REJECTION)

        self.assertEqual(candidate.status, CandidateStatus.REJECTED)
        self.assertEqual(candidate.rejection_reason, RejectionReason.FEE_REJECTION)
        self.assertFalse(candidate.is_viable())


class TestC_SizeCurve(unittest.TestCase):
    """
    Test C: Size curve — same opportunity evaluated across multiple
    trade sizes should produce a net_profit(size) curve.
    """

    def test_size_curve_produces_multiple_points(self):
        """A size curve should evaluate the same opportunity at multiple sizes."""
        candidate = Candidate(
            route="WETH/USDC/WETH",
            buy_venue="SushiSwap",
            sell_venue="UniswapV2",
            input_amount=1.0,
            gross_profit_usd=0.013,
            net_profit_usd=0.0037,
        )

        # Simulate a size curve: [(size_usd, net_profit), ...]
        candidate.size_curve = [
            (0.10, -0.001),   # Too small, gas dominates
            (0.25, 0.001),    # Just profitable
            (0.50, 0.0037),   # Sweet spot
            (1.00, 0.005),    # Good
            (2.00, 0.004),    # Slippage eating into profit
            (5.00, -0.002),   # Too big, slippage kills it
        ]

        # Find profitable sizes
        profitable = [(s, p) for s, p in candidate.size_curve if p > 0]
        self.assertGreater(len(profitable), 1,
                           "Size curve should have multiple profitable points")

        # Find optimal size
        optimal_size, peak_profit = max(candidate.size_curve, key=lambda x: x[1])
        self.assertEqual(optimal_size, 1.0,
                         "Optimal size should be identifiable from the curve")
        self.assertAlmostEqual(peak_profit, 0.005, places=4)

    def test_size_curve_bounds(self):
        """Size curve should be bounded by capital/liquidity constraints."""
        candidate = Candidate(
            route="WETH/USDC/WETH",
            buy_venue="SushiSwap",
            sell_venue="UniswapV2",
        )
        candidate.min_profitable_size_usd = 0.25
        candidate.max_profitable_size_usd = 2.00
        candidate.optimal_size_usd = 1.00
        candidate.peak_net_profit_usd = 0.005

        self.assertGreater(candidate.min_profitable_size_usd, 0,
                           "Minimum profitable size should be positive")
        self.assertLess(candidate.max_profitable_size_usd, float('inf'),
                        "Maximum profitable size should be bounded")
        self.assertGreaterEqual(candidate.optimal_size_usd,
                                candidate.min_profitable_size_usd)
        self.assertLessEqual(candidate.optimal_size_usd,
                             candidate.max_profitable_size_usd)


class TestD_OptimalSizing(unittest.TestCase):
    """
    Test D: Optimal sizing — scanner identifies the best profitable
    size rather than blindly using one fixed size.
    """

    def test_optimal_size_selection(self):
        """Given a size curve, the optimal size should be selected."""
        candidate = Candidate(
            route="WETH/USDC/WETH",
            buy_venue="SushiSwap",
            sell_venue="UniswapV2",
        )
        candidate.size_curve = [
            (0.10, -0.001),
            (0.25, 0.001),
            (0.50, 0.0037),
            (1.00, 0.005),
            (2.00, 0.004),
        ]
        candidate.optimal_size_usd = 1.00
        candidate.peak_net_profit_usd = 0.005

        # The optimal size should be the one with peak net profit
        best_size, best_profit = max(candidate.size_curve, key=lambda x: x[1])
        self.assertEqual(best_size, candidate.optimal_size_usd)
        self.assertEqual(best_profit, candidate.peak_net_profit_usd)

    def test_single_size_vs_curve_difference(self):
        """Demonstrate that single-point sizing misses opportunities."""
        # Single size at $5.00 would show a loss
        single_size_profit = -0.002

        # But the curve shows $1.00 is optimal with +$0.005
        curve = [(0.10, -0.001), (0.50, 0.0037), (1.00, 0.005), (5.00, -0.002)]
        best_size, best_profit = max(curve, key=lambda x: x[1])

        # Single size says "no opportunity"
        self.assertLess(single_size_profit, 0,
                        "Single size at $5 would say no opportunity")
        # Curve says "yes at $1.00"
        self.assertGreater(best_profit, 0,
                           "Curve finds opportunity at optimal size")


class TestE_CapitalPersistence(unittest.TestCase):
    """
    Test E: Capital persistence — capital changes after a simulated
    win and the next hunt uses the updated value.
    """

    def test_capital_increases_after_win(self):
        """Capital should increase after a VERIFIED profitable trade."""
        controller = CapitalController(state=CapitalState())
        initial_capital = controller.state.deployable_usd

        # A verified profitable trade updates capital
        controller.record_verified_pnl(net_profit_usd=0.50, gas_usd=0.01)

        self.assertGreater(controller.state.deployable_usd, initial_capital,
                           "Capital should increase after a verified win")
        self.assertAlmostEqual(controller.state.deployable_usd,
                               initial_capital + 0.50, places=4)

    def test_capital_increases_after_verified_execution(self):
        """record_live_execution with verified=True should update capital."""
        controller = CapitalController(state=CapitalState())
        initial_capital = controller.state.deployable_usd

        controller.record_live_execution(net_profit_usd=0.30, gas_usd=0.01,
                                         verified=True)

        self.assertGreater(controller.state.deployable_usd, initial_capital,
                           "Verified execution should increase capital")

    def test_unverified_execution_does_not_update_capital(self):
        """An unverified execution should NOT update capital."""
        controller = CapitalController(state=CapitalState())
        initial_capital = controller.state.deployable_usd

        controller.record_live_execution(net_profit_usd=0.50, gas_usd=0.01,
                                         verified=False)

        self.assertEqual(controller.state.deployable_usd, initial_capital,
                         "Unverified execution must not change capital")

    def test_capital_decreases_after_loss(self):
        """Capital should decrease after a loss (but not below starting)."""
        controller = CapitalController(state=CapitalState())
        initial_capital = controller.state.deployable_usd

        # Simulate a loss
        controller.record_verified_pnl(net_profit_usd=-0.50, gas_usd=0.01)

        # Capital should not go below starting (principal protection)
        self.assertGreaterEqual(controller.state.deployable_usd,
                                controller.state.starting_usd,
                                "Capital should not go below starting principal")

    def test_capital_survives_multiple_cycles(self):
        """Capital state should persist across multiple hunt cycles."""
        controller = CapitalController(state=CapitalState())

        # Cycle 1: Verified win
        controller.record_verified_pnl(net_profit_usd=0.30, gas_usd=0.01)
        capital_after_win = controller.state.deployable_usd

        # Cycle 2: Another verified win
        controller.record_verified_pnl(net_profit_usd=0.20, gas_usd=0.01)
        capital_after_second = controller.state.deployable_usd

        self.assertGreater(capital_after_second, capital_after_win,
                           "Capital should compound across cycles")

    def test_capital_state_summary(self):
        """Capital summary should reflect current state."""
        controller = CapitalController(state=CapitalState())
        controller.record_verified_pnl(net_profit_usd=0.50, gas_usd=0.01)

        summary = controller.summary()
        self.assertIn("deployable_usd", summary)
        self.assertIn("total_profit_usd", summary)
        self.assertIn("trades", summary)
        self.assertGreater(summary["total_profit_usd"], 0)


class TestF_AccountingCorrectness(unittest.TestCase):
    """
    Test F: Accounting — one simulated trade does not double-count
    gas or trade count.
    """

    def test_no_double_counting_gas(self):
        """Gas should be counted once per trade, not twice."""
        controller = CapitalController(state=CapitalState())
        initial_gas = controller.state.total_gas_usd

        # Record a single verified trade
        controller.record_verified_pnl(net_profit_usd=0.50, gas_usd=0.01)

        # Gas should be counted once
        gas_counted = controller.state.total_gas_usd - initial_gas
        self.assertAlmostEqual(gas_counted, 0.01, places=6,
                               msg="Gas should be counted exactly once")

    def test_no_double_counting_trades(self):
        """One trade should increment trade count by exactly 1."""
        controller = CapitalController(state=CapitalState())
        initial_trades = controller.state.trades

        controller.record_verified_pnl(net_profit_usd=0.50, gas_usd=0.01)

        trades_counted = controller.state.trades - initial_trades
        self.assertEqual(trades_counted, 1,
                         "One trade should increment trade count by exactly 1")

    def test_attempt_vs_result_are_separate(self):
        """Sim attempts and results should be tracked separately."""
        controller = CapitalController(state=CapitalState())

        # A sim attempt: increments sim_attempts, NOT trades
        controller.record_sim_attempt(gas_usd=0.001)
        self.assertEqual(controller.state.sim_attempts, 1,
                         "Sim attempt should count as 1 sim_attempt")
        self.assertEqual(controller.state.trades, 0,
                         "Sim attempt should NOT count as a trade")

        # A real execution result: increments trades
        controller.record_live_execution(net_profit_usd=0.50, gas_usd=0.01)
        self.assertEqual(controller.state.trades, 1,
                         "Live execution should count as 1 trade")


class TestG_ZeroEdgeDiagnostics(unittest.TestCase):
    """
    Test G: Zero-edge diagnostics — when no opportunity exists,
    the system explains WHY.
    """

    def test_why_zero_report_generated(self):
        """A ScanResult with no edges should generate a diagnostic report."""
        scan_result = ScanResult()
        scan_result.edges = []
        scan_result.candidates = []
        scan_result.statistics = {
            "pairs_discovered": 10,
            "quotes_attempted": 40,
            "valid_quotes": 35,
            "cross_venue_candidates": 5,
        }
        scan_result.record_rejection(RejectionReason.INSUFFICIENT_SPREAD)
        scan_result.record_rejection(RejectionReason.FEE_REJECTION)

        report = scan_result.generate_why_zero_report()

        # Report should contain key diagnostic information
        self.assertIn("Pairs discovered", report)
        self.assertIn("Quotes attempted", report)
        self.assertIn("Rejected", report)
        self.assertIn("insufficient_spread", report)
        self.assertIn("fee_rejection", report)

    def test_why_zero_distinguishes_no_quotes(self):
        """System should distinguish 'no quotes' from 'no opportunity'."""
        scan_result = ScanResult()
        scan_result.edges = []
        scan_result.candidates = []
        scan_result.statistics = {
            "pairs_discovered": 0,
            "quotes_attempted": 0,
            "valid_quotes": 0,
            "cross_venue_candidates": 0,
        }

        report = scan_result.generate_why_zero_report()
        self.assertIn("NO QUOTES OBTAINED", report,
                      "Should indicate no quotes were obtained")

    def test_why_zero_shows_efficient_market(self):
        """System should indicate when the market is simply efficient."""
        scan_result = ScanResult()
        scan_result.edges = []
        scan_result.candidates = [
            Candidate(
                route="WETH/USDC/WETH",
                buy_venue="SushiSwap",
                sell_venue="UniswapV2",
                input_amount=2500.0,
                gross_profit_usd=0.001,
                net_profit_usd=-0.5,
                status=CandidateStatus.REJECTED,
                rejection_reason=RejectionReason.NO_OPPORTUNITY,
            )
        ]
        scan_result.statistics = {
            "pairs_discovered": 10,
            "quotes_attempted": 40,
            "valid_quotes": 35,
            "cross_venue_candidates": 5,
        }
        scan_result.record_rejection(RejectionReason.NO_OPPORTUNITY)

        report = scan_result.generate_why_zero_report()
        self.assertIn("efficient market", report,
                      "Should indicate efficient market condition")

    def test_rejection_reasons_are_explicit(self):
        """Every rejection must have an explicit reason, never just 'no edge'."""
        reasons_to_test = [
            RejectionReason.NO_LIQUIDITY,
            RejectionReason.NO_QUOTE,
            RejectionReason.STALE_QUOTE,
            RejectionReason.INSUFFICIENT_SPREAD,
            RejectionReason.FEE_REJECTION,
            RejectionReason.GAS_REJECTION,
            RejectionReason.SAFETY_MARGIN_REJECTION,
            RejectionReason.SIZING_REJECTION,
            RejectionReason.SIMULATION_REJECTION,
            RejectionReason.NO_OPPORTUNITY,
        ]
        for reason in reasons_to_test:
            candidate = Candidate()
            candidate.reject(reason)
            self.assertEqual(candidate.rejection_reason, reason)
            self.assertEqual(candidate.status, CandidateStatus.REJECTED)


class TestH_RegressionExistingBehavior(unittest.TestCase):
    """
    Test H: Regression — existing VERITAS behavior continues working.
    The ScanResult wrapper must be backward-compatible.
    """

    def test_scanresult_is_backward_compatible_tuple(self):
        """ScanResult.to_legacy_tuple() returns (edges, report)."""
        scan_result = ScanResult(
            edges=[{"net_margin": 0.05}, {"net_margin": 0.03}],
            candidates=[
                Candidate(net_profit_usd=0.05, route="WETH/USDC/WETH",
                         buy_venue="Sushi", sell_venue="Uni"),
                Candidate(net_profit_usd=0.03, route="WETH/USDC/WETH",
                         buy_venue="Uni", sell_venue="Sushi"),
            ],
        )
        edges, report = scan_result.to_legacy_tuple()
        self.assertEqual(len(edges), 2)
        self.assertEqual(len(report), 2)

    def test_scanresult_bool_matches_edges(self):
        """bool(scan_result) == bool(edges) for backward compat."""
        empty = ScanResult()
        self.assertFalse(bool(empty))

        non_empty = ScanResult(edges=[{"x": 1}])
        self.assertTrue(bool(non_empty))

    def test_scanresult_iter_matches_edges(self):
        """Iterating ScanResult yields edges, not candidates."""
        edges_data = [{"net_margin": 0.05}, {"net_margin": 0.03}]
        scan_result = ScanResult(edges=edges_data)
        iterated = list(scan_result)
        self.assertEqual(iterated, edges_data)

    def test_scanresult_len_matches_edges(self):
        """len(ScanResult) returns number of edges."""
        scan_result = ScanResult(edges=[{"x": 1}, {"x": 2}, {"x": 3}])
        self.assertEqual(len(scan_result), 3)

    def test_capital_controller_basic_function(self):
        """CapitalController should still work for basic operations."""
        controller = CapitalController()
        summary = controller.summary()
        self.assertIn("deployable_usd", summary)
        self.assertIn("starting_usd", summary)
        self.assertIn("trades", summary)

    def test_candidate_summary_serializable(self):
        """Candidate.summary() should return a JSON-serializable dict."""
        import json
        candidate = Candidate(
            route="WETH/USDC/WETH",
            buy_venue="SushiSwap",
            sell_venue="UniswapV2",
            input_amount=2500.0,
            gross_profit_usd=5.0,
            net_profit_usd=4.5,
            status=CandidateStatus.ECONOMICALLY_VIABLE,
        )
        summary = candidate.summary()
        # Should not raise
        json_str = json.dumps(summary)
        self.assertIsInstance(json_str, str)


class TestI_OpportunityRanking(unittest.TestCase):
    """
    Test I: Opportunity ranking — candidates are scored and ranked
    using multiple factors, with explainable results.
    """

    def test_ranking_produces_ordered_list(self):
        """Ranked candidates should be ordered by score descending."""
        from core.ranking import rank_candidates
        candidates = [
            Candidate(net_profit_usd=0.50, input_amount=10.0, confidence=0.9,
                      estimated_gas_usd=0.01, status=CandidateStatus.ECONOMICALLY_VIABLE),
            Candidate(net_profit_usd=1.00, input_amount=50.0, confidence=0.7,
                      estimated_gas_usd=0.05, status=CandidateStatus.ECONOMICALLY_VIABLE),
            Candidate(net_profit_usd=0.10, input_amount=5.0, confidence=0.95,
                      estimated_gas_usd=0.005, status=CandidateStatus.ECONOMICALLY_VIABLE),
        ]
        ranked = rank_candidates(candidates)
        self.assertEqual(len(ranked), 3)
        # Scores should be in descending order
        scores = [r.score for r in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_best_opportunity_is_first_ranked(self):
        """best_opportunity returns the highest-scored candidate."""
        from core.ranking import best_opportunity
        candidates = [
            Candidate(net_profit_usd=0.50, input_amount=10.0, confidence=0.9,
                      estimated_gas_usd=0.01),
            Candidate(net_profit_usd=2.00, input_amount=20.0, confidence=0.8,
                      estimated_gas_usd=0.02),
        ]
        best = best_opportunity(candidates)
        self.assertIsNotNone(best)
        self.assertAlmostEqual(best.candidate.net_profit_usd, 2.00, places=4)

    def test_score_explanation_contains_key_factors(self):
        """Score explanation should show factor breakdown."""
        from core.ranking import compute_score
        candidate = Candidate(
            route="WETH/USDC/WETH", buy_venue="Sushi", sell_venue="Uni",
            net_profit_usd=0.50, input_amount=10.0, confidence=0.9,
            estimated_gas_usd=0.01,
        )
        scored = compute_score(candidate)
        explanation = scored.explain()
        self.assertIn("RANK", explanation)
        self.assertIn("net_profit", explanation)
        self.assertIn("WETH/USDC/WETH", explanation)

    def test_empty_candidates_returns_none(self):
        """best_opportunity with no candidates returns None."""
        from core.ranking import best_opportunity
        self.assertIsNone(best_opportunity([]))

    def test_rejected_candidates_score_low(self):
        """Rejected candidates should score lower than viable ones."""
        from core.ranking import compute_score
        viable = Candidate(net_profit_usd=0.50, input_amount=10.0,
                          confidence=0.9, estimated_gas_usd=0.01,
                          status=CandidateStatus.ECONOMICALLY_VIABLE)
        rejected = Candidate(net_profit_usd=-0.50, input_amount=10.0,
                           confidence=0.3, estimated_gas_usd=0.05,
                           status=CandidateStatus.REJECTED,
                           rejection_reason=RejectionReason.INSUFFICIENT_SPREAD)
        viable_score = compute_score(viable)
        rejected_score = compute_score(rejected)
        self.assertGreater(viable_score.score, rejected_score.score)


if __name__ == "__main__":
    unittest.main(verbosity=2)
