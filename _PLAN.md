# VERITAS Asset Leverage Implementation Plan

## Findings Summary
- Engine was down since Aug 28 12:44 (last log line); 0 edges across 30+ consecutive cycles
- V3 3-leg executor (FlashloanArbV3) compiled but never deployed / `.executor_v3_address` missing
- Camelot V3 quoting disabled despite being a deep Arbitrum venue
- MIN_DISLOCATION_BPS=25 dampened most live edges
- V2 sizing capped at 0.05 WETH (too small to clear fee stack)
- Hot wallet gas near zero (~0.0002 ETH)
- Fixed multiple latent bugs (call_erc20_balance undefined `pad`, `quote_v3` NameError)

## Implementation Status
- [x] Phase 0: System scan complete
- [x] Phase 1: Fix edge detection bottleneck in arb_engine.py (MIN_DISLOCATION_BPS 25→15, PROBE_SIZES up to 2.0, V2 size scale to pool depth)
- [x] Phase 1b: Fix _v2_mid cache closure bug (q_dec from wrong scope) — this was the #1 silent edge-killer
- [x] Phase 2: Enable Camelot V3 in v3_layer.py (quote_v3_venue + quote_v3_best_multi)
- [x] Phase 3: Wire FlashloanArbV3 executor (--deploy-v3 flag, broadcast_and_verify_v3, V3 sim dispatch)
- [x] Phase 4: Verify all modified files compile (py_compile passes)
- [x] Phase 5: Clean ops/start_engines.py (removed dead carry_engine reference)
- [ ] Phase 6: Restart flash_hunter engine (needs user action / .hot_secret presence)
- [ ] Phase 7: Fund hot wallet (gas ~0)
- [ ] Phase 8: Monitor logs for edges & broadcasts