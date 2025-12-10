#!/usr/bin/env python3
"""
Micro-benchmark for DOM Viewer performance.

Tests:
1. Order book update throughput
2. Flow engine trade processing throughput
3. Binned ladder generation speed
4. Full snapshot generation speed

Usage:
    python -m dom_viewer.benchmark
"""

from __future__ import annotations

import random
import time
from statistics import mean, stdev

from .datafeed.orderbook import OrderBook
from .engine.flows import FlowEngine
from .types import Trade


def generate_mock_snapshot(base_price: float = 600.0, levels: int = 1000) -> dict:
    """Generate a mock order book snapshot."""
    tick_size = 0.01
    
    bids = []
    asks = []
    
    for i in range(levels):
        bid_price = base_price - (i + 1) * tick_size
        ask_price = base_price + (i + 1) * tick_size
        
        bids.append([str(bid_price), str(random.uniform(1, 100))])
        asks.append([str(ask_price), str(random.uniform(1, 100))])
    
    return {
        'lastUpdateId': 1000000,
        'bids': bids,
        'asks': asks,
    }


def generate_mock_update(base_price: float, update_id: int, changes: int = 50) -> dict:
    """Generate a mock depth update."""
    tick_size = 0.01
    
    bids = []
    asks = []
    
    for _ in range(changes // 2):
        offset = random.randint(1, 500)
        bid_price = base_price - offset * tick_size
        ask_price = base_price + offset * tick_size
        
        # Random qty (0 = remove level)
        bid_qty = random.uniform(0, 100) if random.random() > 0.2 else 0
        ask_qty = random.uniform(0, 100) if random.random() > 0.2 else 0
        
        bids.append([str(bid_price), str(bid_qty)])
        asks.append([str(ask_price), str(ask_qty)])
    
    return {
        'U': update_id,
        'u': update_id,
        'b': bids,
        'a': asks,
    }


def benchmark_orderbook_updates(iterations: int = 10000) -> None:
    """Benchmark order book update throughput."""
    print("\n=== Order Book Update Benchmark ===")
    
    ob = OrderBook("BNBUSDT")
    ob.load_snapshot(generate_mock_snapshot())
    
    # Pre-generate updates
    updates = [generate_mock_update(600.0, i + 1, changes=50) for i in range(iterations)]
    
    # Warm up
    for u in updates[:100]:
        ob.apply_update(u)
    ob.last_update_id = 0  # Reset
    
    # Benchmark
    start = time.perf_counter()
    for u in updates:
        ob.apply_update(u)
    elapsed = time.perf_counter() - start
    
    rate = iterations / elapsed
    print(f"  Updates applied: {iterations:,}")
    print(f"  Time: {elapsed*1000:.1f}ms")
    print(f"  Rate: {rate:,.0f} updates/sec")
    print(f"  Per update: {elapsed/iterations*1_000_000:.1f}µs")


def benchmark_flow_engine(iterations: int = 100000) -> None:
    """Benchmark flow engine trade processing."""
    print("\n=== Flow Engine Benchmark ===")
    
    flow = FlowEngine(bin_size=0.1, window_sec=60.0)
    
    # Pre-generate trades
    base_price = 600.0
    trades = []
    base_ts = int(time.time() * 1000)
    
    for i in range(iterations):
        trades.append(Trade(
            price=base_price + random.uniform(-5, 5),
            qty=random.uniform(0.1, 10),
            is_buyer_maker=random.random() > 0.5,
            timestamp_ms=base_ts + i * 10,  # 10ms apart
        ))
    
    # Benchmark
    start = time.perf_counter()
    for t in trades:
        flow.process_trade(t)
    elapsed = time.perf_counter() - start
    
    rate = iterations / elapsed
    print(f"  Trades processed: {iterations:,}")
    print(f"  Time: {elapsed*1000:.1f}ms")
    print(f"  Rate: {rate:,.0f} trades/sec")
    print(f"  Per trade: {elapsed/iterations*1_000_000:.2f}µs")


def benchmark_binned_ladder(iterations: int = 1000) -> None:
    """Benchmark binned ladder generation."""
    print("\n=== Binned Ladder Generation Benchmark ===")
    
    ob = OrderBook("BNBUSDT")
    ob.load_snapshot(generate_mock_snapshot())
    
    # Warm up
    for _ in range(10):
        ob.get_binned_ladder(0.1, 25)
    
    # Benchmark
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        ob.get_binned_ladder(0.1, 25)
        times.append(time.perf_counter() - start)
    
    avg_time = mean(times) * 1000
    std_time = stdev(times) * 1000
    
    print(f"  Iterations: {iterations}")
    print(f"  Avg time: {avg_time:.3f}ms")
    print(f"  Std dev: {std_time:.3f}ms")
    print(f"  Rate: {1000/avg_time:,.0f} calls/sec")


def benchmark_full_snapshot(iterations: int = 500) -> None:
    """Benchmark full snapshot generation (what UI needs)."""
    print("\n=== Full Snapshot Generation Benchmark ===")
    
    ob = OrderBook("BNBUSDT")
    ob.load_snapshot(generate_mock_snapshot())
    
    flow = FlowEngine(bin_size=0.1)
    base_ts = int(time.time() * 1000)
    for i in range(1000):
        flow.process_trade(Trade(
            price=600.0 + random.uniform(-5, 5),
            qty=random.uniform(0.1, 10),
            is_buyer_maker=random.random() > 0.5,
            timestamp_ms=base_ts + i * 10,
        ))
    
    # Benchmark full snapshot generation
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        binned = ob.get_binned_ladder(0.1, 25)
        flow_levels = flow.merge_with_book(binned)
        times.append(time.perf_counter() - start)
    
    avg_time = mean(times) * 1000
    std_time = stdev(times) * 1000
    
    print(f"  Iterations: {iterations}")
    print(f"  Avg time: {avg_time:.3f}ms")
    print(f"  Std dev: {std_time:.3f}ms")
    print(f"  Max FPS possible: {1000/avg_time:,.0f}")


def main() -> None:
    """Run all benchmarks."""
    print("=" * 60)
    print("DOM Viewer Performance Benchmark")
    print("=" * 60)
    
    benchmark_orderbook_updates()
    benchmark_flow_engine()
    benchmark_binned_ladder()
    benchmark_full_snapshot()
    
    print("\n" + "=" * 60)
    print("Benchmark complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()

