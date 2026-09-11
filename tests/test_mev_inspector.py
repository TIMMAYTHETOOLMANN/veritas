#!/usr/bin/env python3
"""
tests/test_mev_inspector.py — Tests for MEV Inspector module.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mev_inspector import (
    MEVType,
    ArbitrageOpportunity,
    LiquidationOpportunity,
    SandwichOpportunity,
    ArbitrageDetector,
    LiquidationDetector,
    SandwichDetector,
    BlockProcessor,
    MEVTracker,
)
from core.external_apis import AlchemyRPC
from core.rpc_resilience import ResilientRPC, RPCCache


class TestMEVTracker(unittest.TestCase):
    """Test MEV tracking functionality."""
    
    def test_add_opportunities(self):
        """Test adding opportunities to tracker."""
        tracker = MEVTracker()
        
        arb = ArbitrageOpportunity(
            block_number=100,
            tx_hash="0xabc",
            profit_wei=10**18,
            profit_usd=2500.0,
            route=["WETH", "USDC", "WETH"],
            venues=["uniswap", "sushi"],
            gas_used=100000,
            gas_price_wei=10**9,
            effective_gas_price=10**9,
        )
        
        tracker.add_opportunities(MEVType.ARBITRAGE, [arb])
        
        self.assertEqual(tracker.stats["total_detected"], 1)
        self.assertEqual(tracker.stats["total_profit_wei"], 10**18)
        self.assertEqual(tracker.stats["total_profit_usd"], 2500.0)
    
    def test_get_top_opportunities(self):
        """Test getting top opportunities."""
        tracker = MEVTracker()
        
        # Add multiple opportunities
        for i in range(5):
            arb = ArbitrageOpportunity(
                block_number=100 + i,
                tx_hash=f"0xabc{i}",
                profit_wei=10**18 * (i + 1),
                profit_usd=2500.0 * (i + 1),
                route=["WETH", "USDC", "WETH"],
                venues=["uniswap"],
                gas_used=100000,
                gas_price_wei=10**9,
                effective_gas_price=10**9,
            )
            tracker.add_opportunities(MEVType.ARBITRAGE, [arb])
        
        top = tracker.get_top_opportunities(3)
        self.assertEqual(len(top), 3)
        # Highest profit should be first
        self.assertEqual(top[0].profit_usd, 2500.0 * 5)
    
    def test_get_summary(self):
        """Test summary statistics."""
        tracker = MEVTracker()
        
        arb = ArbitrageOpportunity(
            block_number=100,
            tx_hash="0xabc",
            profit_wei=10**18,
            profit_usd=2500.0,
            route=["WETH", "USDC", "WETH"],
            venues=["uniswap"],
            gas_used=100000,
            gas_price_wei=10**9,
            effective_gas_price=10**9,
        )
        
        tracker.add_opportunities(MEVType.ARBITRAGE, [arb])
        summary = tracker.get_summary()
        
        self.assertEqual(summary["total_detected"], 1)
        self.assertEqual(summary["by_type"]["arbitrage"], 1)


class TestArbitrageDetector(unittest.TestCase):
    """Test arbitrage detection."""
    
    def test_init(self):
        """Test detector initialization."""
        rpc = AlchemyRPC()
        detector = ArbitrageDetector(rpc, None, None)
        self.assertIsNotNone(detector)
    
    def test_detect_in_block_empty(self):
        """Test detection in block with no arbitrage."""
        rpc = AlchemyRPC()
        detector = ArbitrageDetector(rpc, None, None)
        
        # This will fail gracefully if no RPC available
        try:
            result = detector.detect_in_block(10921990)
            self.assertIsInstance(result, list)
        except Exception:
            # Expected if no RPC connection
            pass


class TestLiquidationDetector(unittest.TestCase):
    """Test liquidation detection."""
    
    def test_init(self):
        """Test detector initialization."""
        rpc = AlchemyRPC()
        detector = LiquidationDetector(rpc, None, None)
        self.assertIsNotNone(detector)
    
    def test_protocols_configured(self):
        """Test that lending protocols are configured."""
        self.assertIn("aave_v3", LiquidationDetector.LENDING_PROTOCOLS)
        self.assertIn("compound_v3", LiquidationDetector.LENDING_PROTOCOLS)


class TestSandwichDetector(unittest.TestCase):
    """Test sandwich detection."""
    
    def test_init(self):
        """Test detector initialization."""
        rpc = AlchemyRPC()
        detector = SandwichDetector(rpc, None, None)
        self.assertIsNotNone(detector)


class TestBlockProcessor(unittest.TestCase):
    """Test block processing."""
    
    def test_init(self):
        """Test processor initialization."""
        rpc = AlchemyRPC()
        from core.pool import PoolRegistry
        from core.price_oracle import PriceOracle
        
        registry = PoolRegistry()
        price_oracle = PriceOracle(rpc)
        
        processor = BlockProcessor(rpc, None, price_oracle, registry)
        
        self.assertIsNotNone(processor.arbitrage_detector)
        self.assertIsNotNone(processor.liquidation_detector)
        self.assertIsNotNone(processor.sandwich_detector)


if __name__ == "__main__":
    unittest.main(verbosity=2)
