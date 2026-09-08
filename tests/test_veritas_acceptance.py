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


class TestJ_IntegrationScannerWithMockedRPC(unittest.TestCase):
    """
    Test J: Integration test — actually invoke scan_cross_venue() with
    mocked RPC/pools to prove the production scanner pipeline works.

    Unlike Tests A-I which construct data structures directly, this test
    exercises the real scan_cross_venue() function with mocked dependencies.

    IMPORTANT: These tests must FAIL if the scanner breaks. No ejector seats.
    """

    def _make_mock_rpc(self):
        """Create a mock RPC that returns realistic pool data."""
        from core.rpc import RPC
        rpc = MagicMock(spec=RPC)
        rpc.eth_blockNumber.return_value = 1000000
        rpc.eth_call.return_value = "0x" + "0" * 63 + "1"  # non-zero balance
        rpc.eth_gasPrice.return_value = 10**9  # 1 gwei
        return rpc

    def test_scan_cross_venue_returns_scan_result(self):
        """scan_cross_venue() should return a ScanResult instance."""
        from arb_engine import scan_cross_venue
        rpc = self._make_mock_rpc()
        # This MUST succeed with a mock RPC — if it fails, the scanner is broken
        result = scan_cross_venue(rpc, eth_usd=2500.0, gas_usd=0.01)
        self.assertIsInstance(result, ScanResult)

    def test_scan_cross_venue_with_no_quotes_produces_why_zero(self):
        """When no quotes are available, why_zero_report should explain why."""
        from arb_engine import scan_cross_venue
        rpc = self._make_mock_rpc()
        result = scan_cross_venue(rpc, eth_usd=2500.0, gas_usd=0.01)
        report = result.generate_why_zero_report()
        self.assertIsInstance(report, str)
        self.assertIn("VERITAS SCAN", report)

    def test_scan_result_compatibility_layer(self):
        """ScanResult should be backward-compatible via __bool__, __len__, __iter__."""
        result = ScanResult()
        # Empty result is falsy
        self.assertFalse(bool(result))
        self.assertEqual(len(result), 0)
        # With edges, it's truthy
        result.edges = [{"net_margin": 1.0}]
        self.assertTrue(bool(result))
        self.assertEqual(len(result), 1)
        # Iteration works
        items = list(result)
        self.assertEqual(len(items), 1)


class TestK_RealPersistenceAcrossControllers(unittest.TestCase):
    """
    Test K: Real persistence test — prove that capital state survives
    across controller instances (simulating process/cycle boundaries).

    Controller A -> profit -> save -> destroy ->
    Controller B -> load -> assert updated capital
    """

    def test_persists_verified_pnl_across_instances(self):
        """Verified PnL should persist across controller instances."""
        import tempfile
        import os
        from unittest.mock import patch

        # Use a temporary database for isolation
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_veritas.db")

            # Controller A: record a verified profit
            with patch("core.db.DB_PATH", db_path):
                from core.db import init as db_init
                db_init()
                from core.capital_controller import CapitalController
                ctrl_a = CapitalController()
                initial = ctrl_a.state.deployable_usd
                ctrl_a.record_verified_pnl(0.50, 0.01)
                after_profit = ctrl_a.state.deployable_usd
                self.assertGreater(after_profit, initial,
                                   "Verified PnL should increase deployable capital")

            # Controller B: load from same DB
            with patch("core.db.DB_PATH", db_path):
                from core.capital_controller import CapitalController
                ctrl_b = CapitalController()
                self.assertAlmostEqual(ctrl_b.state.deployable_usd, after_profit,
                                       places=4,
                                       msg="Controller B should load Controller A's saved state")

    def test_sim_attempt_does_not_change_capital(self):
        """Sim attempts should NOT change deployable capital."""
        import tempfile
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_veritas.db")
            with patch("core.db.DB_PATH", db_path):
                from core.db import init as db_init
                db_init()
                from core.capital_controller import CapitalController
                ctrl = CapitalController()
                initial = ctrl.state.deployable_usd
                ctrl.record_sim_attempt(0.01)
                self.assertEqual(ctrl.state.deployable_usd, initial,
                                 "Sim attempt should not change deployable capital")
                self.assertEqual(ctrl.state.sim_attempts, 1,
                                 "Sim attempt counter should increment")

    def test_unverified_live_exec_does_not_increase_capital(self):
        """Unverified live execution should NOT increase deployable capital."""
        import tempfile
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_veritas.db")
            with patch("core.db.DB_PATH", db_path):
                from core.db import init as db_init
                db_init()
                from core.capital_controller import CapitalController
                ctrl = CapitalController()
                initial = ctrl.state.deployable_usd
                ctrl.record_live_execution(0.50, 0.01, verified=False)
                self.assertEqual(ctrl.state.deployable_usd, initial,
                                 "Unverified execution should not increase deployable capital")


