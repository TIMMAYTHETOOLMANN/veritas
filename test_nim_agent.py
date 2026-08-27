"""Smoke-test nim_agent.py: limiter timing, concurrency safety, tool registry."""
import sys, time, threading
sys.path.insert(0, "C:/Users/timot/OneDrive/Documents/VERITAS")
from nim_agent import SlidingWindowRateLimiter, NimAgent

def check_limiter_throughput():
    lim = SlidingWindowRateLimiter(rpm_limit=5, window_seconds=60.0)
    t0 = time.monotonic()
    for _ in range(5):
        lim.wait()           # bullet 5 fire instantly
    t_first5 = time.monotonic() - t0
    t0 = time.monotonic()
    lim.wait()               # 6th must block ~60s
    t_sixth = time.monotonic() - t0
    print(f"  first 5: {t_first5:.3f}s (expect ~0)  |  6th: {t_sixth:.1f}s (expect ~60)")
    assert t_first5 < 2.0, "burst of 5 should not block"
    assert t_sixth >= 59.9, "6th should be blocked by the sliding window"

def check_thread_safety():
    lim = SlidingWindowRateLimiter(rpm_limit=10, window_seconds=60.0)
    errors = []
    def spinner():
        try:
            for _ in range(10):
                lim.wait()
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=spinner) for _ in range(6)]
    for t in threads: t.start()
    t0 = time.monotonic()
    for t in threads: t.join(timeout=20)
    elapsed = time.monotonic() - t0
    print(f"  threads completed in {elapsed:.1f}s, errors={errors}")
    assert not errors, f"lock leaked / deadlocked: {errors}"
    # 6 threads * 10 = 60 wants from a window of 10 -> must take >= 50s, NOT hang
    assert elapsed >= 49, "60 wants through a 10 rpm window must be throttled"
    assert elapsed < 65, "should finish after ~50-60s, not deadlock forever"

def check_registry_no_stub():
    a = NimAgent(api_key="test", rpm_limit=100)
    seen = {}
    a.register("scan_edges", lambda limit: {"edges": int(limit)})
    out = a._execute_tool("scan_edges", {"limit": 7})
    assert out == {"edges": 7}, out
    try:
        a._execute_tool("get_weather", {})
        raise AssertionError("unknown tool must raise, not fake a result")
    except ValueError:
        pass
    print("  registry dispatch + unknown-tool rejection OK")

print("[1] limiter throughput")
check_limiter_throughput()
print("[2] concurrency / no deadlock")
# real 60s timing for one case; run it but tolerate it taking the expected time
check_thread_safety()
print("[3] tool registry (no stub)")
check_registry_no_stub()
print("ALL OK")