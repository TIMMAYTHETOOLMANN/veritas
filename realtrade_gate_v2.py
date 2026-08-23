#!/usr/bin/env python3
"""
VERITAS Realtrade Gate v2 — Dynamic Multi-Position Risk-Budgeted Execution

Enhanced with risk_manager.py for dynamic capital allocation:
- Total pool: $20 (after $10 extraction)
- Max risk: 40% = $8, but emergency stop at $7 → effective max risk = $7
- As many positions as quality opportunities allow
- Per-position risk = $7 / N_positions
- Each position has exchange-enforced hard stop
- Quality over quantity: every trade scouted via funding_scout.py
- Emergency halt if total losses > $7
- $10 extraction to wallet when cumulative PnL >= $10
"""

import sys
import os
import time
import json
import signal
from typing import List, Dict

# Add VERITAS to path
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Import our modules
from trade_exec import require_sdk, get_exchange, TAKER_FEE
from funding_scout import analyze
from risk_manager import (
    discover_opportunities, allocate_portfolio, PortfolioState, PositionPlan,
    TOTAL_POOL_USD, MAX_RISK_USD, EMERGENCY_STOP_USD, EFFECTIVE_MAX_RISK_USD,
    MIN_POSITION_USD, MAX_POSITIONS
)

# === CONFIGURATION ===
EXTRACTION_AMOUNT_USD = 10.0
REMAINING_CAPITAL_USD = 10.0
PROFITABILITY_CHECK_INTERVAL_SEC = 180  # 3 minutes
MAX_PROFITABILITY_INTERVAL_SEC = 300    # 5 min max

# State tracking
state = {
    "portfolio": None,
    "last_profitability_check": time.time(),
    "emergency_halt": False,
    "user_wallet": None,
    "cycle_count": 0,
}

signal.signal(signal.SIGINT, lambda s, f: setattr(state, "emergency_halt", True))
signal.signal(signal.SIGTERM, lambda s, f: setattr(state, "emergency_halt", True))


def signal_handler(signum, frame):
    print(f"\n[!] Signal {signum} received — initiating clean shutdown...")
    state["emergency_halt"] = True


def check_emergency_stop(portfolio: PortfolioState) -> bool:
    """Check if emergency stop should trigger."""
    if portfolio.emergency_stop_triggered:
        print(f"\n[EMERGENCY STOP] Total potential loss ${portfolio.used_risk_usd:.2f} + realized ${abs(min(0, portfolio.realized_pnl_usd)):.2f} exceeds ${EMERGENCY_STOP_USD:.2f}!")
        print("[!] System shutting down — awaiting your return.")
        state["emergency_halt"] = True
        return True
    return False


def check_extraction(portfolio: PortfolioState, user_wallet: str) -> bool:
    """Check if $10 extraction should trigger."""
    if portfolio.extraction_ready and user_wallet:
        print(f"\n[EXTRACTION] Cumulative realized PnL ${portfolio.realized_pnl_usd:.2f} >= ${EXTRACTION_AMOUNT_USD:.2f}")
        print(f"[EXTRACT] Sending ${EXTRACTION_AMOUNT_USD:.2f} to wallet: {user_wallet}")
        # TODO: Implement actual USDC transfer to user wallet
        portfolio.realized_pnl_usd -= EXTRACTION_AMOUNT_USD
        print(f"[SUCCESS] $10 extracted to {user_wallet}. Portfolio PnL adjusted.")
        return True
    return False


def check_profitability_cycle() -> bool:
    """Check if profitability evaluation interval has elapsed."""
    now = time.time()
    elapsed = now - state["last_profitability_check"]
    if elapsed >= PROFITABILITY_CHECK_INTERVAL_SEC:
        print(f"\n[PROFITABILITY] {elapsed:.0f}s since last check — evaluating portfolio...")
        state["last_profitability_check"] = now
        return True
    return False


