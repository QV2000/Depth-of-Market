"""
Data types for DOM Viewer.

Performance notes:
- Using NamedTuple for immutable, memory-efficient structures
- These are the UI-facing data structures; internal hot paths use raw arrays
"""

from typing import NamedTuple


class PriceLevel(NamedTuple):
    """Single price level from the order book."""
    price: float
    bid_qty: float  # 0 if this is an ask level
    ask_qty: float  # 0 if this is a bid level


class BinnedLevel(NamedTuple):
    """Price level aggregated into bins (e.g., $1 bins)."""
    bin_price: float      # Center of the bin
    bid_qty: float        # Total bid volume in this bin
    ask_qty: float        # Total ask volume in this bin


class FlowLevel(NamedTuple):
    """
    Complete level data for UI rendering, combining resting + traded volumes.
    
    This is what the UI consumes. Contains everything needed to render one row.
    """
    price: float              # Bin center price
    bid_resting_qty: float    # Resting bid volume (from order book)
    ask_resting_qty: float    # Resting ask volume (from order book)
    bid_traded_qty: float     # Volume traded at bid (buyer aggressor)
    ask_traded_qty: float     # Volume traded at ask (seller aggressor)
    delta_qty: float          # bid_traded - ask_traded


class Trade(NamedTuple):
    """Single trade from the trade stream."""
    price: float
    qty: float
    is_buyer_maker: bool  # True = sell aggressor, False = buy aggressor
    timestamp_ms: int


class DOMSnapshot(NamedTuple):
    """
    Complete DOM snapshot for UI rendering.
    
    Pushed to the UI queue at ~10-20 FPS.
    """
    symbol: str
    best_bid: float
    best_ask: float
    mid_price: float
    spread_bps: float
    levels: list[FlowLevel]  # Sorted by price descending (asks on top)
    timestamp_ms: int
    updates_per_sec: float   # Performance metric

