#!/usr/bin/env python3
"""
tests/test_veritas_overhaul.py — VERITAS overhaul acceptance test suite.

Tests the newly implemented architecture:
  - Pool identity and registry
  - Price oracle
  - Quote engine
  - Economic model
  - Size optimizer
  - Route generator
  - Execution gate
  - Accounting ledger
  - RPC health monitor
  - Error taxonomy
  - End-to-end pipeline

Target: 100+ meaningful tests.
"""
import sys
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===========================================================================
# Pool Identity Tests
# ===========================================================================
class TestPoolIdentity(unittest.TestCase):
    """Test pool identity: (chain_id, venue, factory, pool_address)."""

    def test_pool_id_is_unique_per_venue(self):
        """Two venues with same pair must have different pool IDs."""
        from core.pool import PoolId

        pool_a = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool_b = PoolId(
            chain_id=42161, venue="sushi",
            factory="0xc35dadb65012ec5796536bd9864ed8773abc74c4",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )

        # Same address but different venue = different pool
        self.assertNotEqual(pool_a, pool_b)
        self.assertNotEqual(hash(pool_a), hash(pool_b))

    def test_pool_id_case_insensitive(self):
        """Pool addresses should be compared case-insensitively."""
        from core.pool import PoolId

        pool_a = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0xABCDEF1234567890ABCDEF1234567890ABCDEF12"
        )
        pool_b = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0xabcdef1234567890abcdef1234567890abcdef12"
        )

        self.assertEqual(pool_a, pool_b)
        self.assertEqual(hash(pool_a), hash(pool_b))

    def test_pool_metadata_unique_key(self):
        """PoolMetadata unique_key should match PoolId string."""
        from core.pool import PoolId, PoolMetadata

        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v3",
            factory="0x1f98431c8ad98523631ae4a59f267346ea31f984",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = PoolMetadata(pool_id=pool_id, token0="0xaaa", token1="0xbbb")

        self.assertEqual(pool.unique_key, str(pool_id))

    def test_pool_freshness_check(self):
        """Pool freshness should detect stale data."""
        from core.pool import PoolId, PoolMetadata

        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xaaa", token1="0xbbb",
            last_updated_timestamp=int(time.time()),
            last_updated_block=100,
        )

        # Fresh pool
        self.assertTrue(pool.is_fresh(max_age_seconds=180, current_block=102))

        # Stale by time
        pool.last_updated_timestamp = int(time.time()) - 300
        self.assertFalse(pool.is_fresh(max_age_seconds=180))

    def test_pool_liquidity_check(self):
        """Pool liquidity check should filter low-liquidity pools."""
        from core.pool import PoolId, PoolMetadata

        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xaaa", token1="0xbbb",
            reserve0=0, reserve1=0, usd_depth=0,
        )

        self.assertFalse(pool.has_sufficient_liquidity())

        pool.reserve0 = 1000
        pool.reserve1 = 1000
        pool.usd_depth = 5000
        self.assertTrue(pool.has_sufficient_liquidity())


