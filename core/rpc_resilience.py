#!/usr/bin/env python3
"""
core/rpc_resilience.py — VERITAS RPC resilience layer.

Provides:
- Health-aware provider failover
- Rate-limit detection and cooldown
- Block-aware RPC caching
- Structured failure classification
- Provider health telemetry
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.rpc import RPC


class FailureType(Enum):
    """Classification of RPC failures."""
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    HTTP_ERROR = "http_error"
    RPC_ERROR = "rpc_error"
    REVERT = "revert"
    INVALID_RESPONSE = "invalid_response"
    SUCCESS = "success"


class HealthStatus(Enum):
    """Provider health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RATE_LIMITED = "rate_limited"
    COOLDOWN = "cooldown"
    UNHEALTHY = "unhealthy"


# Patterns that indicate rate limiting
_RATE_LIMIT_PATTERNS = [
    "rate limit", "usage limit", "too many requests", "429",
    "throttled", "quota exceeded", "limit reached",
]


def _classify_failure(error: Exception) -> FailureType:
    """Classify an exception into a failure type."""
    error_str = str(error).lower()
    
    # Check for rate limiting first
    for pattern in _RATE_LIMIT_PATTERNS:
        if pattern in error_str:
            return FailureType.RATE_LIMITED
    
    # Timeout
    if "timeout" in error_str or "timed out" in error_str:
        return FailureType.TIMEOUT
    
    # Connection errors
    if "connection" in error_str or "unreachable" in error_str or "refused" in error_str:
        return FailureType.CONNECTION_ERROR
    
    # HTTP errors (500, etc.)
    if "http error" in error_str or "500" in error_str or "502" in error_str or "503" in error_str:
        return FailureType.HTTP_ERROR
    
    # EVM revert (this is NOT an RPC failure)
    if "execution reverted" in error_str:
        return FailureType.REVERT
    
    # JSON-RPC errors
    if "jsonrpc" in error_str or "rpc" in error_str:
        return FailureType.RPC_ERROR
    
    return FailureType.RPC_ERROR


@dataclass
class ProviderHealth:
    """Health state for a single RPC provider."""
    url: str
    status: HealthStatus = HealthStatus.HEALTHY
    requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rate_limits: int = 0
    timeouts: int = 0
    last_success: float = 0.0
    last_failure: float = 0.0
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    avg_latency_ms: float = 0.0
    _latency_samples: List[float] = field(default_factory=list)
    
    def record_success(self, latency_ms: float):
        """Record a successful request."""
        self.requests += 1
        self.successful_requests += 1
        self.last_success = time.time()
        self.consecutive_failures = 0
        self._latency_samples.append(latency_ms)
        # Keep last 100 samples
        if len(self._latency_samples) > 100:
            self._latency_samples = self._latency_samples[-100:]
        self.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)
    
    def record_failure(self, failure_type: FailureType):
        """Record a failed request."""
        self.requests += 1
        self.failed_requests += 1
        self.last_failure = time.time()
        self.consecutive_failures += 1
        
        if failure_type == FailureType.RATE_LIMITED:
            self.rate_limits += 1
        elif failure_type == FailureType.TIMEOUT:
            self.timeouts += 1
    
    def is_available(self) -> bool:
        """Check if provider is available for use."""
        # Only check cooldown - never permanently disable a provider
        if self.cooldown_until > time.time():
            return False
        return True
    
    def set_cooldown(self, seconds: float):
        """Set cooldown period."""
        self.cooldown_until = time.time() + seconds
        self.status = HealthStatus.COOLDOWN
    
    def update_status(self):
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
            self.status = HealthStatus.HEALTHY
    
    def to_dict(self) -> dict:
        return {
            "url": self.url[:50] + "..." if len(self.url) > 50 else self.url,
            "status": self.status.value,
            "requests": self.requests,
            "successful": self.successful_requests,
            "failed": self.failed_requests,
            "rate_limits": self.rate_limits,
            "timeouts": self.timeouts,
            "consecutive_failures": self.consecutive_failures,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "cooldown_remaining": max(0, round(self.cooldown_until - time.time(), 1)),
        }


@dataclass
class CacheEntry:
    """A cached RPC result."""
    value: Any
    block_number: int
    timestamp: float


