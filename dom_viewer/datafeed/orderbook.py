"""
High-performance local order book for Binance Futures.

HOT PATH: apply_update() is called 10x per second with potentially 1000s of price updates.

Performance strategy:
1. Use dict[float, float] for O(1) lookup/update of individual prices
2. Maintain sorted arrays lazily (only rebuild when UI requests)
3. Reuse numpy arrays to minimize allocations
4. Track dirty state to avoid unnecessary re-sorting

Future Cython/Rust optimization targets:
- Replace dicts with contiguous price arrays (known tick size)
- Use binary search for insertions instead of full re-sort
- Vectorized bin aggregation with numpy
"""

from __future__ import annotations

import time
from bisect import bisect_left, insort
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

from ..types import BinnedLevel, PriceLevel


class OrderBook:
    """
    Local order book with REST snapshot + WS incremental updates.
    
    Thread-safety: NOT thread-safe. Designed for single-threaded async use.
    """
    
    __slots__ = (
        'symbol', 'bids', 'asks', 'last_update_id',
        '_bid_prices_sorted', '_ask_prices_sorted',
        '_dirty', '_last_sort_time', '_update_count', '_update_start_time'
    )
    
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        
        # Core data: price -> quantity
        # HOT PATH: These dicts are updated every ~100ms with 100s of changes
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        
        self.last_update_id: int = 0
        
        # Cached sorted price arrays - rebuilt lazily
        self._bid_prices_sorted: list[float] = []  # Descending (best bid first)
        self._ask_prices_sorted: list[float] = []  # Ascending (best ask first)
        self._dirty: bool = True
        self._last_sort_time: float = 0.0
        
        # Performance tracking
        self._update_count: int = 0
        self._update_start_time: float = time.perf_counter()
    
    def load_snapshot(self, data: dict) -> None:
        """
        Load initial order book from REST snapshot.
        
        Expected format: {lastUpdateId, bids: [[price, qty], ...], asks: [[price, qty], ...]}
        """
        self.last_update_id = data['lastUpdateId']
        
        self.bids.clear()
        self.asks.clear()
        
        for price_str, qty_str in data['bids']:
            price, qty = float(price_str), float(qty_str)
            if qty > 0:
                self.bids[price] = qty
        
        for price_str, qty_str in data['asks']:
            price, qty = float(price_str), float(qty_str)
            if qty > 0:
                self.asks[price] = qty
        
        self._dirty = True
        self._ensure_sorted()
    
    def apply_update(self, update: dict) -> bool:
        """
        Apply incremental depth update from WebSocket.
        
        HOT PATH - called ~10x per second.
        
        Expected format: {U: first_id, u: last_id, b: [[price, qty], ...], a: [[price, qty], ...]}
        
        Returns True if update was applied, False if out of sequence.
        """
        first_id = update.get('U', update.get('u', 0))
        last_id = update['u']
        
        # Sequence check: first update ID should be <= last_update_id + 1
        if first_id > self.last_update_id + 1:
            return False  # Gap in sequence, need to re-snapshot
        
        # Skip if we've already processed this
        if last_id <= self.last_update_id:
            return True
        
        # HOT PATH: Update bids
        for price_str, qty_str in update.get('b', []):
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty
        
        # HOT PATH: Update asks
        for price_str, qty_str in update.get('a', []):
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty
        
        self.last_update_id = last_id
        self._dirty = True
        self._update_count += 1

        return True

    def _ensure_sorted(self) -> None:
        """Rebuild sorted price arrays if dirty. Called lazily before UI reads."""
        if not self._dirty:
            return

        # Full re-sort - O(n log n) but only done when UI requests data
        # OPTIMIZATION TARGET: Incremental maintenance with binary search
        self._bid_prices_sorted = sorted(self.bids.keys(), reverse=True)
        self._ask_prices_sorted = sorted(self.asks.keys())
        self._dirty = False
        self._last_sort_time = time.perf_counter()

    @property
    def best_bid(self) -> float:
        """Best bid price. Returns 0.0 if no bids."""
        self._ensure_sorted()
        return self._bid_prices_sorted[0] if self._bid_prices_sorted else 0.0

    @property
    def best_ask(self) -> float:
        """Best ask price. Returns 0.0 if no asks."""
        self._ensure_sorted()
        return self._ask_prices_sorted[0] if self._ask_prices_sorted else 0.0

    @property
    def mid_price(self) -> float:
        """Mid price. Returns 0.0 if no book."""
        bb, ba = self.best_bid, self.best_ask
        if bb > 0 and ba > 0:
            return (bb + ba) / 2.0
        return bb or ba

    def get_ladder(self, levels: int = 25) -> list[PriceLevel]:
        """
        Get raw price ladder centered around best bid/ask.

        Returns `levels` prices on each side of the spread.
        """
        self._ensure_sorted()

        result: list[PriceLevel] = []

        # Add asks (ascending from best ask, but we'll reverse for display)
        ask_count = min(levels, len(self._ask_prices_sorted))
        for i in range(ask_count - 1, -1, -1):
            price = self._ask_prices_sorted[i]
            result.append(PriceLevel(price, 0.0, self.asks[price]))

        # Add bids (descending from best bid)
        bid_count = min(levels, len(self._bid_prices_sorted))
        for i in range(bid_count):
            price = self._bid_prices_sorted[i]
            result.append(PriceLevel(price, self.bids[price], 0.0))

        return result

    def get_binned_ladder(self, bin_size: float, levels: int = 25) -> list[BinnedLevel]:
        """
        Get price ladder aggregated into bins.

        Args:
            bin_size: Size of each price bin (e.g., 1.0 for $1 bins)
            levels: Number of bins on each side of mid

        Returns list of BinnedLevel sorted by price descending.

        OPTIMIZATION TARGET: Vectorize with numpy for large books.
        """
        self._ensure_sorted()

        mid = self.mid_price
        if mid == 0:
            return []

        # Calculate bin boundaries
        center_bin = (mid // bin_size) * bin_size + bin_size / 2

        # Pre-allocate bins: {bin_center: [bid_qty, ask_qty]}
        bins: dict[float, list[float]] = {}
        for i in range(-levels, levels + 1):
            bin_center = center_bin + i * bin_size
            bins[bin_center] = [0.0, 0.0]

        # Aggregate bids into bins
        for price, qty in self.bids.items():
            bin_center = (price // bin_size) * bin_size + bin_size / 2
            if bin_center in bins:
                bins[bin_center][0] += qty

        # Aggregate asks into bins
        for price, qty in self.asks.items():
            bin_center = (price // bin_size) * bin_size + bin_size / 2
            if bin_center in bins:
                bins[bin_center][1] += qty

        # Convert to sorted list (descending by price)
        result = [
            BinnedLevel(price, bid_qty, ask_qty)
            for price, (bid_qty, ask_qty) in sorted(bins.items(), reverse=True)
        ]

        return result

    def get_updates_per_sec(self) -> float:
        """Return update rate for performance monitoring."""
        elapsed = time.perf_counter() - self._update_start_time
        if elapsed < 0.001:
            return 0.0
        return self._update_count / elapsed

    def reset_perf_counters(self) -> None:
        """Reset performance counters."""
        self._update_count = 0
        self._update_start_time = time.perf_counter()

