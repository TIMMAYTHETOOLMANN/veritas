"""Fast smoke-test nim_agent.py: limiter timing, thread safety, tool registry.
Uses a short sliding window so throttling is provable in ~2s, not ~120s.
"""
import sys, time, threading
sys.path.insert(0, "C:/Users/timot/OneDrive/Documents/VERITAS")
from nim_agent import SlidingWindowRateLimiter, NimAgent

def check_throughput():
    lim = SlidingWindowRateLimiter(rpm_limit=3, window_seconds=2.0)
    t0 = time.monotonic()
    lim.wait(); lim.wait(); lim.wait()          # burst of 3 -> instant
    t_burst = time.monotonic() - t0
    t0 = time.monotonic()
    lim.wait()                                   # 4th must be blocked ~2s
    t_blocked = time.monotonic() - t0
    print(f"  burst(3): {t_burst:.3f}s (expect ~0) | 4th: {t_blocked:.2f}s (expect ~2)")
    assert t_burst < 1.0, "burst should not block"
    assert t_blocked >= 1.9, "4th request must be gated by the sliding window"
    print("  OK")

def check_thread_safety():
    lim = SlidingWindowRateLimiter(rpm_limit=5, window_seconds=2.0)
    errors = []
    def spinner():
        try:
            for _ in range(8):
                lim.wait()
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=spinner) for _ in range(6)]
    for t in threads: t.start()
    t0 = time.monotonic()
    for t in threads: t.join(timeout=30)
    elapsed = time.monotonic() - t0
    print(f"  6 threads x 8 wants, 5rpm/2s window: finished in {elapsed:.1f}s, errors={errors}")
    assert not errors, f"lock leaked/deadlocked: {errors}"
    # 48 wants, window of 5 per 2s -> must take well more than the 10 fastest would
    assert elapsed >= 10, "should be throttled, not burst through"
    print("  OK (no deadlock)")

def check_registry_no_stub():
    a = NimAgent(api_key="test", rpm_limit=100)
    a.register("scan_edges", lambda limit: {"edges": int(limit)})
    out = a._execute_tool("scan_edges", {"limit": 7})
    assert out == {"edges": 7}, out
    try:
        a._execute_tool("get_weather", {})   # the old paste's stub tool
        raise AssertionError("unknown tool must raise, not fake a result")
    except ValueError:
        pass
    print("  registry dispatch + unknown-tool rejection OK")

print("[1] sliding-window throughput"); check_throughput()
print("[2] concurrency / no deadlock"); check_thread_safety()
print("[3] tool registry (no stub)"); check_registry_no_stub()
print("ALL OK")