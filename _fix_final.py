#!/usr/bin/env python3
"""Final fix for accounting.py."""
import pathlib

content = pathlib.Path('core/accounting.py').read_text()

# 1. Add gas_already_in_delta column to CREATE TABLE
old_col = '                    gas_usd REAL DEFAULT 0,\n                    created_at INTEGER DEFAULT 0,'
new_col = '                    gas_usd REAL DEFAULT 0,\n                    gas_already_in_delta INTEGER DEFAULT 1,\n                    created_at INTEGER DEFAULT 0,'
content = content.replace(old_col, new_col)

# 2. Add migration after executescript
old_migration = '            """)\n            c.commit()\n        finally:\n            c.close()'
new_migration = '''            """)
            # Migration: add gas_already_in_delta column if missing (existing DBs)
            cols = [r[1] for r in c.execute("PRAGMA table_info(executions)").fetchall()]
            if "gas_already_in_delta" not in cols:
                c.execute("ALTER TABLE executions ADD COLUMN gas_already_in_delta INTEGER DEFAULT 1")
            c.commit()
        finally:
            c.close()'''
content = content.replace(old_migration, new_migration)

pathlib.Path('core/accounting.py').write_text(content)

# Verify
verify = pathlib.Path('core/accounting.py').read_text()
if 'gas_already_in_delta' in verify and 'ALTER TABLE' in verify:
    print("Fix verified: gas_already_in_delta column and migration added")
else:
    print("WARNING: Fix may not have applied correctly")
    print("gas_already_in_delta found:", 'gas_already_in_delta' in verify)
    print("ALTER TABLE found:", 'ALTER TABLE' in verify)
