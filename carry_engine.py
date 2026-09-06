#!/usr/bin/env python3
"""
carry_engine.py - Main Hyperliquid funding rate trading loop.

Scans for opportunities, executes trades, manages positions.
"""
import argparse
import sys
import time

from funding_scout import scan_opportunities, fetch_all_markets
from risk_manager import RiskManager
from trade_exec import market_order, place_order

# Config
SCAN_INTERVAL = 60  # seconds
MIN_FUNDING_RATE = 0.0005  # 0.05%/hr
MIN_OI = 1_000_000  # $1M


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid carry trade engine")
    parser.add_argument("--interval", type=int, default=SCAN_INTERVAL)
    parser.add_argument("--min-rate", type=float, default=MIN_FUNDING_RATE)
    parser.add_argument("--dry", action="store_true", help="Dry run - no trades")
    args = parser.parse_args()
    
    print("=" * 70)
    print("HYPERLIQUID CARRY TRADE ENGINE")
    print("=" * 70)
    print(f"Scan interval: {args.interval}s")
    print(f"Min funding rate: {args.min_rate*100:.4f}%/hr")
    print(f"Dry run: {args.dry}")
    print("=" * 70)
    print()
    
    risk = RiskManager(initial_capital=15.0)
    
    try:
        while True:
            print(f"\n[{time.strftime('%H:%M:%S')}] Scanning...")
            
            # Scan for opportunities
            opportunities = scan_opportunities(
                min_rate=args.min_rate,
                min_oi=MIN_OI,
                top_n=5
            )
            
            # Show status
            status = risk.get_status()
            print(f"Capital: ${status['total']:.2f} | P&L: ${status['pnl']:.2f} | Positions: {status['positions']}")
            
            if opportunities:
                print(f"\nTop opportunity:")
                best = opportunities[0]
                print(f"  {best['coin']} {best['direction']} @ {best['funding_rate']*100:.4f}%/hr")
                print(f"  Annual: {best['annualized_return']*100:.1f}% | OI: ${best['open_interest']:,.0f}")
                
                if not args.dry and risk.can_open_position():
                    # Execute trade
                    coin = best["coin"]
                    is_buy = best["direction"] == "LONG"
                    
                    # Get market data for sizing
                    markets = fetch_all_markets()
                    market = next((m for m in markets if m["name"] == coin), None)
                    if market:
                        size = risk.calculate_position_size(
                            market["markPrice"], 
                            best["funding_rate"]
                        )
                        print(f"  Executing: {'BUY' if is_buy else 'SELL'} {size:.4f} {coin}")
                        result = market_order(coin, is_buy, size, market.get("szDecimals", 2))
                        if result:
                            risk.open_position(coin, best["direction"], size, 
                                             market["markPrice"], best["funding_rate"])
                            print(f"  Position opened!")
                        else:
                            print(f"  Execution failed")
            else:
                print("No opportunities found")
            
            print(f"\nNext scan in {args.interval}s...")
            time.sleep(args.interval)
            
    except KeyboardInterrupt:
        print("\n\nEngine stopped by user")
        print(f"Final P&L: ${risk.pnl:.2f}")


if __name__ == "__main__":
    main()