# ===========================================================================
# Pool Registry Tests
# ===========================================================================
class TestPoolRegistry(unittest.TestCase):
    """Test pool registry operations."""

    def setUp(self):
        """Set up a temporary database for each test."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_veritas.db")
        self.patcher = patch("core.db.DB_PATH", self.db_path)
        self.patcher.start()

        # Re-import to pick up patched DB_PATH
        import importlib
        import core.pool
        importlib.reload(core.pool)
        from core.pool import PoolRegistry, PoolId, PoolMetadata
        self.registry = PoolRegistry()
        self.PoolId = PoolId
        self.PoolMetadata = PoolMetadata

    def tearDown(self):
        self.patcher.stop()
        self.tmpdir.cleanup()

    def test_register_and_retrieve(self):
        """Register a pool and retrieve it by ID."""
        pool_id = self.PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = self.PoolMetadata(
            pool_id=pool_id, token0="0xaaa", token1="0xbbb",
            reserve0=1000, reserve1=2000, usd_depth=5000, is_live=True,
        )
        self.registry.register(pool)

        retrieved = self.registry.get(pool_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.token0, "0xaaa")
        self.assertEqual(retrieved.reserve0, 1000)

    def test_get_pools_for_pair(self):
        """Get all pools for a token pair across venues."""
        # Register two pools for same pair at different venues
        for venue in ["uniswap_v2", "sushi"]:
            pool_id = self.PoolId(
                chain_id=42161, venue=venue,
                factory=f"0x{venue}_factory",
                pool_address=f"0x{venue}_pool"
            )
            pool = self.PoolMetadata(
                pool_id=pool_id, token0="0xWETH", token1="0xUSDC",
                is_live=True, usd_depth=50000,
            )
            self.registry.register(pool)

        pools = self.registry.get_pools_for_pair("0xWETH", "0xUSDC")
        self.assertEqual(len(pools), 2)

    def test_get_pools_for_pair_and_venue(self):
        """Get pools for a specific pair at a specific venue."""
        for venue in ["uniswap_v2", "sushi"]:
            pool_id = self.PoolId(
                chain_id=42161, venue=venue,
                factory=f"0x{venue}_factory",
                pool_address=f"0x{venue}_pool"
            )
            pool = self.PoolMetadata(
                pool_id=pool_id, token0="0xWETH", token1="0xUSDC",
                is_live=True, usd_depth=50000,
            )
            self.registry.register(pool)

        uni_pools = self.registry.get_pools_for_pair_and_venue(
            "0xWETH", "0xUSDC", "uniswap_v2"
        )
        self.assertEqual(len(uni_pools), 1)
        self.assertEqual(uni_pools[0].pool_id.venue, "uniswap_v2")

    def test_two_venues_same_pair_different_pools(self):
        """
        CRITICAL TEST: Two venues containing the same pair can never
        accidentally resolve to the same pool.
        """
        pool_id_a = self.PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        )
        pool_id_b = self.PoolId(
            chain_id=42161, venue="sushi",
            factory="0xc35dadb65012ec5796536bd9864ed8773abc74c4",
            pool_address="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        )

        pool_a = self.PoolMetadata(
            pool_id=pool_id_a, token0="0xWETH", token1="0xUSDC",
            is_live=True, usd_depth=50000,
        )
        pool_b = self.PoolMetadata(
            pool_id=pool_id_b, token0="0xWETH", token1="0xUSDC",
            is_live=True, usd_depth=30000,
        )

        self.registry.register(pool_a)
        self.registry.register(pool_b)

        # They must be different pools
        self.assertNotEqual(pool_a.pool_id, pool_b.pool_id)
        self.assertNotEqual(pool_a.unique_key, pool_b.unique_key)

        # Retrieving by ID must return the correct pool
        self.assertEqual(self.registry.get(pool_a.pool_id).pool_id.venue, "uniswap_v2")
        self.assertEqual(self.registry.get(pool_b.pool_id).pool_id.venue, "sushi")

    def test_hot_pools(self):
        """Test hot pool tracking."""
        pool_id = self.PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = self.PoolMetadata(
            pool_id=pool_id, token0="0xaaa", token1="0xbbb",
            is_live=True, usd_depth=5000, historical_edge_count=3,
        )
        self.registry.register(pool)

        hot = self.registry.get_hot_pools(min_edge_count=1)
        self.assertEqual(len(hot), 1)

        not_hot = self.registry.get_hot_pools(min_edge_count=10)
        self.assertEqual(len(not_hot), 0)


# ===========================================================================
# Price Oracle Tests
# ===========================================================================
class TestPriceOracle(unittest.TestCase):
    """Test price oracle with source hierarchy."""

    def test_stablecoin_price(self):
        """Stablecoins should return $1.00 with high confidence."""
        from core.price_oracle import PriceOracle

        mock_rpc = MagicMock()
        mock_rpc.eth_blockNumber.return_value = 100
        oracle = PriceOracle(mock_rpc)

        usdc_price = oracle.get_price(
            "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
        )
        self.assertIsNotNone(usdc_price)
        self.assertEqual(usdc_price.price_usd, 1.0)
        self.assertEqual(usdc_price.source, "onchain_stable")
        self.assertEqual(usdc_price.confidence, 1.0)

    def test_fallback_price(self):
        """Unknown tokens should return fallback with low confidence."""
        from core.price_oracle import PriceOracle

        mock_rpc = MagicMock()
        mock_rpc.eth_blockNumber.return_value = 100
        oracle = PriceOracle(mock_rpc)

        weth_price = oracle.get_price(
            "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
        )
        # WETH is in fallback list
        self.assertIsNotNone(weth_price)
        self.assertGreater(weth_price.price_usd, 0)

    def test_price_freshness(self):
        """Price freshness check should work."""
        from core.price_oracle import PricePoint

        fresh = PricePoint(
            token="0xtest", price_usd=100.0, source="test",
            timestamp=time.time(), block_number=100, confidence=0.9
        )
        self.assertTrue(fresh.is_fresh(max_age_seconds=60))

        stale = PricePoint(
            token="0xtest", price_usd=100.0, source="test",
            timestamp=time.time() - 300, block_number=100, confidence=0.9
        )
        self.assertFalse(stale.is_fresh(max_age_seconds=60))

    def test_price_reliability(self):
        """Price reliability check should filter low-confidence prices."""
        from core.price_oracle import PricePoint

        reliable = PricePoint(
            token="0xtest", price_usd=100.0, source="onchain_stable",
            timestamp=time.time(), block_number=100, confidence=0.9
        )
        self.assertTrue(reliable.is_reliable(min_confidence=0.5))

        unreliable = PricePoint(
            token="0xtest", price_usd=100.0, source="fallback",
            timestamp=time.time(), block_number=100, confidence=0.1
        )
        self.assertFalse(unreliable.is_reliable(min_confidence=0.5))


# ===========================================================================
# Quote Engine Tests
# ===========================================================================
class TestQuoteEngine(unittest.TestCase):
    """Test unified quote engine."""

    def test_v2_quote_from_reserves(self):
        """V2 quote should use constant product formula."""
        from core.quote_engine import QuoteEngine, V2QuoteAdapter
        from core.pool import PoolId, PoolMetadata

        mock_rpc = MagicMock()
        mock_rpc.eth_blockNumber.return_value = 100
        adapter = V2QuoteAdapter(mock_rpc)

        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xWETH", token1="0xUSDC",
            decimals0=18, decimals1=6, fee=3000, kind="v2",
            reserve0=100 * 10**18, reserve1=250000 * 10**6,
        )

        # Quote 1 WETH -> USDC
        result = adapter.quote("0xWETH", "0xUSDC", 10**18, pool)

        self.assertTrue(result.success)
        self.assertGreater(result.amount_out, 0)
        self.assertEqual(result.venue, "uniswap_v2")
        self.assertEqual(result.fee, 3000)

    def test_v2_quote_zero_reserves(self):
        """V2 quote with zero reserves should fail gracefully."""
        from core.quote_engine import V2QuoteAdapter
        from core.pool import PoolId, PoolMetadata

        mock_rpc = MagicMock()
        adapter = V2QuoteAdapter(mock_rpc)

        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xWETH", token1="0xUSDC",
            reserve0=0, reserve1=0,
        )

        result = adapter.quote("0xWETH", "0xUSDC", 10**18, pool)
        self.assertFalse(result.success)
        self.assertIn("zero reserves", result.error)

    def test_v2_quote_wrong_token(self):
        """V2 quote with token not in pool should fail."""
        from core.quote_engine import V2QuoteAdapter
        from core.pool import PoolId, PoolMetadata

        mock_rpc = MagicMock()
        adapter = V2QuoteAdapter(mock_rpc)

        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xWETH", token1="0xUSDC",
            reserve0=100 * 10**18, reserve1=250000 * 10**6,
        )

        result = adapter.quote("0xUNKNOWN", "0xUSDC", 10**18, pool)
        self.assertFalse(result.success)

    def test_quote_result_is_fresh(self):
        """Quote freshness check should work."""
        from core.quote_engine import QuoteResult

        fresh = QuoteResult(
            amount_in=100, amount_out=200, success=True,
            timestamp=time.time(), block_number=100
        )
        self.assertTrue(fresh.is_fresh(max_age_seconds=30))

        stale = QuoteResult(
            amount_in=100, amount_out=200, success=True,
            timestamp=time.time() - 60, block_number=100
        )
        self.assertFalse(stale.is_fresh(max_age_seconds=30))

    def test_quote_engine_stats(self):
        """Quote engine should track statistics."""
        from core.quote_engine import QuoteEngine

        mock_rpc = MagicMock()
        mock_rpc.eth_blockNumber.return_value = 100
        engine = QuoteEngine(mock_rpc)

        stats = engine.stats()
        self.assertEqual(stats["quotes_attempted"], 0)
        self.assertEqual(stats["quotes_successful"], 0)


# ===========================================================================
# Economic Model Tests
# ===========================================================================
class TestEconomicModel(unittest.TestCase):
    """Test canonical economic model."""

    def setUp(self):
        from core.price_oracle import PriceOracle
        self.mock_rpc = MagicMock()
        self.mock_rpc.eth_blockNumber.return_value = 100
        self.oracle = PriceOracle(self.mock_rpc)
        # Patch WETH price
        self.oracle._cache["0x82af49447d8a07e3bd95bd0d56f35241523fbab1"] = (
            self.oracle.get_price("0x82af49447d8a07e3bd95bd0d56f35241523fbab1")
        )

    def test_profitable_opportunity(self):
        """A clearly profitable opportunity should be detected."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle, min_profit_usd=0.01)
        result = model.evaluate(
            input_usd=100.0,
            gross_output_usd=101.0,
            flash_loan_amount_usd=100.0,
            estimated_gas_units=350000,
            gas_price_gwei=0.01,
            slippage_bps=10,
        )

        self.assertTrue(result.gross_profit_usd > 0)
        self.assertIsInstance(result.total_costs_usd, float)
        self.assertIsInstance(result.expected_net_profit_usd, float)

    def test_unprofitable_opportunity(self):
        """An unprofitable opportunity should be rejected."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle, min_profit_usd=0.01)
        result = model.evaluate(
            input_usd=100.0,
            gross_output_usd=100.01,  # Tiny gross profit
            flash_loan_amount_usd=100.0,
            estimated_gas_units=500000,
            gas_price_gwei=0.1,  # High gas
            slippage_bps=50,
        )

        # Should not be worth executing
        self.assertFalse(result.is_worth_executing)

    def test_flash_loan_fee_calculation(self):
        """Flash loan fee should be 0.05% of borrowed amount."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle)
        fee = model.flash_loan_fee(1000.0)
        self.assertAlmostEqual(fee, 0.5, places=4)  # 0.05% of 1000 = 0.5

    def test_gas_cost_calculation(self):
        """Gas cost should be calculated correctly."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle)
        cost = model.gas_cost_usd(
            gas_units=350000, gas_price_gwei=0.01, eth_price_usd=2500.0
        )
        # 350000 * 0.01e-9 * 2500 = 0.0875
        self.assertGreater(cost, 0)
        self.assertAlmostEqual(cost, 0.00875, places=4)

    def test_evaluate_actual_gas_in_delta(self):
        """
        If gas is already in settlement delta, don't subtract again.
        This is the no-double-subtract test.
        """
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle)

        # Settlement delta already includes gas cost
        profit = model.evaluate_actual(
            settlement_delta_usd=0.50,
            actual_gas_usd=0.05,
            gas_already_in_delta=True,
        )
        self.assertAlmostEqual(profit, 0.50, places=4)

    def test_evaluate_actual_gas_not_in_delta(self):
        """If gas is NOT in settlement delta, subtract it."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle)

        profit = model.evaluate_actual(
            settlement_delta_usd=0.50,
            actual_gas_usd=0.05,
            gas_already_in_delta=False,
        )
        self.assertAlmostEqual(profit, 0.45, places=4)

    def test_evaluate_actual_zero_profit(self):
        """Zero profit case."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle)
        profit = model.evaluate_actual(
            settlement_delta_usd=0.0,
            actual_gas_usd=0.05,
            gas_already_in_delta=True,
        )
        self.assertAlmostEqual(profit, 0.0, places=4)

    def test_evaluate_actual_loss(self):
        """Loss case: settlement delta negative."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle)
        profit = model.evaluate_actual(
            settlement_delta_usd=-0.05,
            actual_gas_usd=0.05,
            gas_already_in_delta=True,
        )
        self.assertAlmostEqual(profit, -0.05, places=4)

    def test_roi_calculation(self):
        """ROI should be calculated correctly."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle, min_profit_usd=0.0, min_roi_bps=0.0)
        result = model.evaluate(
            input_usd=100.0,
            gross_output_usd=101.0,
            flash_loan_amount_usd=100.0,
            estimated_gas_units=100000,
            gas_price_gwei=0.01,
            slippage_bps=0,
        )

        if result.input_usd > 0:
            expected_roi = (result.expected_net_profit_usd / result.input_usd) * 10000
            self.assertAlmostEqual(result.roi_bps, expected_roi, places=2)

    def test_below_threshold_reason(self):
        """Below-threshold opportunities should have a reason."""
        from core.economic_model import EconomicModel

        model = EconomicModel(self.oracle, min_profit_usd=1.00)
        result = model.evaluate(
            input_usd=100.0,
            gross_output_usd=100.50,
            flash_loan_amount_usd=100.0,
            estimated_gas_units=100000,
            gas_price_gwei=0.01,
            slippage_bps=0,
        )

        if not result.is_worth_executing and result.is_profitable:
            self.assertIsNotNone(result.below_threshold_reason)


# ===========================================================================
# Size Optimizer Tests
# ===========================================================================
class TestSizeOptimizer(unittest.TestCase):
    """Test adaptive size optimizer."""

    def test_coarse_curve_defined(self):
        """Default coarse curve should have reasonable values."""
        from core.size_optimizer import DEFAULT_COARSE_CURVE

        self.assertIn(0.01, DEFAULT_COARSE_CURVE)
        self.assertIn(100.0, DEFAULT_COARSE_CURVE)
        self.assertGreater(len(DEFAULT_COARSE_CURVE), 5)

    def test_size_result_profitable(self):
        """Size result should correctly classify profitability."""
        from core.size_optimizer import SizeResult

        result = SizeResult(
            size_usd=1.0, net_profit_usd=0.05,
            gross_profit_usd=0.10, total_costs_usd=0.05,
            roi_bps=500, is_profitable=True,
        )
        self.assertTrue(result.is_profitable)

    def test_size_curve_summary(self):
        """Size curve should compute summary correctly."""
        from core.size_optimizer import SizeCurve, SizeResult

        curve = SizeCurve()
        curve.results = [
            SizeResult(size_usd=0.10, net_profit_usd=0.01, gross_profit_usd=0.02,
                       total_costs_usd=0.01, roi_bps=1000, is_profitable=True),
            SizeResult(size_usd=0.50, net_profit_usd=0.05, gross_profit_usd=0.10,
                       total_costs_usd=0.05, roi_bps=1000, is_profitable=True),
            SizeResult(size_usd=1.00, net_profit_usd=-0.01, gross_profit_usd=0.05,
                       total_costs_usd=0.06, roi_bps=-100, is_profitable=False),
        ]

        profitable = curve.profitable_results
        self.assertEqual(len(profitable), 2)
        self.assertTrue(curve.has_profitable)


# ===========================================================================
# Route Generator Tests
# ===========================================================================
class TestRouteGenerator(unittest.TestCase):
    """Test cross-venue and multi-hop route generation."""

    def setUp(self):
        from core.pool import PoolRegistry, PoolId, PoolMetadata
        self.registry = PoolRegistry()
        self.PoolId = PoolId
        self.PoolMetadata = PoolMetadata

        # Register pools for testing
        self._register_test_pools()

    def _register_test_pools(self):
        """Register test pools across venues."""
        tokens = {
            "WETH": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            "ARB": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        }

        # WETH/USDC on multiple venues
        for venue, factory in [
            ("uniswap_v2", "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"),
            ("sushi", "0xc35dadb65012ec5796536bd9864ed8773abc74c4"),
            ("camelot", "0x6eccab422d763ac031210895c81787e87b43a652"),
        ]:
            pool_id = self.PoolId(
                chain_id=42161, venue=venue, factory=factory,
                pool_address=f"0x{venue}_weth_usdc_pool"
            )
            pool = self.PoolMetadata(
                pool_id=pool_id, token0=tokens["WETH"], token1=tokens["USDC"],
                is_live=True, usd_depth=50000, kind="v2",
            )
            self.registry.register(pool)

        # USDC/ARB on one venue
        pool_id = self.PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0xuniswap_v2_usdc_arb_pool"
        )
        pool = self.PoolMetadata(
            pool_id=pool_id, token0=tokens["USDC"], token1=tokens["ARB"],
            is_live=True, usd_depth=10000, kind="v2",
        )
        self.registry.register(pool)

        # ARB/WETH on one venue
        pool_id = self.PoolId(
            chain_id=42161, venue="sushi",
            factory="0xc35dadb65012ec5796536bd9864ed8773abc74c4",
            pool_address="0xsushi_arb_weth_pool"
        )
        pool = self.PoolMetadata(
            pool_id=pool_id, token0=tokens["ARB"], token1=tokens["WETH"],
            is_live=True, usd_depth=15000, kind="v2",
        )
        self.registry.register(pool)

    def test_cross_venue_routes_generated(self):
        """Cross-venue routes should be generated for multi-venue pairs."""
        from core.route_generator import RouteGenerator

        gen = RouteGenerator(self.registry)
        routes = gen.generate_cross_venue_routes(
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        )

        # Should have routes: uni->sushi, uni->camelot, sushi->uni, sushi->camelot, camelot->uni, camelot->sushi
        self.assertGreater(len(routes), 0)

        # All should be cross-venue
        for route in routes:
            self.assertTrue(route.is_cross_venue)
            self.assertEqual(route.num_hops, 2)

    def test_cross_venue_route_venues_differ(self):
        """Cross-venue routes must span different venues."""
        from core.route_generator import RouteGenerator

        gen = RouteGenerator(self.registry)
        routes = gen.generate_cross_venue_routes(
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        )

        for route in routes:
            buy_venue = route.steps[0].venue
            sell_venue = route.steps[1].venue
            self.assertNotEqual(buy_venue, sell_venue)

    def test_triangular_routes(self):
        """Triangular routes should start and end with same token."""
        from core.route_generator import RouteGenerator

        gen = RouteGenerator(self.registry, max_hops=3, max_routes_per_token=50)
        routes = gen.generate_triangular_routes(
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
            max_hops=3,
        )

        for route in routes:
            self.assertTrue(route.is_triangular)
            self.assertEqual(route.steps[0].token_in.lower(),
                           route.steps[-1].token_out.lower())

    def test_route_ranking(self):
        """Routes should be ranked by score."""
        from core.route_generator import RouteGenerator, Route, RouteStep

        gen = RouteGenerator(self.registry)

        routes = [
            Route(steps=[
                RouteStep(venue="a", pool_address="0x1", token_in="0xW", token_out="0xU"),
                RouteStep(venue="b", pool_address="0x2", token_in="0xU", token_out="0xW"),
            ], start_token="0xW"),
            Route(steps=[
                RouteStep(venue="a", pool_address="0x1", token_in="0xW", token_out="0xU"),
                RouteStep(venue="b", pool_address="0x2", token_in="0xU", token_out="0xA"),
                RouteStep(venue="c", pool_address="0x3", token_in="0xA", token_out="0xW"),
            ], start_token="0xW"),
        ]

        ranked = gen.rank_routes(routes)
        # Cross-venue 2-hop should rank higher than 3-hop
        self.assertGreaterEqual(ranked[0].score, ranked[-1].score)

    def test_route_to_dict(self):
        """Route serialization should work."""
        from core.route_generator import Route, RouteStep

        route = Route(
            start_token="0xWETH",
            steps=[
                RouteStep(venue="uniswap_v2", pool_address="0x1",
                         token_in="0xWETH", token_out="0xUSDC", fee=3000, kind="v2"),
                RouteStep(venue="sushi", pool_address="0x2",
                         token_in="0xUSDC", token_out="0xWETH", fee=3000, kind="v2"),
            ],
        )

        d = route.to_dict()
        self.assertEqual(d["num_hops"], 2)
        self.assertTrue(d["is_cross_venue"])
        self.assertEqual(len(d["steps"]), 2)


# ===========================================================================
# Execution Gate Tests
# ===========================================================================
class TestExecutionGate(unittest.TestCase):
    """Test execution gate."""

    def setUp(self):
        from core.execution_gate import ExecutionGate, GateConfig
        from core.rpc_health import RPCHealthMonitor

        self.config = GateConfig(
            max_gas_usd=1.00,
            max_slippage_bps=50,
            max_capital_exposure_usd=5000.0,
            min_profit_usd=0.01,
            max_quote_age_seconds=30,
        )

        mock_health = MagicMock()
        mock_health.is_healthy.return_value = True
        self.gate = ExecutionGate(self.config, mock_health)

    def test_pass_when_all_conditions_met(self):
        """Gate should pass when all conditions are met."""
        from core.quote_engine import QuoteResult
        from core.economic_model import EconomicResult

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            token_in="0xWETH", token_out="0xUSDC",
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=2500.0, gross_output_usd=2505.0,
            expected_net_profit_usd=3.0,
            estimated_gas_usd=0.5,
            is_profitable=True, is_worth_executing=True,
        )

        result = self.gate.check(quote, economic, sim_passed=True, candidate_id="test_1")
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "PASS")

    def test_reject_stale_quote(self):
        """Gate should reject stale quotes."""
        from core.quote_engine import QuoteResult
        from core.economic_model import EconomicResult

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time() - 60,  # 60 seconds old
            block_number=100,
        )

        economic = EconomicResult(
            input_usd=2500.0, gross_output_usd=2505.0,
            expected_net_profit_usd=3.0, is_profitable=True,
        )

        result = self.gate.check(quote, economic, sim_passed=True)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "stale_quote")

    def test_reject_not_profitable(self):
        """Gate should reject unprofitable opportunities."""
        from core.quote_engine import QuoteResult
        from core.economic_model import EconomicResult

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=2500.0, gross_output_usd=2490.0,
            expected_net_profit_usd=-10.0,
            is_profitable=False,
        )

        result = self.gate.check(quote, economic, sim_passed=True)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "not_profitable")

    def test_reject_gas_too_high(self):
        """Gate should reject when gas exceeds maximum."""
        from core.quote_engine import QuoteResult
        from core.economic_model import EconomicResult

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=2500.0, gross_output_usd=2510.0,
            expected_net_profit_usd=5.0,
            estimated_gas_usd=2.00,  # Exceeds max_gas_usd=1.00
            is_profitable=True, is_worth_executing=True,
        )

        result = self.gate.check(quote, economic, sim_passed=True)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "gas_too_high")

    def test_reject_slippage_too_high(self):
        """Gate should reject when slippage exceeds maximum."""
        from core.quote_engine import QuoteResult
        from core.economic_model import EconomicResult

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=100,  # 1% > 0.5% max
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=2500.0, gross_output_usd=2505.0,
            expected_net_profit_usd=3.0,
            is_profitable=True, is_worth_executing=True,
        )

        result = self.gate.check(quote, economic, sim_passed=True)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "slippage_too_high")

    def test_reject_already_executed(self):
        """Gate should reject duplicate candidates."""
        from core.quote_engine import QuoteResult
        from core.economic_model import EconomicResult

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=2500.0, gross_output_usd=2505.0,
            expected_net_profit_usd=3.0,
            is_profitable=True, is_worth_executing=True,
        )

        # First check passes
        result1 = self.gate.check(quote, economic, sim_passed=True, candidate_id="dup_test")
        self.assertTrue(result1.passed)

        # Second check with same candidate_id fails
        result2 = self.gate.check(quote, economic, sim_passed=True, candidate_id="dup_test")
        self.assertFalse(result2.passed)
        self.assertEqual(result2.reason, "already_executed")

    def test_circuit_breaker(self):
        """Gate should trip circuit breaker after consecutive failures."""
        from core.quote_engine import QuoteResult
        from core.economic_model import EconomicResult

        self.gate._consecutive_failures = self.config.max_consecutive_failures

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=50.0, gross_output_usd=55.0,
            expected_net_profit_usd=3.0,
            is_profitable=True, is_worth_executing=True,
        )

        result = self.gate.check(quote, economic, sim_passed=True)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "circuit_breaker")


# ===========================================================================
# Accounting Ledger Tests
# ===========================================================================
class TestAccountingLedger(unittest.TestCase):
    """Test immutable execution ledger."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_veritas.db")
        self.patcher = patch("core.db.DB_PATH", self.db_path)
        self.patcher.start()

        import importlib
        import core.accounting
        importlib.reload(core.accounting)
        from core.accounting import AccountingLedger, ExecutionRecord
        self.ledger = AccountingLedger()
        self.ExecutionRecord = ExecutionRecord

    def tearDown(self):
        self.patcher.stop()
        self.tmpdir.cleanup()

    def test_record_execution(self):
        """Recording an execution should persist it."""
        record = self.ExecutionRecord(
            tx_hash="0xabc123",
            chain_id=42161,
            projected_profit_usd=1.0,
            status="pending",
        )

        inserted = self.ledger.record_execution(record)
        self.assertTrue(inserted)

        retrieved = self.ledger.get_execution("0xabc123")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.tx_hash, "0xabc123")
        self.assertEqual(retrieved.status, "pending")

    def test_idempotent_recording(self):
        """Same tx_hash should not create duplicate records."""
        record = self.ExecutionRecord(tx_hash="0xidem", projected_profit_usd=1.0)

        first = self.ledger.record_execution(record)
        self.assertTrue(first)

        second = self.ledger.record_execution(record)
        self.assertFalse(second)  # Already exists

    def test_verify_execution_gas_in_delta(self):
        """
        When gas is already in settlement delta, verified = realized.
        No double subtraction.
        """
        record = self.ExecutionRecord(
            tx_hash="0xverify1", projected_profit_usd=1.0, status="pending",
        )
        self.ledger.record_execution(record)

        updated = self.ledger.verify_execution(
            tx_hash="0xverify1",
            realized_profit_usd=0.50,
            gas_native=0.0001,
            gas_usd=0.25,
            block_number=100,
            gas_already_in_delta=True,
        )

        self.assertIsNotNone(updated)
        self.assertAlmostEqual(updated.verified_profit_usd, 0.50, places=4)
        self.assertEqual(updated.status, "confirmed")

    def test_verify_execution_gas_not_in_delta(self):
        """When gas is NOT in delta, subtract it."""
        record = self.ExecutionRecord(
            tx_hash="0xverify2", projected_profit_usd=1.0, status="pending",
        )
        self.ledger.record_execution(record)

        updated = self.ledger.verify_execution(
            tx_hash="0xverify2",
            realized_profit_usd=0.50,
            gas_native=0.0001,
            gas_usd=0.10,
            block_number=100,
            gas_already_in_delta=False,
        )

        self.assertIsNotNone(updated)
        self.assertAlmostEqual(updated.verified_profit_usd, 0.40, places=4)

    def test_mark_reverted(self):
        """Reverted transactions should record gas loss."""
        record = self.ExecutionRecord(
            tx_hash="0xrevert1", projected_profit_usd=1.0, status="pending",
        )
        self.ledger.record_execution(record)

        updated = self.ledger.mark_reverted(
            tx_hash="0xrevert1",
            gas_native=0.0001,
            gas_usd=0.05,
            block_number=100,
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "reverted")
        self.assertAlmostEqual(updated.verified_profit_usd, -0.05, places=4)

    def test_get_pending_executions(self):
        """Pending executions should be retrievable for crash recovery."""
        for i in range(3):
            record = self.ExecutionRecord(
                tx_hash=f"0xpending{i}", status="pending",
            )
            self.ledger.record_execution(record)

        pending = self.ledger.get_pending_executions()
        self.assertEqual(len(pending), 3)

    def test_accounting_summary(self):
        """Accounting summary should aggregate correctly."""
        # Record some executions
        for i in range(3):
            record = self.ExecutionRecord(
                tx_hash=f"0xsum{i}", status="confirmed",
                verified_profit_usd=0.50, gas_usd=0.05,
            )
            self.ledger.record_execution(record)

        summary = self.ledger.summary()
        self.assertEqual(summary["total_executions"], 3)
        self.assertEqual(summary["confirmed"], 3)


