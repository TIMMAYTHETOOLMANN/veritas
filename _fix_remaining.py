#!/usr/bin/env python3
"""Fix remaining test issues."""
import pathlib
import re

p = pathlib.Path('tests/test_veritas_overhaul.py')
content = p.read_text()

# Fix 1: Add PoolId import back to TestPoolIdentity
# Find the first test in TestPoolIdentity and add import
old_import = """    def test_pool_id_is_unique_per_venue(self):
        \"\"\"Two venues with same pair must have different pool IDs.\"\"\"
        from core.pool import PoolId"""
new_import = """    def test_pool_id_is_unique_per_venue(self):
        \"\"\"Two venues with same pair must have different pool IDs.\"\"\"
        from core.pool import PoolId"""
# The import is already there, but PoolId might have been removed by regex
# Let me check if PoolId is imported in TestPoolIdentity
if 'from core.pool import PoolId' not in content[:500]:
    # Add import at the top of TestPoolIdentity
    content = content.replace(
        'class TestPoolIdentity(unittest.TestCase):\n    """Test pool identity',
        'class TestPoolIdentity(unittest.TestCase):\n    """Test pool identity'
    )

# Fix 2: The execution gate tests use input_usd=2500 which exceeds max_capital_exposure_usd=100
# Need to increase max_capital_exposure_usd in the test config
content = content.replace(
    '''self.config = GateConfig(
            max_gas_usd=1.00,
            max_slippage_bps=50,
            max_capital_exposure_usd=100.0,
            min_profit_usd=0.01,
            max_quote_age_seconds=30,
        )''',
    '''self.config = GateConfig(
            max_gas_usd=1.00,
            max_slippage_bps=50,
            max_capital_exposure_usd=5000.0,
            min_profit_usd=0.01,
            max_quote_age_seconds=30,
        )'''
)

# Fix 3: The circuit breaker test sets consecutive_failures but the gate also checks capital_exposure
# Need to set input_usd lower in the circuit breaker test
content = content.replace(
    '''        self.gate._consecutive_failures = self.config.max_consecutive_failures

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=2500.0, gross_output_usd=2505.0,
            expected_net_profit_usd=3.0,
            is_profitable=True, is_worth_executing=True,
        )''',
    '''        self.gate._consecutive_failures = self.config.max_consecutive_failures

        quote = QuoteResult(
            amount_in=10**18, amount_out=2500 * 10**6,
            success=True, price_impact_bps=5,
            timestamp=time.time(), block_number=100,
        )

        economic = EconomicResult(
            input_usd=50.0, gross_output_usd=55.0,
            expected_net_profit_usd=3.0,
            is_profitable=True, is_worth_executing=True,
        )'''
)

p.write_text(content)
print("Fixed remaining test issues")
