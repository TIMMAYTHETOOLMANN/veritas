#!/usr/bin/env python3
"""Fix test file issues."""
import re

with open('tests/test_veritas_overhaul.py', 'r') as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]

    # Fix: Add pool import and pool to QuoteResult in execution gate tests
    if 'from core.quote_engine import QuoteResult' in line and i > 0:
        # Check if we're in TestExecutionGate or TestEndToEndPipeline
        in_gate_test = False
        for j in range(max(0, i - 20), i):
            if 'TestExecutionGate' in lines[j] or 'TestEndToEndPipeline' in lines[j]:
                in_gate_test = True
                break
        if in_gate_test:
            new_lines.append(line)
            # Add pool import after QuoteResult import
            new_lines.append('        from core.pool import PoolId\n')
            i += 1
            continue

    # Fix: Add pool= to QuoteResult in gate tests
    if 'success=True, price_impact_bps=5,' in line:
        # Look ahead to see if this is followed by timestamp (QuoteResult)
        if i + 1 < len(lines) and 'timestamp=time.time()' in lines[i + 1]:
            new_lines.append(line)
            new_lines.append('            pool=PoolId(\n')
            new_lines.append('                chain_id=42161, venue="uniswap_v2",\n')
            new_lines.append('                factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",\n')
            new_lines.append('                pool_address="0x1234567890abcdef1234567890abcdef12345678"\n')
            new_lines.append('            ),\n')
            i += 1
            continue

    # Fix: Add pool= to QuoteResult with price_impact_bps=100 (slippage test)
    if 'success=True, price_impact_bps=100,' in line:
        new_lines.append(line)
        new_lines.append('            pool=PoolId(\n')
        new_lines.append('                chain_id=42161, venue="uniswap_v2",\n')
        new_lines.append('                factory="0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f",\n')
        new_lines.append('                pool_address="0x1234567890abcdef1234567890abcdef12345678"\n')
        new_lines.append('            ),\n')
        i += 1
        continue

    new_lines.append(line)
    i += 1

with open('tests/test_veritas_overhaul.py', 'w') as f:
    f.writelines(new_lines)

print("Done fixing tests")
