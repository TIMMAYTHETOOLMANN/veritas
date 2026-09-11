#!/usr/bin/env python3
"""Add PoolId import back to TestPoolIdentity."""
import pathlib

p = pathlib.Path('tests/test_veritas_overhaul.py')
content = p.read_text()

# Add import to test_pool_id_is_unique_per_venue
content = content.replace(
    '''    def test_pool_id_is_unique_per_venue(self):
        """Two venues with same pair must have different pool IDs."""

        pool_a = PoolId(''',
    '''    def test_pool_id_is_unique_per_venue(self):
        """Two venues with same pair must have different pool IDs."""
        from core.pool import PoolId

        pool_a = PoolId('''
)

# Add import to test_pool_id_case_insensitive
content = content.replace(
    '''    def test_pool_id_case_insensitive(self):
        """Pool addresses should be compared case-insensitively."""

        pool_a = PoolId(''',
    '''    def test_pool_id_case_insensitive(self):
        """Pool addresses should be compared case-insensitively."""
        from core.pool import PoolId

        pool_a = PoolId('''
)

p.write_text(content)
print("Added PoolId imports back")