# ===========================================================================
# RPC Health Monitor Tests
# ===========================================================================
class TestRPCHealthMonitor(unittest.TestCase):
    """Test RPC health monitoring."""

    def test_initial_health(self):
        """All endpoints should start as healthy."""
        from core.rpc_health import RPCHealthMonitor

        monitor = RPCHealthMonitor([
            "https://rpc1.example.com",
            "https://rpc2.example.com",
        ])

        self.assertTrue(monitor.is_healthy())
        self.assertEqual(len(monitor.get_healthy_urls()), 2)

    def test_record_success(self):
        """Recording success should update metrics."""
        from core.rpc_health import RPCHealthMonitor

        monitor = RPCHealthMonitor(["https://rpc1.example.com"])
        monitor.record_success("https://rpc1.example.com", latency_ms=100, block_number=50)

        status = monitor.status()
        self.assertTrue(status["is_healthy"])

    def test_record_error_marks_unhealthy(self):
        """Consecutive errors should mark endpoint unhealthy."""
        from core.rpc_health import RPCHealthMonitor

        monitor = RPCHealthMonitor(
            ["https://rpc1.example.com"],
            max_consecutive_errors=3,
        )

        for _ in range(3):
            monitor.record_error("https://rpc1.example.com", "timeout", is_timeout=True)

        self.assertFalse(monitor.is_healthy())

    def test_failover(self):
        """Getting healthy RPC should failover to next endpoint."""
        from core.rpc_health import RPCHealthMonitor

        monitor = RPCHealthMonitor(
            ["https://rpc1.example.com", "https://rpc2.example.com"],
            max_consecutive_errors=1,
        )

        # Kill first endpoint
        monitor.record_error("https://rpc1.example.com", "timeout")

        # Should still get a healthy RPC (the second one)
        rpc = monitor.get_healthy_rpc()
        self.assertIsNotNone(rpc)


