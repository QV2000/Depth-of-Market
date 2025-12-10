#!/usr/bin/env python3
"""
DOM Viewer GUI - Standalone window version.

Usage:
    python -m dom_viewer.gui BNBUSDT --bin-size 0.1 --levels 25
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading


def run_async_feed(client, loop: asyncio.AbstractEventLoop) -> None:
    """Run the async data feed in a separate thread."""
    import traceback
    try:
        print("[FEED] Starting async feed thread...", flush=True)
        asyncio.set_event_loop(loop)
        loop.run_until_complete(client.run())
    except Exception as e:
        print(f"[FEED] ERROR in feed thread: {e}", flush=True)
        traceback.print_exc()


def main(symbol: str, bin_size: float, levels: int) -> None:
    """Main entry point - runs data feed in background, GUI in main thread."""
    
    from .datafeed.binance_client import BinanceClient
    from .ui.dom_window import run_gui
    
    print(f"Starting DOM Viewer GUI for {symbol}...")
    print(f"  Bin size: {bin_size}")
    print(f"  Levels: {levels}")
    print()
    
    # Create client with shared queue
    client = BinanceClient(
        symbol=symbol,
        bin_size=bin_size,
        levels=levels,
        flow_window_sec=60.0,
        snapshot_interval_ms=16,  # ~60 FPS target
    )
    
    # Create event loop for async operations
    loop = asyncio.new_event_loop()
    
    # Start data feed in background thread
    feed_thread = threading.Thread(
        target=run_async_feed, 
        args=(client, loop),
        daemon=True
    )
    feed_thread.start()
    
    # Run GUI in main thread (required by Qt)
    try:
        run_gui(client.snapshot_queue, loop)
    finally:
        client.stop()
        loop.call_soon_threadsafe(loop.stop)


def cli() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="DOM Viewer GUI - Standalone window for Binance Futures",
    )
    
    parser.add_argument(
        "symbol",
        nargs="?",
        default="BNBUSDT",
        help="Trading symbol (default: BNBUSDT)"
    )
    
    parser.add_argument(
        "--bin-size",
        type=float,
        default=0.1,
        help="Price bin size for aggregation (default: 0.1)"
    )
    
    parser.add_argument(
        "--levels",
        type=int,
        default=25,
        help="Number of price levels to show (default: 25)"
    )
    
    args = parser.parse_args()
    
    try:
        main(args.symbol, args.bin_size, args.levels)
    except KeyboardInterrupt:
        print("\nShutdown requested.")
        sys.exit(0)


if __name__ == "__main__":
    cli()