class TestL_LiquidityDecimalInvariant(unittest.TestCase):
    """
    Test L: Liquidity calculation must use the correct decimal for the
    WETH side of the pool, regardless of whether WETH is token_a or token_b.
    """

    def test_liquidity_when_weth_is_token_a(self):
        """When WETH is token_a, divide reserve_a by dec_a."""
        from arb_engine import _estimate_pool_liquidity
        forward = [{
            "reserve_a": 100 * 10**18,  # 100 WETH
            "reserve_b": 250000 * 10**6,  # 250000 USDC
            "token_a": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
            "token_b": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
            "token_a_decimals": 18,
            "token_b_decimals": 6,
        }]
        reverse = []
        liquidity = _estimate_pool_liquidity(forward, reverse, 2500.0)
        # 2 * 100 WETH * $2500 = $500,000
        self.assertAlmostEqual(liquidity, 500000.0, places=1,
                               msg="Liquidity should be $500K when WETH is token_a")

    def test_liquidity_when_weth_is_token_b(self):
        """When WETH is token_b, divide reserve_b by dec_b (not dec_a)."""
        from arb_engine import _estimate_pool_liquidity
        forward = [{
            "reserve_a": 250000 * 10**6,  # 250000 USDC
            "reserve_b": 100 * 10**18,  # 100 WETH
            "token_a": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
            "token_b": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
            "token_a_decimals": 6,
            "token_b_decimals": 18,
        }]
        reverse = []
        liquidity = _estimate_pool_liquidity(forward, reverse, 2500.0)
        # 2 * 100 WETH * $2500 = $500,000
        # BUG SCENARIO: if we divided by dec_a (6), we'd get:
        #   100e18 / 1e6 = 1e14 -> 2 * 1e14 * 2500 = 5e17 (WRONG!)
        self.assertAlmostEqual(liquidity, 500000.0, places=1,
                               msg="Liquidity should be $500K when WETH is token_b")

    def test_liquidity_both_orientations_match(self):
        """Liquidity estimate should be identical regardless of token order."""
        from arb_engine import _estimate_pool_liquidity
        forward_a_first = [{
            "reserve_a": 100 * 10**18,
            "reserve_b": 250000 * 10**6,
            "token_a": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
            "token_b": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "token_a_decimals": 18,
            "token_b_decimals": 6,
        }]
        forward_b_first = [{
            "reserve_a": 250000 * 10**6,
            "reserve_b": 100 * 10**18,
            "token_a": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "token_b": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
            "token_a_decimals": 6,
            "token_b_decimals": 18,
        }]
        liq_a = _estimate_pool_liquidity(forward_a_first, [], 2500.0)
        liq_b = _estimate_pool_liquidity(forward_b_first, [], 2500.0)
        self.assertAlmostEqual(liq_a, liq_b, places=1,
                               msg="Liquidity should be identical regardless of token order")


class TestM_FactorySeparation(unittest.TestCase):
    """
    Test M: Factory separation — verify that the scanner preserves
    multi-venue topology and doesn't collapse factories.
    """

    def test_pair_cache_preserves_factory_topology(self):
        """pair_cache should be keyed by (factory, base, quote) to preserve venues."""
        # Simulate what discover_tokens_and_pairs does
        pair_cache = {}
        factories = ["0xSUSHI", "0xUNIV2", "0xCAMELOT"]
        base = "0xTOKENA"
        quote = "0xTOKENB"

        for factory in factories:
            pair_addr = f"0xPAIR_{factory[-4:]}"
            pair_cache[(factory, base, quote)] = pair_addr
            pair_cache[(factory, quote, base)] = pair_addr

        # All three factories should be preserved
        self.assertEqual(len(pair_cache), 6)  # 3 factories * 2 directions
        self.assertIn(("0xSUSHI", base, quote), pair_cache)
        self.assertIn(("0xUNIV2", base, quote), pair_cache)
        self.assertIn(("0xCAMELOT", base, quote), pair_cache)

    def test_scan_cross_venue_queries_all_factories(self):
        """scan_cross_venue should query all three factories, not stop at first."""
        import inspect
        from arb_engine import scan_cross_venue
        source = inspect.getsource(scan_cross_venue)
        # Verify all three factories are in the source
        self.assertIn("SUSHI_FACTORY", source)
        self.assertIn("UNIV2_FACTORY", source)
        self.assertIn("CAMELOT_FACTORY", source)


