# VERITAS SYSTEM-WIDE OPTIMIZATION PLAN
## Pre-Deployment Profit Maximization

### Executive Summary
- **Current State**: 0 ETH wallet balance, 118 Python modules, 6 active profit vectors
- **Missing Vectors**: JIT liquidity (0 modules), MEV bundles (minimal integration)
- **Critical Issues**: RPC hang vulnerability (core/rpc.py:54), hunter liveness detection broken
- **Target**: $10 verified profit before declaring deployment success

---

## Phase 1: CRITICAL FIXES (MUST COMPLETE FIRST)

### 1.1 Fix RPC Hang Vulnerability ⚠️
**File**: `core/rpc.py` line 54
**Issue**: Unhardened `call()` function causes indefinite urllib TLS hangs
**Fix**: Apply daemon-thread + queue.get(timeout) pattern from `call_hard()`

### 1.2 Fix Hunter Liveness Detection ⚠️
**File**: `start_engines.py`
**Issue**: Reports "ALREADY RUNNING" for hung PIDs, cannot restart
**Fix**: Add TCP port check or recent-log-timestamp validation

### 1.3 Fix Watchdog Log Detection
**Files**: Watchdog script (need to locate)
**Issue**: Checks flash_hunter.log (stale), should check hunter_full.log
**Fix**: Use newest of flash_hunter/hunter_stdout/hunter_full logs

---

## Phase 2: PROFIT VECTOR EXPANSION

### 2.1 MEV Bundle Integration (HIGH PRIORITY)
**New Capability**: Submit profitable arbs via Flashbots to avoid frontrunning
**Implementation**:
- Add `core/flashbots.py` module
- Integrate with `flash_hunter.py` execution path
- Add bundle simulation before submission
- **Profit Multiplier**: 15-30% via reduced revert risk

### 2.2 JIT Liquidity Detection (NEW VECTOR)
**New Capability**: Detect and exploit Just-In-Time liquidity provision
**Implementation**:
- Add `jit_detector.py` module
- Monitor mempool for large swap txs
- Front-run with liquidity provision, back-run with removal
- **Profit Multiplier**: 5-20% on high-volume swaps

### 2.3 Triangular Arbitrage Expansion
**Current**: 9 modules, limited venue coverage
**Enhancement**:
- Expand from 2-hop to 3-hop and 4-hop cycles
- Add cross-DEX triangular (Uniswap → SushiSwap → Balancer)
- **Profit Multiplier**: 10-25% via multi-venue inefficiencies

### 2.4 Cross-Chain Arbitrage
**New Capability**: Arbitrage between Arbitrum, Optimism, Base, Mainnet
**Implementation**:
- Add bridge monitoring
- Detect price divergence across chains
- Use native bridges or Circle CCTP for USDC
- **Profit Multiplier**: 20-50% on major price dislocations

---

## Phase 3: GAS OPTIMIZATION

### 3.1 Batch ZK Proof Generation
**File**: `zk_prover.py`
**Enhancement**: Generate proofs for multiple opportunities in parallel
**Savings**: 30-40% proof generation time

### 3.2 EIP-1559 Gas Pricing
**Files**: All execution modules
**Enhancement**: Dynamic base fee + priority fee optimization
**Savings**: 10-20% gas costs

### 3.3 Calldata Compression
**Files**: `flash_hunter.py` (encode_execute_*)
**Enhancement**: Pack parameters tighter, use bytes32 where possible
**Savings**: 15-25% calldata costs

---

## Phase 4: SLIPPAGE & DEPTH MODELING

### 4.1 Real-Time Depth Tracking
**File**: `pool_registry.py`
**Enhancement**: Cache pool reserves, update on every block
**Impact**: Reduce slippage estimation errors by 60%

### 4.2 Multi-Block MEV Simulation
**New File**: `sim_multiblock.py`
**Enhancement**: Simulate arb profitability across next 3 blocks
**Impact**: Increase execution success rate by 25%

---

## Phase 5: CONCURRENCY & THROUGHPUT

### 5.1 Parallel Edge Scanning
**File**: `arb_engine.py` (parallel_scan_edges)
**Enhancement**: Increase max_workers from 4 to 12
**Impact**: 3x scan throughput

### 5.2 Async RPC Calls
**File**: `core/rpc.py`
**Enhancement**: Convert synchronous calls to async/await
**Impact**: 50-70% RPC latency reduction

---

## Phase 6: DEPLOYMENT CHECKLIST

- [ ] Wallet funded with minimum 0.1 ETH for gas
- [ ] All critical fixes applied (Phase 1)
- [ ] At least 3 new profit vectors integrated (Phase 2)
- [ ] Gas optimizations applied (Phase 3)
- [ ] Slippage models updated (Phase 4)
- [ ] Concurrency enhancements deployed (Phase 5)
- [ ] Dry-run simulation shows >$10 profit potential
- [ ] Monitoring dashboard configured
- [ ] Kill-switch mechanism tested

---

## Monitoring Strategy

### Real-Time Metrics
- Wallet balance (poll every 10 seconds)
- Successful arb executions
- Failed execution reasons
- Gas expenditure vs profit
- Profit per opportunity type

### Success Criteria
- **Primary**: $10.00 verified profit in wallet
- **Secondary**: >80% execution success rate
- **Tertiary**: <$2.00 gas expenditure per $10 profit

### Auto-Shutdown Triggers
- Wallet balance < 0.01 ETH
- 10 consecutive failed executions
- Profit/gas ratio < 0.5
- Unhandled exception in core engine
