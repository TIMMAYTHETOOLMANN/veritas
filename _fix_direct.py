#!/usr/bin/env python3
"""Direct fix for accounting.py."""
import pathlib

content = pathlib.Path('core/accounting.py').read_text()

# Find and replace the specific section
# Look for the pattern: gas_usd REAL DEFAULT 0,\n                    created_at
old_pattern = 'gas_usd REAL DEFAULT 0,\n                    created_at INTEGER DEFAULT 0,'
new_pattern = 'gas_usd REAL DEFAULT 0,\n                    gas_already_in_delta INTEGER DEFAULT 1,\n                    created_at INTEGER DEFAULT 0,'

content = content.replace(old_pattern, new_pattern)

# Find and replace the c.commit() after executescript
old_commit = '''            """)
            c.commit()
        finally:
            c.close()

    def record_execution'''

new_commit = '''            """)
            # Migration: add gas_already_in_delta column if missing (existing DBs)
            cols = [r[1] for r in c.execute("PRAGMA table_info(executions)").fetchall()]
            if "gas_already_in_delta" not in cols:
                c.execute("ALTER TABLE executions ADD COLUMN gas_already_in_delta INTEGER DEFAULT 1")
            c.commit()
        finally:
            c.close()

    def record_execution'''

content = content.replace(old_commit, new_commit)

pathlib.Path('core/accounting.py').write_text(content)
print("Direct fix applied")
