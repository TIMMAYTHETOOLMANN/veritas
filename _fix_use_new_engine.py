#!/usr/bin/env python3
"""Update engine to use new quote engine."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Replace the import
content = content.replace(
    'from core.quote_engine import QuoteEngine, QuoteResult',
    'from core.quote_engine_v2 import QuoteEngine, QuoteResult'
)

pathlib.Path('veritas_engine.py').write_text(content)
print("Updated engine to use new quote engine")
