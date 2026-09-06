#!/usr/bin/env python3
"""
funding_scout.py - Hyperliquid funding rate opportunity scanner.
"""
import argparse
import json
import time
import urllib.request
from typing import Dict, List, Optional

HYPERLIQUID_API = "https://api.hyperliquid.xyz"

MIN_FUNDING_RATE = 0.0005
MIN_OPEN_INTEREST = 1_000_000


def api_post(endpoint: str, payload: dict) -> dict:
    """Make a POST request to Hyperliquid API."""
    url = f"{HYPERLIQUID_API}/{endpoint}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[funding_scout] API error: {e}")
        return {}


def fetch_meta() -> List[dict]:
    """Fetch all market metadata."""
    try:
        result = api_post("info", {"type": "meta"})
        return result.get("universe", [])
    except Exception as e:
        print(f"[funding_scout] Error fetching meta: {e}")
        return []


def fetch_all_markets() -> List[dict]:
    """Fetch all market data in a single API call."""
    try:
        result = api_post("info", {"type": "metaAndAssetCtxs"})
        if len(result) < 2:
            return []
        meta, ctxs = result[0], result[1]
        markets = []
        for i, m in enumerate(meta.get("universe", [])):
            if i < len(ctxs):
                ctx = ctxs[i]
                markets.append({
                    "name": m.get("name", ""),
                    "szDecimals": m.get("szDecimals", 0),
                    "markPrice": float(ctx.get("markPx", 0)),
                    "fundingRate": float(ctx.get("funding", 0)),
                    "openInterest": float(ctx.get("openInterest", 0)),
                    "dayVolume": float(ctx.get("dayNtlVlm", 0)),
                    "oraclePrice": float(ctx.get("oraclePx", 0)),
                })
        return markets
    except Exception as e:
        print(f"[funding_scout] Error fetching markets: {e}")
    return []


def calculate_annualized_return(funding_rate: float) -> float:
    """Calculate annualized return from hourly funding rate."""
    hourly_rate = abs(funding_rate)
    return hourly_rate * 24 * 365


def scan_opportunities(min_rate: float, min_oi: float, top_n: int) -> List[dict]:
    """Scan all markets and return top opportunities."""
    print("[funding_scout] Fetching market data...")
    markets = fetch_all_markets()
    if not markets:
        print("[funding_scout] No markets found")
        return []
    print(f"[funding_scout] Found {len(markets)} markets, scanning...")
    opportunities = []
    for data in markets:
        coin = data["name"]
        if not coin:
            continue
        funding = data["fundingRate"]
        oi = data["openInterest"] * data["markPrice"]
        if abs(funding) < min_rate or oi < min_oi:
            continue
        annualized = calculate_annualized_return(funding)
        direction = "LONG" if funding < 0 else "SHORT"
        opportunities.append({
            "coin": coin,
            "direction": direction,
            "funding_rate": funding,
            "annualized_return": annualized,
            "open_interest": oi,
            "mark_price": data["markPrice"],
            "day_volume": data["dayVolume"],
        })
    opportunities.sort(key=lambda x: x["annualized_return"], reverse=True)
    return opportunities[:top_n]


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid funding rate scout")
    parser.add_argument("--min-rate", type=float, default=MIN_FUNDING_RATE)
    parser.add_argument("--min-oi", type=float, default=MIN_OPEN_INTEREST)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    print("=" * 70)
    print("HYPERLIQUID FUNDING RATE SCOUT")
    print("=" * 70)
    print()
    opportunities = scan_opportunities(args.min_rate, args.min_oi, args.top)
    if not opportunities:
        print("No opportunities found.")
        return
    print(f"Found {len(opportunities)} opportunities:")
    print("-" * 70)
    print(f"{'#':<3} {'Coin':<10} {'Dir':<6} {'Rate/hr':<12} {'Annual':<12} {'OI':<15}")
    print("-" * 70)
    for i, opp in enumerate(opportunities, 1):
        print(f"{i:<3} {opp['coin']:<10} {opp['direction']:<6} "
              f"{opp['funding_rate']*100:>8.4f}%   "
              f"{opp['annualized_return']*100:>8.1f}%   "
              f"${opp['open_interest']:>12,.0f}")
    print("-" * 70)


if __name__ == "__main__":
    main()