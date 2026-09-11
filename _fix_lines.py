#!/usr/bin/env python3
"""Fix accounting.py line by line."""
import pathlib

lines = pathlib.Path('core/accounting.py').read_text().split('\n')

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Add gas_already_in_delta to CREATE TABLE
    if 'gas_usd REAL DEFAULT 0,' in line and 'gas_already_in_delta' not in lines[i+1] if i+1 < len(lines) else True:
        new_lines.append(line)
        # Get the indentation
        indent = len(line) - len(line.lstrip())
        new_lines.append(' ' * indent + 'gas_already_in_delta INTEGER DEFAULT 1,')
        i += 1
        continue
    
    # Add migration after the executescript block
    if 'c.commit()' in line and i > 0 and 'executescript' in lines[i-5:i]:
        new_lines.append(line)
        new_lines.append('')
        new_lines.append('            # Migration: add gas_already_in_delta column if missing (existing DBs)')
        new_lines.append('            cols = [r[1] for r in c.execute("PRAGMA table_info(executions)").fetchall()]')
        new_lines.append('            if "gas_already_in_delta" not in cols:')
        new_lines.append('                c.execute("ALTER TABLE executions ADD COLUMN gas_already_in_delta INTEGER DEFAULT 1")')
        i += 1
        continue
    
    new_lines.append(line)
    i += 1

pathlib.Path('core/accounting.py').write_text('\n'.join(new_lines))
print("Fixed accounting.py line by line")
