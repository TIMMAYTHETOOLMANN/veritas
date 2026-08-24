# VERITAS — Engine Room

Arbitrum flash-loan arbitrage system. Atomic, zero-capital-at-risk:
borrow via Aave V3 → swap across venues → repay, all in one tx.
Failure reverts whole; only gas is ever at risk.

## Layout (reorganized 2026-08-24 — keep it this way)

```
flash_hunter.py        ACTIVE loop: scan → sim-gate → broadcast → verify
arb_engine.py          Scan layer (read-only, $0): cross-venue dislocations
arb_engine_registry.py Scan layer over pool registry (V3 + V2 census)
pool_registry.py       Pool census → veritas.db (Camelot + UniV3 + Sushi)
sim_gate.py            Fork-sim gate — NOTHING is signed before this passes
v3_layer.py            Uniswap V3 QuoterV2 exact-quote layer
verify_arb_venues.py   On-chain address verification (read-only)
heartbeat_monitor.py   Forensic status: engine PID, log, executor, wallet, DB
core/                  Shared library (rpc, selectors, config...) — DO NOT move
contracts/             Executor Solidity sources + ABIs
veritas.db             Pool registry + census progress (gitignored, precious)
.executor_v2_address   ACTIVE executor V4 (authoritative)
.hot_secret            Hot wallet key (gitignored, NEVER print)
hyperliquid/           ARCHIVED: closed HL perp chapter (drained 2026-08-23)
zk-scanner/            ARCHIVED: original ZK/Railgun vuln-scanner era
archive/logs/          Historical census/sentinel logs
```

## Operations

- Run engine:    `python3 flash_hunter.py --run` (180s cycles)
- Heartbeat:     `python3 heartbeat_monitor.py`
- Pool census:   `python3 pool_registry.py` (auto-resumes from DB)
- Watchdogs (cron): ec2016bb9a90 (engine restart, */30) +
                    735a11892875 (self-heal health, */30, alerts on degrade)

## Rules

1. Nothing signs without the fork-sim gate passing first.
2. Profit is only ever claimed from on-chain balance deltas.
3. Executor = `.executor_v2_address` contents. Older executors superseded.
4. Run engine scripts with `python3` (Python 3.12 has the SDK).
5. Sibling sessions edit this repo — re-read files before patching.
6. New scripts go in an appropriate directory, never loose in root.