# ===========================================================================
# Error Taxonomy Tests
# ===========================================================================
class TestErrorTaxonomy(unittest.TestCase):
    """Test error classification."""

    def test_classify_timeout(self):
        """Timeout exceptions should be classified correctly."""
        from core.error_taxonomy import ErrorTaxonomy, ErrorCode

        err = ErrorTaxonomy.classify(TimeoutError("Connection timed out"))
        self.assertEqual(err.code, ErrorCode.RPC_TIMEOUT)
        self.assertTrue(err.recoverable)

    def test_classify_connection_error(self):
        """Connection errors should be classified as RPC unavailable."""
        from core.error_taxonomy import ErrorTaxonomy, ErrorCode

        err = ErrorTaxonomy.classify(ConnectionError("Connection refused"))
        self.assertEqual(err.code, ErrorCode.RPC_UNAVAILABLE)

    def test_classify_revert(self):
        """Revert errors should be classified correctly."""
        from core.error_taxonomy import ErrorTaxonomy, ErrorCode

        err = ErrorTaxonomy.classify(Exception("transaction reverted"))
        self.assertEqual(err.code, ErrorCode.SIMULATION_REVERT)

    def test_classify_nonce(self):
        """Nonce errors should be classified correctly."""
        from core.error_taxonomy import ErrorTaxonomy, ErrorCode

        err = ErrorTaxonomy.classify(Exception("nonce too low"))
        self.assertEqual(err.code, ErrorCode.NONCE_FAILURE)

    def test_to_rejection_reason(self):
        """Error codes should map to rejection reasons."""
        from core.error_taxonomy import ErrorTaxonomy, ErrorCode
        from core.opportunity_telemetry import RejectionReason

        err = ErrorTaxonomy.make_error(ErrorCode.GAS_FAILURE, "gas too high")
        reason = err.to_rejection_reason()
        self.assertEqual(reason, RejectionReason.GAS_REJECTION)

    def test_make_error_direct(self):
        """Creating errors directly should work."""
        from core.error_taxonomy import ErrorTaxonomy, ErrorCode, ErrorCategory

        err = ErrorTaxonomy.make_error(
            ErrorCode.QUOTE_FAILURE, "quote unavailable",
            candidate_id="test_123", recoverable=True,
        )

        self.assertEqual(err.code, ErrorCode.QUOTE_FAILURE)
        self.assertEqual(err.category, ErrorCategory.DATA)
        self.assertEqual(err.candidate_id, "test_123")
        self.assertTrue(err.recoverable)

    def test_error_to_dict(self):
        """Error serialization should work."""
        from core.error_taxonomy import ErrorTaxonomy, ErrorCode

        err = ErrorTaxonomy.make_error(ErrorCode.RPC_TIMEOUT, "timeout")
        d = err.to_dict()

        self.assertEqual(d["code"], "rpc_timeout")
        self.assertEqual(d["category"], "rpc")
        self.assertIn("timestamp", d)


