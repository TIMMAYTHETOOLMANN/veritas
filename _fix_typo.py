#!/usr/bin/env python3
"""Fix the typo in execution_gate.py."""
import pathlib

p = pathlib.Path('core/execution_gate.py')
content = p.read_text()

# Fix the typo
content = content.replace('from dataclass import dataclass, field', 'from dataclasses import dataclass, field')

p.write_text(content)
print("Fixed typo in execution_gate.py")
