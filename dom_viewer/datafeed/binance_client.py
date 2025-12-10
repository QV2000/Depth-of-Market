"""
Binance Futures WebSocket client with async orchestration.

Handles:
1. REST snapshot for initial order book state
2. Combined WebSocket stream for depth updates + trades
3. Proper sequencing per Binance depth protocol
4. Periodic snapshot generation for UI

Performance notes:
- Uses orjson for fast JSON parsing (falls back to json)
- Minimal logging in hot path
- All I/O is non-blocking (pure asyncio)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Callable

# Try fast JSON first
try:
    import orjson
    def json_loads(data: bytes | str) -> dict:
        return orjson.loads(data)
except ImportError:
    import json
    def json_loads(data: bytes | str) -> dict:
        return json.loads(data)

import aiohttp

from .orderbook import OrderBook
from ..engine.flows import FlowEngine
from ..types import DOMSnapshot, FlowLevel, Trade

# Binance Futures endpoints
REST_BASE = "https://fapi.binance.com"
WS_BASE = "wss://fstream.binance.com"


class BinanceClient:
    """
    Async Binance Futures client for order book + trade streams.
    
    Usage:
        client = BinanceClient("BNBUSDT", bin_size=1.0)
        async for snapshot in client.run():
            # Process DOM snapshot
            pass
    """
    
    def __init__(
        self,
        symbol: str,
        bin_size: float = 1.0,
        levels: int = 25,
        flow_window_sec: float = 60.0,
        snapshot_interval_ms: int = 100,  # Push to UI every 100ms
    ) -> None:
        self.symbol = symbol.upper()
        self.symbol_lower = symbol.lower()
        self.bin_size = bin_size
        self.levels = levels
        self.snapshot_interval_ms = snapshot_interval_ms
        
        # Core components
        self.orderbook = OrderBook(self.symbol)
        self.flows = FlowEngine(bin_size=bin_size, window_sec=flow_window_sec)
        
        # State
        self._running = False
        self._snapshot_loaded = False
        self._buffered_updates: list[dict] = []
        self._last_snapshot_time: float = 0.0

        # Rolling update rate tracking
        import time as _time
        self._update_count: int = 0
        self._update_count_last: int = 0
        self._rate_calc_time: float = _time.perf_counter()
        self._updates_per_sec: float = 0.0

        # Output queue for UI - use thread-safe queue for cross-thread access
        import queue
        self.snapshot_queue: queue.Queue[DOMSnapshot] = queue.Queue(maxsize=5)
    
    async def _fetch_snapshot(self, session: aiohttp.ClientSession) -> dict:
        """Fetch initial order book snapshot via REST."""
        url = f"{REST_BASE}/fapi/v1/depth?symbol={self.symbol}&limit=1000"
        async with session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.read()
            return json_loads(data)
    
    def _build_ws_url(self) -> str:
        """Build combined stream WebSocket URL. Using real-time depth (no interval = fastest)."""
        streams = f"{self.symbol_lower}@depth/{self.symbol_lower}@trade"
        return f"{WS_BASE}/stream?streams={streams}"
    
    _debug_depth_count: int = 0

    def _process_depth_update(self, data: dict) -> None:
        """
        Process depth update with proper sequencing.

        HOT PATH - called ~10x per second.
        """
        import sys
        BinanceClient._debug_depth_count += 1

        if not self._snapshot_loaded:
            # Buffer updates until snapshot is loaded
            self._buffered_updates.append(data)
            if BinanceClient._debug_depth_count < 5:
                print(f"[DEBUG] Buffering depth update (snapshot not loaded yet)", flush=True)
            return

        # Apply update to order book
        applied = self.orderbook.apply_update(data)
        if applied:
            self._update_count += 1

        # Debug: print first few updates
        if BinanceClient._debug_depth_count < 10:
            first_id = data.get('U', data.get('u', 0))
            last_id = data.get('u', 0)
            print(f"[DEBUG] Depth #{BinanceClient._debug_depth_count}: applied={applied}, "
                  f"msg_U={first_id}, msg_u={last_id}, book_last={self.orderbook.last_update_id}, "
                  f"bids={len(self.orderbook.bids)}, asks={len(self.orderbook.asks)}", flush=True)
    
    def _process_trade(self, data: dict) -> None:
        """
        Process trade message.
        
        HOT PATH - called for every trade.
        """
        trade = Trade(
            price=float(data['p']),
            qty=float(data['q']),
            is_buyer_maker=data['m'],
            timestamp_ms=data['T'],
        )
        self.flows.process_trade(trade)
    
    def _maybe_push_snapshot(self) -> None:
        """Push DOM snapshot to queue if interval elapsed."""
        now = time.perf_counter()
        elapsed_ms = (now - self._last_snapshot_time) * 1000

        if elapsed_ms < self.snapshot_interval_ms:
            return

        self._last_snapshot_time = now

        # Calculate rolling update rate
        rate_elapsed = now - self._rate_calc_time
        if rate_elapsed >= 1.0:
            self._updates_per_sec = (self._update_count - self._update_count_last) / rate_elapsed
            self._update_count_last = self._update_count
            self._rate_calc_time = now

        # Build snapshot
        binned = self.orderbook.get_binned_ladder(self.bin_size, self.levels)
        flow_levels = self.flows.merge_with_book(binned)

        best_bid = self.orderbook.best_bid
        best_ask = self.orderbook.best_ask
        mid = self.orderbook.mid_price
        spread_bps = ((best_ask - best_bid) / mid * 10000) if mid > 0 else 0.0

        snapshot = DOMSnapshot(
            symbol=self.symbol,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid,
            spread_bps=spread_bps,
            levels=flow_levels,
            timestamp_ms=int(time.time() * 1000),
            updates_per_sec=self._updates_per_sec,
        )

        # Non-blocking put (drop if queue full) - using thread-safe queue.Queue
        import queue
        try:
            self.snapshot_queue.put_nowait(snapshot)
        except queue.Full:
            # Drop oldest, put newest
            try:
                self.snapshot_queue.get_nowait()
                self.snapshot_queue.put_nowait(snapshot)
            except:
                pass

    async def run(self) -> None:
        """
        Main run loop. Connects to Binance and processes messages.

        Pushes DOMSnapshot to self.snapshot_queue for UI consumption.
        """
        self._running = True

        async with aiohttp.ClientSession() as session:
            # 1. Fetch initial snapshot
            snapshot_data = await self._fetch_snapshot(session)
            self.orderbook.load_snapshot(snapshot_data)
            self._snapshot_loaded = True

            # 2. Apply any buffered updates
            for update in self._buffered_updates:
                self.orderbook.apply_update(update)
            self._buffered_updates.clear()

            # 3. Connect to WebSocket
            ws_url = self._build_ws_url()

            async with session.ws_connect(ws_url) as ws:
                self._last_snapshot_time = time.perf_counter()

                async for msg in ws:
                    if not self._running:
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle_ws_message(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break

    _debug_ws_count: int = 0

    def _handle_ws_message(self, raw: str) -> None:
        """
        Handle incoming WebSocket message.

        HOT PATH - called for every message (~10-100+ per second).
        """
        BinanceClient._debug_ws_count += 1
        data = json_loads(raw)

        # Combined stream format: {stream: "...", data: {...}}
        stream = data.get('stream', '')
        payload = data.get('data', data)

        if BinanceClient._debug_ws_count < 5:
            print(f"[WS] Message #{BinanceClient._debug_ws_count}: stream={stream}", flush=True)

        if '@depth' in stream:
            self._process_depth_update(payload)
        elif '@trade' in stream:
            self._process_trade(payload)

        # Check if we should push a snapshot to UI
        self._maybe_push_snapshot()

    def stop(self) -> None:
        """Signal the client to stop."""
        self._running = False
