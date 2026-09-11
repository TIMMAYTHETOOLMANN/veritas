#!/usr/bin/env python3
"""Fix encoding issue - replace em dashes with ASCII."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace em dashes with double dashes
content = content.replace('\u2014', '--')
content = content.replace('\u2013', '-')

pathlib.Path('veritas_engine.py').write_text(content)
print("Fixed encoding issues")
