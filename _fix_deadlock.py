#!/usr/bin/env python3
"""Fix resilient RPC deadlock."""
import pathlib

content = pathlib.Path('core/rpc_resilience.py').read_text()

# Remove UNHEALTHY status - providers should always be retryable
old = '''    def is_available(self) -> bool:
        """Check if provider is available for use."""
        if self.status == HealthStatus.UNHEALTHY:
            return False
        if self.cooldown_until > time.time():
            return False
        return True'''

new = '''    def is_available(self) -> bool:
        """Check if provider is available for use."""
        # Only check cooldown - never permanently disable a provider
        if self.cooldown_until > time.time():
            return False
        return True'''

content = content.replace(old, new)

pathlib.Path('core/rpc_resilience.py').write_text(content)
print("Fixed resilient RPC deadlock")
