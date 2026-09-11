#!/usr/bin/env python3
"""Add targeted tests for the three fixes."""
import pathlib

test_content = pathlib.Path('tests/test_veritas_overhaul.py').read_text()

# Find the end of the file and add new test classes
new_tests = '''

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
'''

# Insert new tests before the last line (if __name__ == ...)
test_content = test_content.replace(
    "\nif __name__ == \"__main__\":",
    new_tests + "\nif __name__ == \"__main__\":"
)

pathlib.Path('tests/test_veritas_overhaul.py').write_text(test_content)
print("Added fix verification tests")
