"""
Trade flow aggregation engine.

HOT PATH: process_trade() is called for every trade (~100s per second for active symbols).

Performance strategy:
1. O(1) per-trade updates using dict with bin prices as keys
2. Rolling window via time-bucketed deque (cleanup is amortized)
3. Avoid scanning entire ladder on each trade
4. Reuse FlowLevel list to minimize allocations

Future Cython/Rust optimization targets:
- Replace dict with fixed-size ring buffer for bins (known price range)
- Vectorized cleanup of expired trades
- Lock-free updates for multi-threaded ingestion
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING

from ..types import BinnedLevel, FlowLevel, Trade

# Rolling window in seconds
DEFAULT_WINDOW_SEC = 60.0


class FlowEngine:
    """
    Real-time trade flow aggregation engine.
    
    Maintains rolling stats for bid/ask traded volume per price bin.
    
    Thread-safety: NOT thread-safe. Designed for single-threaded async use.
    """
    
    __slots__ = (
        'window_sec', 'bin_size',
        '_trades', '_bid_volume', '_ask_volume',
        '_last_cleanup_time', '_cleanup_interval'
    )
    
    def __init__(self, bin_size: float = 1.0, window_sec: float = DEFAULT_WINDOW_SEC) -> None:
        self.window_sec = window_sec
        self.bin_size = bin_size
        
        # Rolling trade history for cleanup
        # Each entry: (timestamp_ms, bin_price, qty, is_bid)
        self._trades: deque[tuple[int, float, float, bool]] = deque()
        
        # Current aggregated volumes per bin
        # HOT PATH: Updated on every trade, read by UI
        self._bid_volume: dict[float, float] = {}  # bin_price -> total qty
        self._ask_volume: dict[float, float] = {}
        
        # Amortized cleanup
        self._last_cleanup_time: float = 0.0
        self._cleanup_interval: float = 1.0  # Cleanup every 1 second
    
    def _price_to_bin(self, price: float) -> float:
        """Convert price to bin center. HOT PATH."""
        return (price // self.bin_size) * self.bin_size + self.bin_size / 2
    
    def process_trade(self, trade: Trade) -> None:
        """
        Process a single trade from the WebSocket stream.
        
        HOT PATH - called for every trade (~100s per second).
        
        Args:
            trade: Trade with price, qty, is_buyer_maker, timestamp_ms
        """
        bin_price = self._price_to_bin(trade.price)
        
        # Determine aggressor side:
        # is_buyer_maker=True means buyer is maker, so seller aggressed (ask hit)
        # is_buyer_maker=False means seller is maker, so buyer aggressed (bid hit)
        is_bid = not trade.is_buyer_maker
        
        # O(1) update
        if is_bid:
            self._bid_volume[bin_price] = self._bid_volume.get(bin_price, 0.0) + trade.qty
        else:
            self._ask_volume[bin_price] = self._ask_volume.get(bin_price, 0.0) + trade.qty
        
        # Store for rolling window cleanup
        self._trades.append((trade.timestamp_ms, bin_price, trade.qty, is_bid))
        
        # Amortized cleanup
        now = time.perf_counter()
        if now - self._last_cleanup_time > self._cleanup_interval:
            self._cleanup_expired(trade.timestamp_ms)
            self._last_cleanup_time = now
    
    def _cleanup_expired(self, now_ms: int) -> None:
        """
        Remove trades older than window_sec from aggregates.
        
        Amortized O(k) where k = number of expired trades.
        """
        cutoff_ms = now_ms - int(self.window_sec * 1000)
        
        while self._trades and self._trades[0][0] < cutoff_ms:
            ts_ms, bin_price, qty, is_bid = self._trades.popleft()
            
            # Subtract from aggregates
            if is_bid:
                self._bid_volume[bin_price] = max(0.0, self._bid_volume.get(bin_price, 0.0) - qty)
                if self._bid_volume[bin_price] == 0.0:
                    self._bid_volume.pop(bin_price, None)
            else:
                self._ask_volume[bin_price] = max(0.0, self._ask_volume.get(bin_price, 0.0) - qty)
                if self._ask_volume[bin_price] == 0.0:
                    self._ask_volume.pop(bin_price, None)
    
    def get_flow_at_bin(self, bin_price: float) -> tuple[float, float, float]:
        """
        Get flow metrics for a single bin. O(1).
        
        Returns (bid_traded, ask_traded, delta).
        """
        bid = self._bid_volume.get(bin_price, 0.0)
        ask = self._ask_volume.get(bin_price, 0.0)
        return bid, ask, bid - ask
    
    def merge_with_book(
        self,
        binned_levels: list[BinnedLevel],
    ) -> list[FlowLevel]:
        """
        Merge resting book levels with traded flow metrics.
        
        Args:
            binned_levels: From OrderBook.get_binned_ladder()
        
        Returns list of FlowLevel for UI rendering.
        """
        result: list[FlowLevel] = []
        
        for level in binned_levels:
            bid_traded = self._bid_volume.get(level.bin_price, 0.0)
            ask_traded = self._ask_volume.get(level.bin_price, 0.0)
            delta = bid_traded - ask_traded
            
            result.append(FlowLevel(
                price=level.bin_price,
                bid_resting_qty=level.bid_qty,
                ask_resting_qty=level.ask_qty,
                bid_traded_qty=bid_traded,
                ask_traded_qty=ask_traded,
                delta_qty=delta,
            ))
        
        return result
    
    def clear(self) -> None:
        """Clear all flow data."""
        self._trades.clear()
        self._bid_volume.clear()
        self._ask_volume.clear()

