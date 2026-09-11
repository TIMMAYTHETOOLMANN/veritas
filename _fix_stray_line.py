#!/usr/bin/env python3
"""Remove stray gas_already_in_delta line."""
import pathlib

lines = pathlib.Path('core/accounting.py').read_text().split('\n')

# Remove the stray line
new_lines = [line for line in lines if line.strip() != 'gas_already_in_delta INTEGER DEFAULT 1,']

pathlib.Path('core/accounting.py').write_text('\n'.join(new_lines))
print(f"Removed stray line. File now has {len(new_lines)} lines.")
