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

- Run engine:    `python3 flash_hunter.py --run` (target 15s cadence;
                 sleep covers only the remainder after each cycle's work)
- Tune cadence:  `python3 flash_hunter.py --run --interval 30`
- Heartbeat:     `python3 heartbeat_monitor.py`
- Pool census:   `python3 pool_registry.py` (auto-resumes from DB)
- Watchdogs (cron): ec2016bb9a90 (engine restart, */30) +
                    735a11892875 (self-heal health, */30, alerts on degrade)

## Hunt pipeline (2026-08-27 throughput overhaul)

Every cycle (15s target; actual cycle length is scan-bound): scan →
cross-RPC confirmation → batch fork-sim → broadcast best PASS. Every
cycle writes a vetted result to `vetted_targets.jsonl` (top candidates,
sim verdicts, broadcast status).

- Scan: V2 pool-state cache (4s TTL) + 5s quoter cache → ~10x fewer RPC calls
- Confirmation: edges re-verified on 3 RPCs in parallel (2-of-3 vote);
  the old serial pass had a NameError that silently dropped EVERY edge
- Vet: ONE anvil fork per cycle, evm_snapshot/evm_revert between sims,
  up to 6 edges per cycle, best-net first
- Gate: profit > 1.0x gas AND > $0.05 net (aligned across flash_hunter
  + sim_gate; scanner SAFETY_MARGIN_USD = $0.10, dislocation filter 25bps)
- Cadence: elapsed-based sleep keeps the loop on the 15s target
- Scan RPC rotates each cycle to dodge per-endpoint rate limits
- 3-pool multi-hop removed: zero-reserve dead code; the 2-leg executor
  cannot execute 3-pool routes anyway

## Rules

1. Nothing signs without the fork-sim gate passing first.
2. Profit is only ever claimed from on-chain balance deltas.
3. Executor = `.executor_v2_address` contents. Older executors superseded.
4. Run engine scripts with `python3` (Python 3.12 has the SDK).
5. Sibling sessions edit this repo — re-read files before patching.
6. New scripts go in an appropriate directory, never loose in root.