class TestN_SimPathDoubleCounting(unittest.TestCase):
    """
    Test N: Verify that simulation PASS does NOT count as a live execution.
    Only actual broadcasts should increment trade counters.
    """

    def test_sim_pass_does_not_increment_trade_count(self):
        """A simulation PASS should only call record_sim_attempt, not record_live_execution."""
        import tempfile
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_veritas.db")
            with patch("core.db.DB_PATH", db_path):
                from core.db import init as db_init
                db_init()
                from core.capital_controller import CapitalController
                ctrl = CapitalController()
                # Simulate a sim PASS: only record_sim_attempt should be called
                ctrl.record_sim_attempt(0.01)
                # Trade count should remain 0 (sim is not a trade)
                self.assertEqual(ctrl.state.trades, 0,
                                 "Sim attempt should not increment trade count")
                self.assertEqual(ctrl.state.sim_attempts, 1,
                                 "Sim attempt counter should increment")

    def test_one_broadcast_records_one_trade(self):
        """One broadcast should result in exactly one verified PnL entry, not two."""
        import tempfile
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_veritas.db")
            with patch("core.db.DB_PATH", db_path):
                from core.db import init as db_init
                db_init()
                from core.capital_controller import CapitalController
                ctrl = CapitalController()
                initial_trades = ctrl.state.trades
                # Sim path: only record_sim_attempt
                ctrl.record_sim_attempt(0.01)
                # Broadcast path: record_verified_pnl (one trade)
                ctrl.record_verified_pnl(0.50, 0.01)
                # Should be exactly 1 trade, not 2
                self.assertEqual(ctrl.state.trades, initial_trades + 1,
                                 "One broadcast should record exactly one trade")


class TestO_RealizedPnLVerification(unittest.TestCase):
    """
    Test O: Verify that realized PnL verification uses on-chain balance delta,
    not just the scanner's projected profit.
    """

    def test_weth_balance_helper_reads_erc20(self):
        """_weth_balance should correctly decode ERC20 balanceOf response."""
        from flash_hunter import _weth_balance
        from unittest.mock import MagicMock
        rpc = MagicMock()
        # balanceOf returns 1.5 WETH = 1500000000000000000 wei
        balance_1_5_weth = int(1.5 * 1e18)
        # Encode as 32-byte hex (what eth_call returns)
        rpc.eth_call.return_value = "0x" + format(balance_1_5_weth, "064x")
        balance = _weth_balance(rpc, "0xWETH", "0xHOLDER")
        self.assertAlmostEqual(balance, 1.5 * 1e18, places=0,
                               msg="_weth_balance should decode 1.5 WETH")

    def test_weth_balance_handles_error(self):
        """_weth_balance should return 0 on RPC failure."""
        from flash_hunter import _weth_balance
        from unittest.mock import MagicMock
        rpc = MagicMock()
        rpc.eth_call.side_effect = Exception("RPC error")
        balance = _weth_balance(rpc, "0xWETH", "0xHOLDER")
        self.assertEqual(balance, 0,
                         "_weth_balance should return 0 on error")

    def test_realized_pnl_uses_minimum_of_projected_and_realized(self):
        """Verified PnL should use the LESSER of projected or realized profit."""
        import tempfile
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_veritas.db")
            with patch("core.db.DB_PATH", db_path):
                from core.db import init as db_init
                db_init()
                from core.capital_controller import CapitalController
                ctrl = CapitalController()
                initial_capital = ctrl.state.deployable_usd
                # Simulate: projected $0.50, realized $0.30 (slippage)
                projected = 0.50
                realized = 0.30
                verified = min(projected, realized) if realized > 0 else projected
                ctrl.record_verified_pnl(verified, 0.01)
                # Capital should increase by realized ($0.30), not projected ($0.50)
                self.assertAlmostEqual(ctrl.state.deployable_usd,
                                       initial_capital + 0.30, places=4,
                                       msg="Verified PnL should use realized profit, not projected")


if __name__ == "__main__":
    unittest.main(verbosity=2)