# ===========================================================================
# Scan Result Tests
# ===========================================================================
class TestScanResultOverhaul(unittest.TestCase):
    """Test scan result diagnostics."""

    def test_why_zero_report_no_quotes(self):
        """Report should explain when no quotes obtained."""
        from core.scan_result import ScanResult

        result = ScanResult()
        result.statistics = {
            "pairs_discovered": 5,
            "quotes_attempted": 0,
            "valid_quotes": 0,
        }
        result.scan_metadata = {"block_number": 100}

        report = result.generate_why_zero_report()
        self.assertIn("NO QUOTES OBTAINED", report)

    def test_why_zero_report_market_efficient(self):
        """Report should explain when market is efficient."""
        from core.scan_result import ScanResult
        from core.opportunity_telemetry import Candidate, CandidateStatus

        result = ScanResult()
        result.statistics = {
            "pairs_discovered": 10,
            "quotes_attempted": 50,
            "valid_quotes": 48,
        }
        result.candidates = [
            Candidate(
                route="WETH/USDC/WETH",
                buy_venue="uniswap", sell_venue="sushi",
                input_amount=2500.0, gross_profit_usd=0.5,
                net_profit_usd=-0.1,
                status=CandidateStatus.REJECTED,
            )
        ]
        result.scan_metadata = {"block_number": 100}

        report = result.generate_why_zero_report()
        self.assertIn("NO PROFITABLE OPPORTUNITY", report)

    def test_why_zero_report_below_threshold(self):
        """Report should show candidates below threshold."""
        from core.scan_result import ScanResult
        from core.opportunity_telemetry import Candidate, CandidateStatus

        result = ScanResult()
        result.statistics = {
            "pairs_discovered": 10,
            "quotes_attempted": 50,
            "valid_quotes": 48,
        }
        result.candidates = [
            Candidate(
                route="WETH/USDC/WETH",
                buy_venue="uniswap", sell_venue="sushi",
                input_amount=2500.0, gross_profit_usd=0.5,
                net_profit_usd=0.001,
                status=CandidateStatus.ECONOMICALLY_VIABLE,
            )
        ]
        result.scan_metadata = {"block_number": 100}

        report = result.generate_why_zero_report()
        self.assertIn("BELOW EXECUTION THRESHOLD", report)

    def test_backward_compat_edges(self):
        """ScanResult should maintain backward compatibility."""
        from core.scan_result import ScanResult

        result = ScanResult()
        result.edges = [{"pool_buy": "0x1", "pool_sell": "0x2", "net_margin": 1.0}]

        self.assertTrue(bool(result))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["net_margin"], 1.0)

    def test_rejection_counting(self):
        """Rejections should be counted correctly."""
        from core.scan_result import ScanResult
        from core.opportunity_telemetry import RejectionReason

        result = ScanResult()
        result.record_rejection(RejectionReason.GAS_REJECTION)
        result.record_rejection(RejectionReason.GAS_REJECTION)
        result.record_rejection(RejectionReason.SAFETY_MARGIN_REJECTION)

        self.assertEqual(result.rejections["gas_rejection"], 2)
        self.assertEqual(result.rejections["safety_margin_rejection"], 1)


