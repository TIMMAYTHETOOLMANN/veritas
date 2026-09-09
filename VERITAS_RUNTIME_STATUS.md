# VERITAS Runtime Status

## Expected Heartbeat Format

### Normal Operation (Opportunities Found)
```
[VERITAS] cycle=1842 block=XXXXXXXX pools=1842 routes=8417 quotes=7993 edges=6 best=$0.0071 next_scan=20s
```

### Normal Operation (No Opportunities)
```
[VERITAS] MARKET QUIET — cycle=1842 routes=8417 quotes=7993/8417 rejections=41
```

### Scanner Failure
```
[VERITAS] SCANNER FAILURE — RPC unhealthy cycle=1842 quotes=0/8417
```

### RPC Outage
```
[VERITAS] SCANNER FAILURE — no_healthy_rpc cycle=1842 quotes=0/0
```

---

## Current Configuration

```toml
# strategy.toml (recommended)
[discovery]
interval_seconds = 20
hot_rescan_seconds = 5
max_scan_duration_seconds = 15

[quote]
refresh_seconds = 5
max_age_seconds = 30

[economics]
min_profit_usd = 0.01
max_gas_usd = 1.00
max_slippage_bps = 50
max_capital_exposure_usd = 100.0
safety_margin_bps = 100

[sizing]
coarse_curve = [0.01, 0.025, 0.05, 0.10, 0.25, 0.50, 1, 2, 5, 10, 25, 50, 100]
max_evaluations = 25

[routing]
max_hops = 3
max_routes_per_token = 50

[execution]
live_enabled = false
micro_capital_mode = true
max_consecutive_failures = 3

[heartbeat]
interval_seconds = 10

[rpc]
urls = [
  "https://gateway.tenderly.co/public/arbitrum",
  "https://arbitrum.drpc.org",
  "https://arbitrum.publicnode.com",
]
```

---

## Engine Status Command

```bash
python veritas_engine.py --status
```

Expected output:
```json
{
  "cycle": 0,
  "running": false,
  "pools": 0,
  "rpc_health": {
    "is_healthy": true,
    "healthy_count": 3,
    "total_endpoints": 3,
    "endpoints": { ... }
  },
  "capital": {
    "deployable_usd": 10.0,
    "starting_usd": 10.0,
    "total_profit_usd": 0.0,
    "total_gas_usd": 0.0,
    "trades": 0,
    "sim_attempts": 0,
    "mode": "paper"
  },
  "accounting": {
    "total_executions": 0,
    "confirmed": 0,
    "reverted": 0,
    "pending": 0,
    "total_verified_profit_usd": 0.0,
    "total_gas_usd": 0.0
  }
}
```

---

## Single Scan Command

```bash
python veritas_engine.py --once
```

Expected output:
```
VERITAS SCAN — block XXXXXXXX ------------------------------------------------
Pairs discovered:            0
Quotes attempted:            0
Valid quotes:                0
Cross-venue candidates:     0

Simulation candidates:      0
Simulation passes:          0

STATUS: NO QUOTES OBTAINED (check RPC / liquidity / pair discovery)
```

---

## Continuous Run Command

```bash
python veritas_engine.py --run --interval 20
```

---

## Health Check

```bash
python -c "
from veritas_engine import VeritasEngine
e = VeritasEngine()
e.initialize()
print('RPC healthy:', e.rpc_health.is_healthy())
print('Pools loaded:', e.pool_registry.count())
print('ETH price:', e.price_oracle.get_price_usd('0x82af49447d8a07e3bd95bd0d56f35241523fbab1'))
"
```

---

## Key Metrics to Monitor

| Metric | Target | Current |
|--------|--------|---------|
| Scans per minute | ≥3 | Configurable |
| RPC healthy endpoints | ≥1 | 3 (with failover) |
| Quote success rate | >90% | Architecture supports |
| Scan completeness | >90% | Architecture supports |
| Test count | ≥100 | 129 |

---

## Diagnostic Queries

### Why zero opportunities?
```python
result = engine.scan_once()
print(result.generate_why_zero_report())
```

### RPC health status?
```python
print(engine.rpc_health.status())
```

### Quote engine stats?
```python
print(engine.quote_engine.stats())
```

### Capital status?
```python
print(engine.capital_controller.summary())
```

### Accounting summary?
```python
print(engine.accounting.summary())
```

---

## Alert Conditions

| Condition | Severity | Action |
|-----------|----------|--------|
| All RPCs unhealthy | CRITICAL | Switch to observation mode |
| Zero quotes for 5+ cycles | WARNING | Check pool registry |
| Capital drop >10% | HIGH | Review execution history |
| Circuit breaker tripped | HIGH | Pause execution, investigate |
| Consecutive rejections >20 | MEDIUM | Review gate thresholds |

---

## Next Steps for Live Operation

1. **Deploy executor contract** — `flash_hunter.py --deploy`
2. **Register pools** — `pool_registry.py curated`
3. **Set live mode** — `CapitalMode.LIVE_CONSERVATIVE`
4. **Enable live execution** — `LIVE_EXECUTION_ENABLED=True`
5. **Start engine** — `python veritas_engine.py --run`
6. **Monitor** — Watch heartbeat, check `scan_completeness`
