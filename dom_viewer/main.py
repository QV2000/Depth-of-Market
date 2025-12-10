#!/usr/bin/env python3
"""
DOM Viewer - High-performance Depth of Market visualization for Binance Futures.

Usage:
    python -m dom_viewer.main --symbol BNBUSDT --bin-size 0.1 --levels 25
    
    Or directly:
    python dom_viewer/main.py BNBUSDT

Controls:
    q - Quit
    r - Reset flow counters
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def main(symbol: str, bin_size: float, levels: int) -> None:
    """Main entry point - runs data feed and UI concurrently."""
    
    # Import here to avoid slow startup for --help
    from .datafeed.binance_client import BinanceClient
    from .ui.dom_view import run_ui
    
    print(f"Starting DOM Viewer for {symbol}...")
    print(f"  Bin size: {bin_size}")
    print(f"  Levels: {levels}")
    print()
    
    # Create client
    client = BinanceClient(
        symbol=symbol,
        bin_size=bin_size,
        levels=levels,
        flow_window_sec=60.0,
        snapshot_interval_ms=100,
    )
    
    # Run data feed and UI concurrently
    async def run_feed() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Feed error: {e}")
    
    # Create tasks
    feed_task = asyncio.create_task(run_feed())
    
    try:
        # Run UI (blocks until quit)
        await run_ui(client.snapshot_queue)
    finally:
        # Cleanup
        client.stop()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass


def cli() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="DOM Viewer - High-performance Depth of Market for Binance Futures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m dom_viewer.main BNBUSDT
    python -m dom_viewer.main BTCUSDT --bin-size 10 --levels 30
    python -m dom_viewer.main ETHUSDT --bin-size 1 --levels 25
        """
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
    
    parser.add_argument(
        "--flow-window",
        type=float,
        default=60.0,
        help="Flow rolling window in seconds (default: 60)"
    )
    
    args = parser.parse_args()
    
    # Run
    try:
        asyncio.run(main(args.symbol, args.bin_size, args.levels))
    except KeyboardInterrupt:
        print("\nShutdown requested.")
        sys.exit(0)


if __name__ == "__main__":
    cli()

