#!/usr/bin/env python3
"""Direct fix: add reserve fetching after pool loading."""
import pathlib

lines = pathlib.Path('veritas_engine.py').read_text().split('\n')

# Find the line with "stats[\"pools_seen\"] = len(pools)"
for i, line in enumerate(lines):
    if 'stats["pools_seen"] = len(pools)' in line:
        print(f"Found target at line {i+1}: {line}")
        
        # Insert reserve fetching code after this line
        new_lines = [
            '',
            '            # Fetch reserves for pools with zero reserves',
            '            pools_with_zero_reserves = [p for p in pools if p.reserve0 == 0 and p.reserve1 == 0]',
            '            if pools_with_zero_reserves:',
            '                for pool in pools_with_zero_reserves:',
            '                    if self.market_discovery.fetch_reserves(pool):',
            '                        self.pool_registry.register(pool, persist=True)',
            '                        stats["pools_refreshed"] = stats.get("pools_refreshed", 0) + 1',
        ]
        
        # Insert after the current line
        lines[i+1:i+1] = new_lines
        break

pathlib.Path('veritas_engine.py').write_text('\n'.join(lines))
print("Added reserve fetching code")