class RPCCache:
    """
    Block-aware RPC cache.
    
    Caches results keyed by (method, params, block_number).
    Same block -> reuse result.
    New block -> refresh.
    """
    
    def __init__(self, max_size: int = 1000):
        self._cache: Dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
    
    def _make_key(self, method: str, params: tuple, block_number: int) -> str:
        """Create a cache key."""
        return f"{method}:{params}:{block_number}"
    
    def get(self, method: str, params: tuple, block_number: int) -> Optional[Any]:
        """Get cached result if available and valid."""
        key = self._make_key(method, params, block_number)
        with self._lock:
            entry = self._cache.get(key)
            if entry and entry.block_number == block_number:
                self._hits += 1
                return entry.value
            self._misses += 1
            return None
    
    def put(self, method: str, params: tuple, block_number: int, value: Any):
        """Cache a result."""
        key = self._make_key(method, params, block_number)
        with self._lock:
            self._cache[key] = CacheEntry(value, block_number, time.time())
            # Evict oldest if over capacity
            if len(self._cache) > self._max_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k].timestamp)
                del self._cache[oldest_key]
    
    def invalidate_block(self, block_number: int):
        """Invalidate all entries for a specific block."""
        with self._lock:
            keys_to_remove = [k for k, v in self._cache.items() if v.block_number == block_number]
            for key in keys_to_remove:
                del self._cache[key]
    
    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
    
    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
        }


class ResilientRPC:
    """
    Resilient RPC client with health-aware failover and caching.
    
    Features:
    - Automatic failover between providers
    - Rate-limit detection and cooldown
    - Block-aware caching
    - Failure classification
    - Health telemetry
    """
    
    def __init__(self, providers: List[Tuple[str, int]], cache: Optional[RPCCache] = None):
        """
        Initialize resilient RPC.
        
        Args:
            providers: List of (url, priority) tuples. Lower priority = preferred.
            cache: Optional RPC cache instance.
        """
        # Sort by priority (lower = preferred)
        self._providers = sorted(providers, key=lambda x: x[1])
        self._rpcs: Dict[str, RPC] = {}
        self._health: Dict[str, ProviderHealth] = {}
        
        for url, priority in self._providers:
            self._rpcs[url] = RPC(url, timeout=30, retries=0)
            self._health[url] = ProviderHealth(url=url)
        
        self._cache = cache or RPCCache()
        self._current_block = 0
    
    def _get_available_providers(self) ->[List[str]]:
        """Get list of available provider URLs in priority order."""
        available = []
        for url, priority in self._providers:
            health = self._health[url]
            health.update_status()
            if health.is_available():
                available.append(url)
        return available
    
    def _cooldown_backoff(self, provider_url: str) -> float:
        """Calculate cooldown duration based on consecutive rate limits."""
        health = self._health[provider_url]
        # Exponential backoff: 2s, 4s, 8s, 16s, max 60s
        cooldown = min(2 * (2 ** health.rate_limits), 60)
        return cooldown
    
    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        """
        Make an eth_call with automatic failover.
        
        Args:
            to: Contract address
            data: Calldata
            block: Block number or "latest"
            
        Returns:
            Result hex string
            
        Raises:
            RuntimeError: If all providers fail
        """
        # Determine block number for caching
        if block == "latest":
            block_num = self._current_block
        else:
            block_num = int(block, 16) if block.startswith("0x") else int(block)
        
        # Check cache first
        cache_key = ("eth_call", (to.lower(), data, block_num))
        cached = self._cache.get("eth_call", (to.lower(), data, block_num), block_num)
        if cached is not None:
            return cached
        
        # Try each provider in priority order
        last_error = None
        for url in self._get_available_providers():
            rpc = self._rpcs[url]
            health = self._health[url]
            
            start = time.time()
            try:
                result = rpc.eth_call(to, data, block=block)
                latency = (time.time() - start) * 1000
                
                # Record success
                health.record_success(latency)
                
                # Cache the result
                if result and result != "0x":
                    self._cache.put("eth_call", (to.lower(), data, block_num), block_num, result)
                
                return result
                
            except Exception as e:
                latency = (time.time() - start) * 1000
                failure_type = _classify_failure(e)
                
                # Record failure
                health.record_failure(failure_type)
                last_error = e
                
                # Apply cooldown for rate limits
                if failure_type == FailureType.RATE_LIMITED:
                    cooldown = self._cooldown_backoff(url)
                    health.set_cooldown(cooldown)
                    continue
                
                # For other failures, try next provider
                continue
        
        # All providers failed
        raise RuntimeError(f"All RPC providers failed. Last error: {last_error}")
    
    def eth_blockNumber(self) -> int:
        """Get current block number with failover."""
        # Always try to get fresh block number (don't cache)
        for url in self._get_available_providers():
            rpc = self._rpcs[url]
            health = self._health[url]
            
            try:
                result = rpc.eth_blockNumber()
                health.record_success(0)
                self._current_block = result
                return result
            except Exception as e:
                health.record_failure(_classify_failure(e))
                continue
        
        raise RuntimeError("All RPC providers failed for eth_blockNumber")
    
    def get_healthy_rpc(self) -> RPC:
        """Get the current healthy primary RPC."""
        for url in self._get_available_providers():
            return self._rpcs[url]
        raise RuntimeError("No healthy RPC providers")
    
    def health_status(self) -> List[dict]:
        """Get health status for all providers."""
        return [h.to_dict() for h in self._health.values()]
    
    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return self._cache.stats()