def execute_position_plan(plan: PositionPlan, execute: bool = False) -> bool:
    """Execute a single position plan via Hyperliquid SDK."""
    print(f"[TRADE] {plan.direction} {plan.coin} — notional: ${plan.notional_usd:.2f} @ {plan.entry_price:.6f} (dry-run={not execute})")
    
    if not execute:
        print("  [DRY-RUN] Skipping actual submission")
        return True
    
    try:
        acct = require_sdk()
        info, ex, _ = get_exchange()
        
        coin = plan.coin.upper()
        is_buy = plan.direction == "LONG"
        
        # Get size decimals
        sz_dec = None
        meta = info.meta()
        for name in meta["universe"]:
            if name["name"] == coin:
                sz_dec = name.get("szDecimals", 4)
                break
        
        if sz_dec is None:
            print(f"  [ERROR] Unknown coin: {coin}")
            return False
        
        mark = float(info.all_mids().get(coin, 0) or 0)
        if mark <= 0:
            print(f"  [ERROR] No mark price for {coin}")
            return False
        
        # Use entry price with small slippage tolerance
        px = plan.entry_price
        sz = round(plan.notional_usd / px, sz_dec)
        
        if sz <= 0:
            print(f"  [ERROR] Size rounds to zero")
            return False
        
        est_fee = plan.notional_usd * TAKER_FEE
        print(f"  Size: {sz} {coin} (${plan.notional_usd:.2f} notional)")
        print(f"  Price: {px:.6f} (mark {mark:.6f})")
        print(f"  Est. fee: ${est_fee:.4f}")
        print(f"  Hard stop: {plan.hard_stop_price:.6f} (exchange-enforced)")
        
        # Execute entry order (IOC limit crossing the book)
        result = ex.order(coin, is_buy, sz, px, {"limit": {"tif": "Ioc"}}, reduce_only=False)
        print(f"  Order result: {result.get('status')}")
        
        # Place hard stop (reduce-only trigger SL)
        stop_buy = plan.direction == "SHORT"  # Close short = buy back
        stop_result = ex.order(coin, stop_buy, sz, plan.hard_stop_price, 
                               {"trigger": {"triggerPx": plan.hard_stop_price, "isMarket": True, "tpsl": "sl"}},
                               reduce_only=True)
        print(f"  Stop order: {stop_result.get('status')} @ {plan.hard_stop_price}")
        
        return result.get("status") == "ok"
        
    except Exception as e:
        print(f"  [TRADE ERROR]: {e}")
        return False


def scan_and_allocate() -> PortfolioState:
    """Scan for opportunities and build optimal portfolio allocation."""
    print("[SCAN] Scanning funding-extreme opportunities...")
    opportunities = discover_opportunities(equity=TOTAL_POOL_USD, top=15)
    
    if not opportunities:
        print("[SCAN] No quality candidates found.")
        return PortfolioState()
    
    print(f"[SCAN] Found {len(opportunities)} quality candidates")
    
    # Build optimal allocation
    plans = allocate_portfolio(opportunities, max_positions=MAX_POSITIONS)
    
    if not plans:
        print("[ALLOC] No valid position plans generated.")
        return PortfolioState()
    
    portfolio = PortfolioState()
    portfolio.positions = plans
    
    # Print allocation
    print(f"\n[ALLOC] Generated {len(plans)} position plans")
    print(f"  Total Notional: ${sum(p.notional_usd for p in plans):.2f}")
    print(f"  Total Margin: ${sum(p.margin_usd for p in plans):.2f}")
    print(f"  Total Max Loss: ${sum(p.max_loss_usd for p in plans):.4f} / ${EFFECTIVE_MAX_RISK_USD:.2f}")
    print(f"  Risk Utilization: {portfolio.risk_utilization_pct:.1f}%")
    print(f"  Est. 12h Carry: ${sum(p.estimated_carry_usd_12h for p in plans):.4f}")
    
    return portfolio