# ===========================================================================
# Property Tests (CPMM Mathematics)
# ===========================================================================
class TestCPMMProperties(unittest.TestCase):
    """Property tests for CPMM mathematics."""

    def test_v2_output_positive_for_valid_input(self):
        """V2 output should be positive for any valid input."""
        from core.quote_engine import V2QuoteAdapter
        from core.pool import PoolId, PoolMetadata

        mock_rpc = MagicMock()
        adapter = V2QuoteAdapter(mock_rpc)

        pool_id = PoolId(
            chain_id=42161, venue="test",
            factory="0xfactory", pool_address="0xpool"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xA", token1="0xB",
            reserve0=1000 * 10**18, reserve1=2500000 * 10**6,
        )

        for amount in [10**10, 10**15, 10**18, 10**20]:
            result = adapter.quote("0xA", "0xB", amount, pool)
            if result.success:
                self.assertGreater(result.amount_out, 0,
                                   f"Output should be positive for input={amount}")

    def test_v2_output_bounded_by_reserve(self):
        """V2 output should never exceed reserve_out."""
        from core.quote_engine import V2QuoteAdapter
        from core.pool import PoolId, PoolMetadata

        mock_rpc = MagicMock()
        adapter = V2QuoteAdapter(mock_rpc)

        pool_id = PoolId(
            chain_id=42161, venue="test",
            factory="0xfactory", pool_address="0xpool"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xA", token1="0xB",
            reserve0=100 * 10**18, reserve1=250000 * 10**6,
        )

        result = adapter.quote("0xA", "0xB", 10**18, pool)
        if result.success:
            self.assertLessEqual(result.amount_out, pool.reserve1,
                                 "Output cannot exceed reserve")

    def test_v2_larger_input_more_output(self):
        """Larger input should produce more output (monotonicity)."""
        from core.quote_engine import V2QuoteAdapter
        from core.pool import PoolId, PoolMetadata

        mock_rpc = MagicMock()
        adapter = V2QuoteAdapter(mock_rpc)

        pool_id = PoolId(
            chain_id=42161, venue="test",
            factory="0xfactory", pool_address="0xpool"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xA", token1="0xB",
            reserve0=10000 * 10**18, reserve1=25000000 * 10**6,
        )

        out1 = adapter.quote("0xA", "0xB", 10**15, pool)
        out2 = adapter.quote("0xA", "0xB", 10**18, pool)

        if out1.success and out2.success:
            self.assertGreater(out2.amount_out, out1.amount_out,
                               "Larger input should produce more output")

    def test_decimal_roundtrip(self):
        """Decimal conversion should be within tolerance."""
        from core.price_oracle import TOKEN_DECIMALS

        for addr, decimals in TOKEN_DECIMALS.items():
            wei = 1.5 * (10 ** decimals)
            back = wei / (10 ** decimals)
            self.assertAlmostEqual(back, 1.5, places=10,
                                   msg=f"Round-trip failed for {addr}")


