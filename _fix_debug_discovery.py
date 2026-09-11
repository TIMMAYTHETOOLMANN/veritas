#!/usr/bin/env python3
"""Add debug output to discovery."""
import pathlib

content = pathlib.Path('veritas_engine.py').read_text()

# Add debug after discovery
old = '''                stats["pools_discovered"] = len(discovered)
                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)'''

new = '''                stats["pools_discovered"] = len(discovered)
                stats["discovery_rate_limits"] = discovery_stats.get("rate_limits", 0)
                print(f"[DEBUG] Discovery: {len(discovered)} pools, stats={discovery_stats}", flush=True)'''

content = content.replace(old, new)

pathlib.Path('veritas_engine.py').write_text(content)
print("Added debug output")
