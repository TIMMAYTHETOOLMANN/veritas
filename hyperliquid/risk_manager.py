#!/usr/bin/env python3
"""
VERITAS Risk Manager — Dynamic Capital Allocation with Risk-Budgeted Position Sizing

Constraints:
- Total pool: $20 (after $10 extraction, $20 remains in pool)
- Max risk at any time: 40% of pool = $8
- Emergency stop: $7 total loss → system halt
- Effective max risk: min($8, $7) = $7 (emergency stop is tighter)
- As many positions as quality opportunities allow
- Per-position risk = $7 / N_positions
- Each position has hard stop on exchange
- Quality over quantity: every trade scouted via funding_scout.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from funding_scout import analyze

# === CONSTANTS ===
TOTAL_POOL_USD = 17.76          # Live capital: hot-wallet HL perps account
MAX_RISK_PCT = 0.40             # 40% max at risk
MAX_RISK_USD = TOTAL_POOL_USD * MAX_RISK_PCT  # $7.10
EMERGENCY_STOP_USD = 7.0        # Hard halt threshold
EFFECTIVE_MAX_RISK_USD = min(MAX_RISK_USD, EMERGENCY_STOP_USD)  # $7.00
MIN_POSITION_USD = 10.0         # Minimum notional per position (Hyperliquid $10 minimum)
MAX_POSITIONS = 10              # Hard cap on simultaneous positions

# Per-asset leverage caps (Hyperliquid max leverage by asset)
# These are discovered dynamically but we have defaults
DEFAULT_MAX_LEVERAGE = {
    'ACE': 3, 'PURR': 5, 'MOVE': 3, 'VINE': 3, 'WIF': 5,
    'BTC': 20, 'ETH': 20, 'SOL': 10, 'ARB': 5, 'OP': 5,
}


@dataclass
class PositionPlan:
    """Complete plan for a single position."""
    coin: str
    direction: str              # 'LONG' or 'SHORT'
    entry_price: float
    hard_stop_price: float
    max_leverage: int
    notional_usd: float         # Total position size in USD
    margin_usd: float           # Margin required (notional / leverage)
    max_loss_usd: float         # Max loss if hard stop hits
    risk_budget_usd: float      # Allocated risk budget for this position
    estimated_carry_usd_12h: float  # Estimated 12h funding carry
    confidence_score: float     # From funding_scout (0-1)
    
    def __post_init__(self):
        # Verify max_loss doesn't exceed risk budget (with small buffer)
        if self.max_loss_usd > self.risk_budget_usd * 1.05:
            raise ValueError(f"Max loss ${self.max_loss_usd:.4f} exceeds risk budget ${self.risk_budget_usd:.4f} for {self.coin}")


@dataclass
class PortfolioState:
    """Current portfolio state."""
    total_pool_usd: float = TOTAL_POOL_USD
    max_risk_usd: float = EFFECTIVE_MAX_RISK_USD
    positions: List[PositionPlan] = field(default_factory=list)
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    total_max_loss_usd: float = 0.0
    
    @property
    def used_risk_usd(self) -> float:
        return sum(p.max_loss_usd for p in self.positions)
    
    @property
    def available_risk_usd(self) -> float:
        return max(0.0, self.max_risk_usd - self.used_risk_usd)
    
    @property
    def risk_utilization_pct(self) -> float:
        return (self.used_risk_usd / self.max_risk_usd * 100) if self.max_risk_usd > 0 else 0.0
    
    @property
    def emergency_stop_triggered(self) -> bool:
        # Emergency stop triggers on ACTUAL REALIZED LOSSES exceeding $7
        # NOT on risk budget allocation (which is by design)
        # Total loss = negative realized PnL (actual money lost)
        actual_loss = abs(min(0, self.realized_pnl_usd))
        return actual_loss > EMERGENCY_STOP_USD
    
    @property
    def extraction_ready(self) -> bool:
        return self.realized_pnl_usd >= 10.0


def calculate_hard_stop_price(entry_price: float, direction: str, leverage: int, 
                              market_structure_buffer_pct: float = 0.05) -> float:
    """
    Calculate hard stop price based on liquidation price + buffer.
    
    For LONG: liquidation = entry * (1 - 1/leverage * maintenance_margin_factor)
    For SHORT: liquidation = entry * (1 + 1/leverage * maintenance_margin_factor)
    
    We place hard stop BEYOND liquidation (wider) with market structure buffer.
    """
    # Hyperliquid maintenance margin is typically ~2.5% for most assets
    maint_margin = 0.025
    
    if direction == 'LONG':
        liq_price = entry_price * (1 - (1/leverage) * (1 - maint_margin))
        # Hard stop BELOW liquidation (wider stop) - add buffer
        hard_stop = liq_price * (1 - market_structure_buffer_pct)
    else:
        liq_price = entry_price * (1 + (1/leverage) * (1 - maint_margin))
        # Hard stop ABOVE liquidation (wider stop)
        hard_stop = liq_price * (1 + market_structure_buffer_pct)
    
    return round(hard_stop, 6)


def calculate_max_loss(entry_price: float, hard_stop_price: float, 
                       notional_usd: float, direction: str) -> float:
    """Calculate max loss in USD if hard stop triggers."""
    if direction == 'LONG':
        loss_pct = (entry_price - hard_stop_price) / entry_price
    else:
        loss_pct = (hard_stop_price - entry_price) / entry_price
    return notional_usd * abs(loss_pct)


def discover_opportunities(equity: float, top: int = 15) -> List[Dict]:
    """Discover quality funding opportunities via funding_scout."""
    result = analyze(equity=equity, top=top)
    candidates = result.get('candidates', [])
    
    # Filter for quality: score > 0.2, persistent funding, good liquidity
    quality = []
    for c in candidates:
        score = c.get('score', 0)
        funding_72h = c.get('funding_72h', {})
        oi = c.get('oi', 0)
        
        if (score >= 0.2 and 
            funding_72h.get('avg', 0) != 0 and 
            oi >= 2_000_000):  # Min $2M OI
            quality.append(c)
    
    return quality


def build_position_plan(candidate: Dict, risk_budget_usd: float) -> Optional[PositionPlan]:
    """Build a position plan from a funding_scout candidate."""
    coin = candidate['coin']
    direction = candidate['direction']
    entry = candidate['mark']  # 'mark' is the entry price from funding_scout
    funding_hr = candidate['funding']
    funding_7h_avg = candidate.get('funding_7h_avg', funding_hr)
    
    # Get max leverage for this asset
    max_lev = DEFAULT_MAX_LEVERAGE.get(coin, 3)
    
    # Calculate hard stop based on leverage and market structure
    hard_stop = calculate_hard_stop_price(entry, direction, max_lev)
    
    # We need to find notional such that max_loss <= risk_budget
    # max_loss = notional * |(entry - hard_stop) / entry|
    loss_pct = abs(entry - hard_stop) / entry
    
    if loss_pct <= 0:
        return None
    
    # Max notional that keeps loss within risk budget
    max_notional = risk_budget_usd / loss_pct
    
    # Also cap by margin available: margin = notional / leverage
    # We want to use leverage efficiently but stay within risk budget
    margin_needed = max_notional / max_lev
    
    # Apply minimum position size
    if max_notional < MIN_POSITION_USD:
        return None
    
    # Round notional to reasonable precision
    notional = round(min(max_notional, 1000), 2)  # Cap at $1000 notional per position
    
    # Recalculate actual max loss with rounded notional
    actual_max_loss = calculate_max_loss(entry, hard_stop, notional, direction)
    
    # Estimate 12h carry
    # For LONG in negative funding: we RECEIVE funding (positive carry)
    # For SHORT in positive funding: we RECEIVE funding (positive carry)
    if direction == 'LONG':
        carry_12h = -funding_7h_avg * notional * 12  # negative funding * -1 = positive
    else:
        carry_12h = funding_7h_avg * notional * 12   # positive funding = positive
    
    return PositionPlan(
        coin=coin,
        direction=direction,
        entry_price=entry,
        hard_stop_price=hard_stop,
        max_leverage=max_lev,
        notional_usd=notional,
        margin_usd=notional / max_lev,
        max_loss_usd=actual_max_loss,
        risk_budget_usd=risk_budget_usd,
        estimated_carry_usd_12h=round(carry_12h, 4),
        confidence_score=candidate.get('score', 0)
    )


def allocate_portfolio(opportunities: List[Dict], max_positions: int = MAX_POSITIONS) -> List[PositionPlan]:
    """
    Allocate risk budget across opportunities dynamically.
    
    Strategy:
    1. Sort opportunities by score (quality)
    2. Determine how many positions we can support: N = min(len(opps), max_positions)
    3. Per-position risk budget = EFFECTIVE_MAX_RISK_USD / N
    4. Build position plans, each constrained by its risk budget
    5. If any position can't meet minimum, reduce N and reallocate
    """
    if not opportunities:
        return []
    
    # Sort by quality score descending
    sorted_opps = sorted(opportunities, key=lambda x: x.get('score', 0), reverse=True)
    
    # Try different numbers of positions to maximize utilization
    best_allocation = []
    best_utilization = 0.0
    
    for n in range(1, min(len(sorted_opps), max_positions) + 1):
        risk_budget = EFFECTIVE_MAX_RISK_USD / n
        plans = []
        total_risk = 0.0
        
        for opp in sorted_opps[:n]:
            plan = build_position_plan(opp, risk_budget)
            if plan:
                plans.append(plan)
                total_risk += plan.max_loss_usd
        
        # Check if all positions meet minimum size
        if len(plans) == n and all(p.notional_usd >= MIN_POSITION_USD for p in plans):
            utilization = total_risk / EFFECTIVE_MAX_RISK_USD
            if utilization > best_utilization:
                best_utilization = utilization
                best_allocation = plans
    
    return best_allocation


def print_allocation(portfolio: PortfolioState, opportunities: List[Dict]):
    """Print detailed allocation plan."""
    print("\n" + "=" * 70)
    print("DYNAMIC RISK ALLOCATION — VERITAS PORTFOLIO")
    print("=" * 70)
    print(f"\n📊 PORTFOLIO STATE")
    print(f"   Total Pool: ${portfolio.total_pool_usd:.2f}")
    print(f"   Max Risk (40%): ${MAX_RISK_USD:.2f}")
    print(f"   Emergency Stop: ${EMERGENCY_STOP_USD:.2f}")
    print(f"   Effective Max Risk: ${EFFECTIVE_MAX_RISK_USD:.2f}")
    print(f"   Used Risk: ${portfolio.used_risk_usd:.2f} ({portfolio.risk_utilization_pct:.1f}%)")
    print(f"   Available Risk: ${portfolio.available_risk_usd:.2f}")
    print(f"   Realized PnL: ${portfolio.realized_pnl_usd:.2f}")
    print(f"   Unrealized PnL: ${portfolio.unrealized_pnl_usd:.2f}")
    
    if portfolio.positions:
        print(f"\n🎯 ACTIVE POSITIONS ({len(portfolio.positions)})")
        print(f"{'Coin':<8}{'Dir':<6}{'Entry':>10}{'Hard Stop':>10}{'Lev':>4}{'Notional':>10}{'Margin':>8}{'MaxLoss':>9}{'Carry12h':>10}{'Score':>6}")
        print("-" * 95)
        for p in portfolio.positions:
            print(f"{p.coin:<8}{p.direction:<6}{p.entry_price:>10.6f}{p.hard_stop_price:>10.6f}"
                  f"{p.max_leverage:>4}x{p.notional_usd:>10.2f}{p.margin_usd:>8.2f}"
                  f"{p.max_loss_usd:>9.4f}{p.estimated_carry_usd_12h:>10.4f}{p.confidence_score:>6.3f}")
    
    if opportunities:
        print(f"\n📡 CANDIDATE OPPORTUNITIES ({len(opportunities)})")
        print(f"{'Coin':<8}{'Dir':<6}{'Entry':>10}{'Funding/hr':>12}{'7h Avg':>10}{'OI($M)':>8}{'Score':>6}")
        print("-" * 65)
        for c in opportunities[:15]:
            fhr = c['funding'] * 100
            f7h = c.get('funding_7h_avg', c['funding']) * 100
            oi = c['oi'] / 1e6
            print(f"{c['coin']:<8}{c['direction']:<6}{c['mark']:>10.6f}{fhr:>11.4f}%"
                  f"{f7h:>9.4f}%{oi:>8.1f}{c.get('score', 0):>6.3f}")
    
    print("=" * 70)


def demo_allocation():
    """Demo the allocation with current market data."""
    print("🔍 Scanning for funding opportunities...")
    opportunities = discover_opportunities(equity=TOTAL_POOL_USD, top=15)
    
    if not opportunities:
        print("No quality opportunities found.")
        return
    
    print(f"Found {len(opportunities)} quality candidates")
    
    # Build portfolio
    portfolio = PortfolioState()
    plans = allocate_portfolio(opportunities)
    portfolio.positions = plans
    
    # Print detailed allocation
    print_allocation(portfolio, opportunities)
    
    # Summary
    print(f"\n✅ ALLOCATION SUMMARY")
    print(f"   Positions: {len(plans)}")
    print(f"   Total Notional: ${sum(p.notional_usd for p in plans):.2f}")
    print(f"   Total Margin: ${sum(p.margin_usd for p in plans):.2f}")
    print(f"   Total Max Loss: ${sum(p.max_loss_usd for p in plans):.4f} / ${EFFECTIVE_MAX_RISK_USD:.2f}")
    print(f"   Estimated 12h Carry: ${sum(p.estimated_carry_usd_12h for p in plans):.4f}")
    print(f"   Risk Utilization: {portfolio.risk_utilization_pct:.1f}%")
    
    return portfolio, opportunities


if __name__ == "__main__":
    portfolio, opportunities = demo_allocation()