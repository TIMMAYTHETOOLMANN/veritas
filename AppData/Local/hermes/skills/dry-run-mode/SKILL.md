# dry-run-mode

**Trigger:** Use when user requests a read-only diagnostic scan of Arbitrum flash-loan arbitrage opportunities without broadcasting any transactions.

**One-line behavior:** Runs a full read-only scan of the VERITAS pool registry, collects edges with profit above a threshold, and prints a human-readable summary report — no transactions broadcast, no signatures made.

## Overview

The `--dry-run` mode provides a confidence-inspecting window into what the VERITAS engine is discovering across the Arbitrum pool ecosystem. This is essential for:

- **Debugging**: Verify the system is finding edges before enabling execution
- **Health monitoring**: Continuous check that the scan is producing results
- **Parameter tuning**: Test different profit thresholds without risk
- **Market analysis**: See what opportunities exist at different profit levels

### Key Features

- **Pure read-only operation** — No transactions are broadcast, no signatures are made, no gas spent
- **Reuses existing functions** — Leverages `scan_cross_venue()` and `scan_once()` from `arb_engine.py`
- **Configurable thresholds** — `--min-profit` defaults to $0.01, adjustable via flag
- **Top-N display** — `--top-n` defaults to 5, shows best routes with profit/gas/pool info
- **DB-first scanning** — Primary mode reads from `veritas.db` pool registry (no RPC calls needed)
- **RPC fallback** — Falls back to `scan_cross_venue()` when DB data is insufficient
- **Output formats** — Console with colored tables (tabulate optional), or JSON for machine parsing

## Usage

```bash
# Via standalone script
python3 dry_run_mode.py --rpc https://arb1.arbitrum.io/rpc --min-profit 0.01 --top-n 5

# Via main flash_hunter.py
python flash_hunter.py --dry-run
```

## Report Output

The script prints a summary including:

- Total edges scanned
- Profitable edges found (above threshold)
- Top N opportunities with:
  - Route description
  - Expected profit (after gas)
  - Gas cost estimate
  - Pools involved

Example output:

```
🔍 VERITAS DRY RUN — 2026-08-26T18:53:21.277714
RPC: https://arb1.arbitrum.io/rpc
------------------------------------------------------------
✅ Total edges scanned: 127
💰 Profitable edges (>0.01 USD): 12

🏆 Top 5 opportunities:
 #  Route              Profit    Gas  Pool
 1  WETH/USDC -> Uniswap V2  $1.2345  0
 2  WETH/USDCE -> Sushi V3  $0.9876  0
 3  WETH/USDC -> Uniswap V2  $0.5432  0
 4  WETH/USDCE -> Sushi V3  $0.3210  0
 5  WETH/USDC -> Uniswap V2  $0.1987  0

✅ Dry run complete — no transactions were sent.
```

## Pitfalls & Tips

- **Zero edges found**: Check RPC connectivity, pool data freshness in DB, or lower the min-profit threshold
- **Expected volume**: With Phase 1 expansion from 8 to 834 tokens, expect dozens to hundreds of edges per scan
- **Market variability**: Activity varies by time of day; run at different times for best coverage
- **JSON output**: For cron-based health checks, the script can be adapted to output JSON
- **Continuous monitoring**: Run every 5-10 minutes as a background health monitor

## Dependencies

- `tabulate` for pretty table output (optional; graceful fallback available)
- `veritas.db` pool registry (required for DB-only mode)
- Optional: RPC connection for fallback scanning mode

## Version

1.0 — Initial release with DB-based scanning and console report
