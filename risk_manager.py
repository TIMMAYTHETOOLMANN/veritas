#!/usr/bin/env python3
"""
risk_manager.py - Position sizing and risk management for Hyperliquid trading.
"""
import json
import os
from typing import Dict, Optional

STATE_FILE = ".risk_state.json"

MAX_POSITION_PCT = 0.10  # 10% of capital per position
MAX_CONCURRENT_POSITIONS = 3
STOP_LOSS_MULTIPLIER = 2.0  # Stop at 2x funding rate


class RiskManager:
    def __init__(self, initial_capital: float = 15.0):
        self.initial_capital = initial_capital
        self.positions = []
        self.pnl = 0.0
        self.load_state()
    
    def load_state(self):
        """Load risk state from file."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    state = json.load(f)
                    self.positions = state.get("positions", [])
                    self.pnl = state.get("pnl", 0.0)
            except Exception:
                pass
    
    def save_state(self):
        """Save risk state to file."""
        with open(STATE_FILE, "w") as f:
            json.dump({
                "positions": self.positions,
                "pnl": self.pnl,
            }, f, indent=2)
    
    def can_open_position(self) -> bool:
        """Check if we can open a new position."""
        return len(self.positions) < MAX_CONCURRENT_POSITIONS
    
    def calculate_position_size(self, mark_price: float, funding_rate: float) -> float:
        """Calculate position size based on capital and risk."""
        available_capital = self.initial_capital + self.pnl
        max_size = available_capital * MAX_POSITION_PCT
        # Size in base currency
        position_size = max_size / mark_price
        return position_size
    
    def should_stop_loss(self, entry_funding: float, current_funding: float) -> bool:
        """Check if stop-loss should be triggered."""
        # Stop if funding moved against us by 2x
        if entry_funding > 0:  # We're short
            return current_funding > entry_funding * STOP_LOSS_MULTIPLIER
        else:  # We're long
            return current_funding < entry_funding * STOP_LOSS_MULTIPLIER
    
    def open_position(self, coin: str, direction: str, size: float, 
                      mark_price: float, funding_rate: float):
        """Record a new position."""
        position = {
            "coin": coin,
            "direction": direction,
            "size": size,
            "entry_price": mark_price,
            "entry_funding": funding_rate,
            "timestamp": __import__("time").time(),
        }
        self.positions.append(position)
        self.save_state()
        return position
    
    def close_position(self, coin: str, exit_price: float):
        """Close a position and record P&L."""
        for i, pos in enumerate(self.positions):
            if pos["coin"] == coin:
                if pos["direction"] == "LONG":
                    pnl = (exit_price - pos["entry_price"]) * pos["size"]
                else:
                    pnl = (pos["entry_price"] - exit_price) * pos["size"]
                self.pnl += pnl
                self.positions.pop(i)
                self.save_state()
                return pnl
        return 0.0
    
    def get_status(self) -> dict:
        """Get current risk status."""
        return {
            "capital": self.initial_capital,
            "pnl": self.pnl,
            "total": self.initial_capital + self.pnl,
            "positions": len(self.positions),
            "max_positions": MAX_CONCURRENT_POSITIONS,
        }