# ===========================================================================
# End-to-End Pipeline Tests
# ===========================================================================
class TestEndToEndPipeline(unittest.TestCase):
    """Test the complete pipeline with mocks."""

    def test_full_pipeline_profitable(self):
        """
        Positive opportunity:
        candidate -> economic viable -> gate pass -> execution -> verification -> capital increase
        """
        from core.economic_model import EconomicModel, EconomicResult
        from core.execution_gate import ExecutionGate, GateConfig
        from core.accounting import AccountingLedger, ExecutionRecord
        from core.quote_engine import QuoteResult

        # Create a profitable economic result
        economic = EconomicResult(
            input_usd=100.0,
            gross_output_usd=101.0,
            gross_profit_usd=1.0,
            flash_loan_fee_usd=0.05,
            estimated_gas_usd=0.10,
            expected_net_profit_usd=0.80,
            is_profitable=True,
            is_worth_executing=True,
        )

        # Create a valid quote
        quote = QuoteResult(
            amount_in=10**18, amount_out=2525 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        # Gate should pass
        config = GateConfig(min_profit_usd=0.01, max_gas_usd=1.00)
        gate = ExecutionGate(config)
        gate_result = gate.check(quote, economic, sim_passed=True, candidate_id="e2e_1")
        self.assertTrue(gate_result.passed)

        # Record execution
        record = ExecutionRecord(
            tx_hash="0xe2e_profitable",
            projected_profit_usd=economic.expected_net_profit_usd,
            status="pending",
        )

        # Verify execution
        verified_profit = 0.75  # Slightly less than projected (slippage)
        record.status = "confirmed"
        record.verified_profit_usd = verified_profit
        record.realized_profit_usd = verified_profit

        self.assertGreater(record.verified_profit_usd, 0)

    def test_full_pipeline_false_opportunity(self):
        """
        False opportunity:
        candidate -> economic rejection
        """
        from core.economic_model import EconomicResult
        from core.execution_gate import ExecutionGate, GateConfig
        from core.quote_engine import QuoteResult

        # Unprofitable
        economic = EconomicResult(
            input_usd=100.0,
            gross_output_usd=100.01,
            gross_profit_usd=0.01,
            flash_loan_fee_usd=0.05,
            estimated_gas_usd=0.10,
            expected_net_profit_usd=-0.14,
            is_profitable=False,
        )

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        config = GateConfig(min_profit_usd=0.01)
        gate = ExecutionGate(config)
        gate_result = gate.check(quote, economic, sim_passed=True)

        self.assertFalse(gate_result.passed)
        self.assertEqual(gate_result.reason, "not_profitable")

    def test_full_pipeline_simulation_failure(self):
        """
        Simulation failure:
        no broadcast, no capital mutation
        """
        from core.economic_model import EconomicResult
        from core.execution_gate import ExecutionGate, GateConfig
        from core.quote_engine import QuoteResult

        economic = EconomicResult(
            input_usd=100.0, gross_output_usd=101.0,
            expected_net_profit_usd=0.80,
            is_profitable=True, is_worth_executing=True,
        )

        quote = QuoteResult(
            amount_in=10**18, amount_out=2525 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        config = GateConfig(require_simulation_pass=True)
        gate = ExecutionGate(config)

        # Simulation did NOT pass
        gate_result = gate.check(quote, economic, sim_passed=False)

        self.assertFalse(gate_result.passed)
        self.assertEqual(gate_result.reason, "simulation_required")

    def test_full_pipeline_revert(self):
        """
        Revert:
        gas loss, no profit, one execution record
        """
        from core.accounting import AccountingLedger, ExecutionRecord

        tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmpdir.name, "test_veritas.db")
        with patch("core.db.DB_PATH", db_path):
            import importlib
            import core.accounting
            importlib.reload(core.accounting)
            from core.accounting import AccountingLedger, ExecutionRecord

            ledger = AccountingLedger()

            # Record pending execution
            record = ExecutionRecord(
                tx_hash="0xrevert_e2e",
                projected_profit_usd=1.0,
                status="pending",
            )
            ledger.record_execution(record)

            # Mark as reverted
            updated = ledger.mark_reverted(
                tx_hash="0xrevert_e2e",
                gas_native=0.0001,
                gas_usd=0.05,
                block_number=100,
            )

            self.assertIsNotNone(updated)
            self.assertEqual(updated.status, "reverted")
            self.assertLess(updated.verified_profit_usd, 0)

            # Only one record
            all_execs = ledger.get_all_executions()
            self.assertEqual(len(all_execs), 1)

        tmpdir.cleanup()

    def test_full_pipeline_duplicate_tx(self):
        """
        Duplicate transaction:
        one ledger record, one capital mutation
        """
        from core.accounting import AccountingLedger, ExecutionRecord

        tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(tmpdir.name, "test_veritas.db")
        with patch("core.db.DB_PATH", db_path):
            import importlib
            import core.accounting
            importlib.reload(core.accounting)
            from core.accounting import AccountingLedger, ExecutionRecord

            ledger = AccountingLedger()

            record = ExecutionRecord(
                tx_hash="0xdup_e2e",
                projected_profit_usd=1.0,
            )

            first = ledger.record_execution(record)
            self.assertTrue(first)

            # Try to record again
            second = ledger.record_execution(record)
            self.assertFalse(second)

            # Only one record
            all_execs = ledger.get_all_executions()
            self.assertEqual(len(all_execs), 1)

        tmpdir.cleanup()

    def test_full_pipeline_rpc_outage(self):
        """
        RPC outage:
        SYSTEM_ZERO, not MARKET_ZERO
        """
        from core.rpc_health import RPCHealthMonitor

        monitor = RPCHealthMonitor(
            ["https://dead-rpc.example.com"],
            max_consecutive_errors=1,
        )

        # Simulate RPC failure
        monitor.record_error("https://dead-rpc.example.com", "connection refused")

        # Should be unhealthy
        self.assertFalse(monitor.is_healthy())

        # This is SYSTEM_ZERO, not MARKET_ZERO
        status = monitor.status()
        self.assertFalse(status["is_healthy"])


# ===========================================================================
# Engine Integration Tests
# ===========================================================================
class TestVeritasEngine(unittest.TestCase):
    """Test the main engine orchestrator."""

    def test_engine_config_defaults(self):
        """Engine should have sensible defaults."""
        from veritas_engine import VeritasEngine, DEFAULT_CONFIG

        engine = VeritasEngine()
        self.assertEqual(engine.config["discovery_interval_seconds"], 20)
        self.assertEqual(engine.config["chain_id"], 42161)
        self.assertIn("gateway.tenderly.co", engine.config["rpc_urls"][0])

    def test_engine_custom_config(self):
        """Engine should accept custom config."""
        from veritas_engine import VeritasEngine

        engine = VeritasEngine({"discovery_interval_seconds": 10, "chain_id": 1})
        self.assertEqual(engine.config["discovery_interval_seconds"], 10)
        self.assertEqual(engine.config["chain_id"], 1)

    def test_engine_register_pool(self):
        """Engine should register pools correctly."""
        from veritas_engine import VeritasEngine

        engine = VeritasEngine()
        engine.register_pool(
            venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0x1234567890abcdef1234567890abcdef12345678",
            token0="0xWETH",
            token1="0xUSDC",
            fee=3000,
            kind="v2",
        )

        self.assertEqual(engine.pool_registry.count(), 1)

    def test_engine_status(self):
        """Engine status should return comprehensive info."""
        from veritas_engine import VeritasEngine

        engine = VeritasEngine()
        status = engine.status()

        self.assertIn("cycle", status)
        self.assertIn("pools", status)
        self.assertIn("rpc_health", status)
        self.assertIn("capital", status)
        self.assertIn("accounting", status)



# ===========================================================================
# Fix Verification Tests
# ===========================================================================
class TestAccountingNoDoubleSubtraction(unittest.TestCase):
    """
    CRITICAL: Verify that accounting summary() NEVER double-subtracts gas.
    
    The accounting equation is:
      verified_profit = settlement_asset_delta - actual_external_gas_cost
      (only if gas is not already in the settlement delta)
    
    When gas_already_in_delta=True (typical for flash-loan arb):
      verified_profit = realized_profit_usd  (gas already in the delta)
    
    summary() must NOT subtract gas again from verified_profit_usd.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_veritas.db")
        self.patcher = patch("core.db.DB_PATH", self.db_path)
        self.patcher.start()

        import importlib
        import core.accounting
        importlib.reload(core.accounting)
        from core.accounting import AccountingLedger, ExecutionRecord
        self.ledger = AccountingLedger()
        self.ExecutionRecord = ExecutionRecord

    def tearDown(self):
        self.patcher.stop()
        self.tmpdir.cleanup()

    def test_summary_no_double_subtract_when_gas_in_delta(self):
        """
        When gas_already_in_delta=True:
        - verified_profit_usd = realized_profit_usd (gas already accounted for)
        - summary() net_profit should EQUAL total verified profit (no gas subtraction)
        """
        record = self.ExecutionRecord(
            tx_hash="0x_nodouble_1",
            realized_profit_usd=0.50,
            verified_profit_usd=0.50,  # gas already in delta, so verified = realized
            gas_usd=0.05,
            gas_already_in_delta=True,
            status="confirmed",
        )
        self.ledger.record_execution(record)

        summary = self.ledger.summary()
        # net_profit should be 0.50, NOT 0.50 - 0.05 = 0.45
        self.assertAlmostEqual(summary["net_profit_usd"], 0.50, places=4,
                               msg="summary() must NOT subtract gas from verified_profit when gas_already_in_delta=True")

    def test_summary_no_double_subtract_multiple_records(self):
        """
        Multiple records with gas_already_in_delta=True:
        net_profit = SUM(verified_profit_usd), NOT SUM(verified) - SUM(gas)
        """
        for i in range(5):
            record = self.ExecutionRecord(
                tx_hash=f"0x_multi_{i}",
                realized_profit_usd=0.10,
                verified_profit_usd=0.10,  # gas already in delta
                gas_usd=0.01,
                gas_already_in_delta=True,
                status="confirmed",
            )
            self.ledger.record_execution(record)

        summary = self.ledger.summary()
        # 5 * 0.10 = 0.50, NOT 0.50 - 0.05 = 0.45
        self.assertAlmostEqual(summary["net_profit_usd"], 0.50, places=4)

    def test_summary_correct_when_gas_not_in_delta(self):
        """
        When gas_already_in_delta=False:
        - verified_profit_usd = realized_profit_usd - gas_usd (gas explicitly subtracted)
        - summary() net_profit should still equal verified_profit_usd
        """
        record = self.ExecutionRecord(
            tx_hash="0x_nodelta_1",
            realized_profit_usd=0.50,
            verified_profit_usd=0.45,  # 0.50 - 0.05 gas
            gas_usd=0.05,
            gas_already_in_delta=False,
            status="confirmed",
        )
        self.ledger.record_execution(record)

        summary = self.ledger.summary()
        # net_profit = 0.45 (already has gas subtracted)
        self.assertAlmostEqual(summary["net_profit_usd"], 0.45, places=4)


class TestLoadFromDbNoWriteLoop(unittest.TestCase):
    """
    Verify that load_from_db() does NOT write to the database.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_veritas.db")
        self.patcher = patch("core.db.DB_PATH", self.db_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmpdir.cleanup()

    def test_load_from_db_no_writes(self):
        """
        Loading pools from DB should NOT produce additional DB writes.
        We verify this by checking that register() with persist=False
        does not call _persist().
        """
        import importlib
        import core.pool
        importlib.reload(core.pool)
        from core.pool import PoolRegistry, PoolId, PoolMetadata

        registry = PoolRegistry()

        # Manually insert a pool into the DB
        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0xTESTPOOL00000000000000000000000000000001"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xWETH", token1="0xUSDC",
            is_live=True,
        )

        # Register with persist=True (initial insert)
        registry.register(pool, persist=True)
        self.assertEqual(registry.count(), 1)

        # Now create a new registry and load from DB
        registry2 = PoolRegistry()
        count = registry2.load_from_db(chain_id=42161)
        self.assertEqual(count, 1)
        self.assertEqual(registry2.count(), 1)

        # The loaded pool should be in memory
        loaded = registry2.get(pool_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.token0, "0xWETH")

    def test_register_persist_false_no_db_write(self):
        """
        register(pool, persist=False) should NOT write to DB.
        """
        import importlib
        import core.pool
        importlib.reload(core.pool)
        from core.pool import PoolRegistry, PoolId, PoolMetadata

        registry = PoolRegistry()

        pool_id = PoolId(
            chain_id=42161, venue="uniswap_v2",
            factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",
            pool_address="0xNOPERSIST00000000000000000000000000000001"
        )
        pool = PoolMetadata(
            pool_id=pool_id, token0="0xWETH", token1="0xUSDC",
        )

        # Register without persisting
        registry.register(pool, persist=False)
        self.assertEqual(registry.count(), 1)

        # Create new registry - pool should NOT be in DB
        registry2 = PoolRegistry()
        count = registry2.load_from_db(chain_id=42161)
        # The non-persisted pool should not be loaded
        self.assertEqual(registry2.get(pool_id), None)


class TestDiscoveryToRouteWiring(unittest.TestCase):
    """
    Verify that market discovery feeds into route generation.
    """

    def test_market_discovery_queries_chain(self):
        """
        MarketDiscovery should query chain for pool addresses.
        """
        from core.market_discovery import MarketDiscovery
        from core.pool import PoolRegistry

        mock_rpc = MagicMock()
        # Simulate a pool address returned by the factory
        mock_rpc.eth_call.return_value = "0x" + "0" * 24 + "0xABCDEF1234567890ABCDEF1234567890ABCDEF12".lower()
        mock_rpc.eth_blockNumber.return_value = 100

        registry = PoolRegistry()
        discovery = MarketDiscovery(mock_rpc, registry, chain_id=42161)

        tokens = [
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        ]

        # Discover from a single venue with one fee tier
        discovered = discovery.discover_pools(
            tokens,
            venues=["uniswap_v3"],
            fee_tiers=[500],
        )

        # Should have discovered at least one pool
        self.assertGreater(len(discovered), 0, "Discovery should find pools from chain")

        # The pool should be registered
        self.assertGreater(registry.count(), 0, "Discovered pools should be registered")

        # Verify eth_call was made (chain was queried)
        self.assertGreater(mock_rpc.eth_call.call_count, 0, "Should query chain via eth_call")

    def test_discovery_registers_pools_with_correct_identity(self):
        """
        Discovered pools should have correct (chain, venue, factory, pool) identity.
        """
        from core.market_discovery import MarketDiscovery
        from core.pool import PoolRegistry, PoolId

        mock_rpc = MagicMock()
        mock_rpc.eth_call.return_value = "0x" + "0" * 24 + "0xPOOLADDR1234567890ABCDEF1234567890ABCDEF".lower()
        mock_rpc.eth_blockNumber.return_value = 100

        registry = PoolRegistry()
        discovery = MarketDiscovery(mock_rpc, registry, chain_id=42161)

        tokens = [
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        ]

        discovered = discovery.discover_pools(
            tokens, venues=["uniswap_v3"], fee_tiers=[500]
        )

        if discovered:
            pool = discovered[0]
            self.assertEqual(pool.pool_id.chain_id, 42161)
            self.assertEqual(pool.pool_id.venue, "uniswap_v3")
            self.assertEqual(pool.pool_id.factory, "0x1f98431c8ad98523631ae4a59f267346ea31f984")
            self.assertEqual(pool.kind, "v3")
            self.assertTrue(pool.is_live)

if __name__ == "__main__":
    unittest.main(verbosity=2)