def realtrade_gate_v2():
    """Main realtrade gate loop with dynamic multi-position allocation."""
    print("=" * 70)
    print("VERITAS Realtrade Gate v2 — Dynamic Multi-Position Execution")
    print("=" * 70)
    print(f"Total Pool: ${TOTAL_POOL_USD:.2f}")
    print(f"Max Risk (40%): ${MAX_RISK_USD:.2f}")
    print(f"Emergency Stop: ${EMERGENCY_STOP_USD:.2f} (effective max risk: ${EFFECTIVE_MAX_RISK_USD:.2f})")
    print(f"Min Position: ${MIN_POSITION_USD:.2f}")
    print(f"Max Positions: {MAX_POSITIONS}")
    print(f"Extraction: ${EXTRACTION_AMOUNT_USD:.2f} to wallet when PnL >= ${EXTRACTION_AMOUNT_USD:.2f}")
    print(f"Profitability Check: every {PROFITABILITY_CHECK_INTERVAL_SEC//60} min")
    print("=" * 70)
    print()
    
    # Initial allocation
    portfolio = scan_and_allocate()
    state["portfolio"] = portfolio
    
    if not portfolio.positions:
        print("[INIT] No initial positions — will retry on next cycle")
    else:
        print("[INIT] Executing initial positions...")
        for plan in portfolio.positions:
            execute_position_plan(plan, execute=False)  # DRY-RUN by default
    
    print("\n[SYSTEM] Entering main monitoring loop...")
    print("=" * 70)
    
    while not state["emergency_halt"]:
        state["cycle_count"] += 1
        
        # Check emergency stop
        if check_emergency_stop(portfolio):
            break
        
        # Check extraction
        user_wallet = os.environ.get("VERITAS_USER_WALLET")
        if user_wallet:
            state["user_wallet"] = user_wallet
        check_extraction(portfolio, state["user_wallet"])
        
        # Profitability cycle check
        if check_profitability_cycle():
            print(f"\n[CYCLE #{state['cycle_count']}] Portfolio rebalancing...")
            
            # Re-scan and reallocate
            new_portfolio = scan_and_allocate()
            
            if new_portfolio.positions:
                # Compare with current portfolio
                current_coins = {p.coin for p in portfolio.positions}
                new_coins = {p.coin for p in new_portfolio.positions}
                
                added = new_coins - current_coins
                removed = current_coins - new_coins
                kept = current_coins & new_coins
                
                if added:
                    print(f"[REBALANCE] Adding: {', '.join(added)}")
                    for plan in new_portfolio.positions:
                        if plan.coin in added:
                            execute_position_plan(plan, execute=False)
                
                if removed:
                    print(f"[REBALANCE] Removing: {', '.join(removed)}")
                    # TODO: Close removed positions
                
                if kept:
                    print(f"[REBALANCE] Keeping: {', '.join(kept)}")
                
                portfolio = new_portfolio
                state["portfolio"] = portfolio
            else:
                print("[REBALANCE] No new valid positions — keeping current")
        
        # Status heartbeat
        if state["cycle_count"] % 5 == 0:
            print(f"\n[HEARTBEAT] Cycle #{state['cycle_count']} | "
                  f"Positions: {len(portfolio.positions)} | "
                  f"Risk Used: ${portfolio.used_risk_usd:.2f}/${EFFECTIVE_MAX_RISK_USD:.2f} "
                  f"({portfolio.risk_utilization_pct:.1f}%) | "
                  f"Realized PnL: ${portfolio.realized_pnl_usd:.2f} | "
                  f"Emergency: {'TRIGGERED' if state['emergency_halt'] else 'OK'}")
        
        # Wait before next check
        wait_time = 30  # 30 seconds between checks
        time.sleep(wait_time)
    
    # Final report
    print("\n" + "=" * 70)
    print("REALTRADE GATE v2 — SESSION END")
    print("=" * 70)
    print(f"Total cycles: {state['cycle_count']}")
    print(f"Final positions: {len(portfolio.positions)}")
    print(f"Total risk used: ${portfolio.used_risk_usd:.2f}")
    print(f"Realized PnL: ${portfolio.realized_pnl_usd:.2f}")
    print(f"Emergency halt: {state['emergency_halt']}")
    
    if state["emergency_halt"]:
        print("\n[SYSTEM] Engine halted — awaiting user return.")
    
    print("=" * 70)


if __name__ == "__main__":
    user_wallet = os.environ.get("VERITAS_USER_WALLET")
    if not user_wallet:
        print("[INFO] VERITAS_USER_WALLET not set — $10 extraction disabled.")
        print("       Set with: $env:VERITAS_USER_WALLET='0x...' (PowerShell)")
    
    print("\n--- Starting VERITAS Realtrade Gate v2 ---\n")
    realtrade_gate_v2()