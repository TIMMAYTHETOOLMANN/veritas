#!/usr/bin/env python3
"""
core/rpc_health.py — VERITAS RPC health monitor.

Tracks per-endpoint:
  - latency
  - error rate
  - timeouts
  - block freshness
  - eth_call failures
  - rate-limit failures

Supports multiple RPC endpoints with health-based failover:
  primary -> secondary -> tertiary

A single dead RPC must not kill opportunity discovery.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.rpc import RPC


@dataclass
class EndpointHealth:
    """Health metrics for a single RPC endpoint."""
    url: str
    is_healthy: bool = True
    last_check: float = 0.0
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    total_requests: int = 0
    total_errors: int = 0
    total_timeouts: int = 0
    avg_latency_ms: float = 0.0
    last_block_number: int = 0
    last_block_timestamp: float = 0.0

    # Thresholds
    max_consecutive_errors: int = 3
    max_avg_latency_ms: float = 5000.0
    max_block_age_seconds: float = 120.0

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    def record_success(self, latency_ms: float, block_number: int = 0) -> None:
        """Record a successful request."""
        self.total_requests += 1
        self.consecutive_errors = 0
        self.is_healthy = True
        self.last_check = time.time()
        # Exponential moving average for latency
        alpha = 0.3
        self.avg_latency_ms = (alpha * latency_ms +
                               (1 - alpha) * self.avg_latency_ms)
        if block_number > 0:
            self.last_block_number = block_number
            self.last_block_timestamp = time.time()

    def record_error(self, error_msg: str, is_timeout: bool = False) -> None:
        """Record a failed request."""
        self.total_requests += 1
        self.total_errors += 1
        self.consecutive_errors += 1
        self.last_error = error_msg
        self.last_check = time.time()
        if is_timeout:
            self.total_timeouts += 1
        if self.consecutive_errors >= self.max_consecutive_errors:
            self.is_healthy = False

    def check_block_freshness(self) -> bool:
        """Check if the last known block is fresh enough."""
        if self.last_block_timestamp == 0:
            return True  # No data yet
        age = time.time() - self.last_block_timestamp
        return age <= self.max_block_age_seconds

    def to_dict(self) -> dict:
        return {
            "url": self.url[:50] + "..." if len(self.url) > 50 else self.url,
            "is_healthy": self.is_healthy,
            "error_rate": round(self.error_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "consecutive_errors": self.consecutive_errors,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "total_timeouts": self.total_timeouts,
            "last_block": self.last_block_number,
        }


class RPCHealthMonitor:
    """
    Monitors health of multiple RPC endpoints with automatic failover.

    Usage:
        monitor = RPCHealthMonitor(["https://rpc1", "https://rpc2"])
        healthy_rpc = monitor.get_healthy_rpc()
        # ... use rpc ...
        monitor.record_success(latency_ms, block)
    """

    def __init__(
        self,
        urls: List[str],
        check_interval_seconds: float = 30.0,
        max_consecutive_errors: int = 3,
    ):
        self.endpoints: Dict[str, EndpointHealth] = {}
        self.check_interval = check_interval_seconds
        self._last_health_check: float = 0.0

        for url in urls:
            self.endpoints[url] = EndpointHealth(
                url=url,
                max_consecutive_errors=max_consecutive_errors,
            )

    def get_healthy_rpc(self) -> Optional[RPC]:
        """
        Get a healthy RPC client, preferring the most responsive endpoint.

        Returns None if no endpoints are healthy.
        """
        healthy = [e for e in self.endpoints.values() if e.is_healthy]
        if not healthy:
            # All unhealthy — try the one with fewest consecutive errors
            least_bad = min(self.endpoints.values(),
                          key=lambda e: e.consecutive_errors)
            return RPC(least_bad.url, timeout=20, retries=1)

        # Sort by latency (prefer fastest)
        healthy.sort(key=lambda e: e.avg_latency_ms)
        best = healthy[0]
        return RPC(best.url, timeout=20, retries=2)

    def get_healthy_urls(self) -> List[str]:
        """Get list of healthy endpoint URLs."""
        return [e.url for e in self.endpoints.values() if e.is_healthy]

    def record_success(self, url: str, latency_ms: float, block_number: int = 0) -> None:
        """Record a successful request to an endpoint."""
        if url in self.endpoints:
            self.endpoints[url].record_success(latency_ms, block_number)

    def record_error(self, url: str, error_msg: str, is_timeout: bool = False) -> None:
        """Record a failed request to an endpoint."""
        if url in self.endpoints:
            self.endpoints[url].record_error(error_msg, is_timeout)

    def is_healthy(self) -> bool:
        """Check if at least one endpoint is healthy."""
        return any(e.is_healthy for e in self.endpoints.values())

    def health_check(self) -> Dict[str, bool]:
        """
        Run a health check on all endpoints.

        Returns dict of {url: is_healthy}.
        """
        results = {}
        for url, health in self.endpoints.items():
            try:
                start = time.time()
                rpc = RPC(url, timeout=10, retries=0)
                block = rpc.eth_blockNumber()
                latency = (time.time() - start) * 1000
                health.record_success(latency, block)
                results[url] = True
            except Exception as e:
                health.record_error(str(e))
                results[url] = False
        self._last_health_check = time.time()
        return results

    def status(self) -> dict:
        """Get full health status."""
        return {
            "is_healthy": self.is_healthy(),
            "healthy_count": sum(1 for e in self.endpoints.values() if e.is_healthy),
            "total_endpoints": len(self.endpoints),
            "endpoints": {url: ep.to_dict() for url, ep in self.endpoints.items()},
        }
