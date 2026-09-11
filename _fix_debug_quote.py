#!/usr/bin/env python3
"""Add debug output to quote evaluation."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Add debug after quote
old = '''            quote = self.quote_engine.quote(
                first_step.token_in, first_step.token_out, amount_in, pool
            )

            if not quote.is_valid:
                stats["quotes_failed"] += 1
                candidate.reject(RejectionReason.NO_QUOTE)
                return candidate'''

new = '''            quote = self.quote_engine.quote(
                first_step.token_in, first_step.token_out, amount_in, pool
            )

            print(f"[DEBUG] Quote {first_step.token_in[:6]}->{first_step.token_out[:6]}: success={quote.success}, out={quote.amount_out}, error={quote.error}", flush=True)

            if not quote.is_valid:
                stats["quotes_failed"] += 1
                candidate.reject(RejectionReason.NO_QUOTE)
                return candidate'''

content = content.replace(old, new)

pathlib.Path('veritas_engine.py').write_text(content)
print("Added debug output to quote")
