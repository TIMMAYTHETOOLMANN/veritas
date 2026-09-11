#!/usr/bin/env python3
"""Fix resilient RPC thresholds."""
import pathlib

content = pathlib.Path('core/rpc_resilience.py').read_text()

# Increase threshold for UNHEALTHY status
old = '''    def update_status(self):
        """Update health status based on recent performance."""
        if self.cooldown_until > time.time():
            self.status = HealthStatus.COOLDOWN
        elif self.consecutive_failures >= 5:
            self.status = HealthStatus.UNHEALTHY
        elif self.consecutive_failures >= 2:
            self.status = HealthStatus.DEGRADED
        elif self.rate_limits > 0 and self.successful_requests == 0:
            self.status = HealthStatus.RATE_LIMITED
        else:
            self.status = HealthStatus.HEALTHY'''

new = '''    def update_status(self):
        """Update health status based on recent performance."""
        if self.cooldown_until > time.time():
            self.status = HealthStatus.COOLDOWN
        elif self.consecutive_failures >= 20:
            # Only mark as UNHEALTHY after many consecutive failures
            self.status = HealthStatus.UNHEALTHY
        elif self.consecutive_failures >= 5:
            self.status = HealthStatus.DEGRADED
        elif self.rate_limits > 0 and self.successful_requests == 0:
            self.status = HealthStatus.RATE_LIMITED
        else:
            self.status = HealthStatus.HEALTHY'''

content = content.replace(old, new)

# Also reduce cooldown duration
old_cooldown = '''    def _cooldown_backoff(self, provider_url: str) -> float:
        """Calculate cooldown duration based on consecutive rate limits."""
        health = self._health[provider_url]
        # Exponential backoff: 5s, 10s, 20s, 40s, max 120s
        cooldown = min(5 * (2 ** health.rate_limits), 120)
        return cooldown'''

new_cooldown = '''    def _cooldown_backoff(self, provider_url: str) -> float:
        """Calculate cooldown duration based on consecutive rate limits."""
        health = self._health[provider_url]
        # Exponential backoff: 2s, 4s, 8s, 16s, max 60s
        cooldown = min(2 * (2 ** health.rate_limits), 60)
        return cooldown'''

content = content.replace(old_cooldown, new_cooldown)

pathlib.Path('core/rpc_resilience.py').write_text(content)
print("Fixed resilient RPC thresholds")
