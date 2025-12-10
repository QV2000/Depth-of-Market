"""
DOM Ladder TUI using Textual.

Displays:
- Left: Heatmap bars for resting bid/ask volume
- Middle: Price ladder with best bid/ask
- Right: Flow columns (bid traded, ask traded, delta)

Performance notes:
- Renders at max ~10 FPS to avoid CPU waste
- Reuses Rich Text objects where possible
- Minimal widget tree updates
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from rich.console import RenderableType
from rich.style import Style
from rich.table import Table
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Static

if TYPE_CHECKING:
    from ..types import DOMSnapshot, FlowLevel

# Color scheme (dark theme)
BID_COLOR = "#22c55e"      # Green
ASK_COLOR = "#ef4444"      # Red
DELTA_POS_COLOR = "#22c55e"
DELTA_NEG_COLOR = "#ef4444"
PRICE_COLOR = "#f8fafc"
HEADER_COLOR = "#94a3b8"
BAR_BG = "#1e293b"


def format_qty(qty: float) -> str:
    """Format quantity for display."""
    if qty >= 1000:
        return f"{qty/1000:.1f}K"
    elif qty >= 1:
        return f"{qty:.1f}"
    else:
        return f"{qty:.3f}"


def make_bar(value: float, max_value: float, width: int, color: str) -> Text:
    """Create a horizontal bar using block characters."""
    if max_value <= 0:
        return Text(" " * width)
    
    fill_ratio = min(1.0, value / max_value)
    fill_width = int(fill_ratio * width)
    
    bar = "█" * fill_width + " " * (width - fill_width)
    return Text(bar, style=Style(color=color, bgcolor=BAR_BG))


class DOMTable(Static):
    """Main DOM ladder display widget."""
    
    DEFAULT_CSS = """
    DOMTable {
        width: 100%;
        height: 100%;
    }
    """
    
    def __init__(self) -> None:
        super().__init__()
        self._snapshot: DOMSnapshot | None = None
    
    def update_snapshot(self, snapshot: DOMSnapshot) -> None:
        """Update with new DOM snapshot."""
        self._snapshot = snapshot
        self.refresh()
    
    def render(self) -> RenderableType:
        """Render the DOM ladder as a Rich Table."""
        if self._snapshot is None:
            return Text("Waiting for data...", style="dim")
        
        snap = self._snapshot
        levels = snap.levels
        
        if not levels:
            return Text("No levels", style="dim")
        
        # Find max values for bar scaling
        max_bid_rest = max((l.bid_resting_qty for l in levels), default=1.0) or 1.0
        max_ask_rest = max((l.ask_resting_qty for l in levels), default=1.0) or 1.0
        max_rest = max(max_bid_rest, max_ask_rest)
        
        max_bid_flow = max((l.bid_traded_qty for l in levels), default=1.0) or 1.0
        max_ask_flow = max((l.ask_traded_qty for l in levels), default=1.0) or 1.0
        max_flow = max(max_bid_flow, max_ask_flow)
        max_delta = max((abs(l.delta_qty) for l in levels), default=1.0) or 1.0
        
        # Build table
        table = Table(
            show_header=True,
            header_style=HEADER_COLOR,
            box=None,
            padding=(0, 1),
            collapse_padding=True,
        )
        
        # Columns
        table.add_column("Bid Vol", justify="right", width=10)
        table.add_column("Bid Bar", justify="left", width=12, no_wrap=True)
        table.add_column("Price", justify="center", width=12)
        table.add_column("Ask Bar", justify="left", width=12, no_wrap=True)
        table.add_column("Ask Vol", justify="left", width=10)
        table.add_column("│", justify="center", width=1)
        table.add_column("Bid Flow", justify="right", width=8)
        table.add_column("Ask Flow", justify="left", width=8)
        table.add_column("Delta", justify="right", width=8)
        
        # Add rows (levels are sorted descending by price)
        for level in levels:
            # Determine if this is bid or ask side
            is_bid_level = level.bid_resting_qty > 0
            is_ask_level = level.ask_resting_qty > 0
            
            # Price styling
            if level.price >= snap.best_ask:
                price_style = ASK_COLOR
            elif level.price <= snap.best_bid:
                price_style = BID_COLOR
            else:
                price_style = PRICE_COLOR
            
            # Bid volume and bar
            bid_vol_text = format_qty(level.bid_resting_qty) if level.bid_resting_qty > 0 else ""
            bid_bar = make_bar(level.bid_resting_qty, max_rest, 12, BID_COLOR)
            
            # Ask volume and bar
            ask_vol_text = format_qty(level.ask_resting_qty) if level.ask_resting_qty > 0 else ""
            ask_bar = make_bar(level.ask_resting_qty, max_rest, 12, ASK_COLOR)
            
            # Flow columns
            bid_flow = format_qty(level.bid_traded_qty) if level.bid_traded_qty > 0 else ""
            ask_flow = format_qty(level.ask_traded_qty) if level.ask_traded_qty > 0 else ""
            
            # Delta with color
            if level.delta_qty > 0:
                delta_text = Text(f"+{format_qty(level.delta_qty)}", style=DELTA_POS_COLOR)
            elif level.delta_qty < 0:
                delta_text = Text(f"{format_qty(level.delta_qty)}", style=DELTA_NEG_COLOR)
            else:
                delta_text = Text("")
            
            table.add_row(
                Text(bid_vol_text, style=BID_COLOR),
                bid_bar,
                Text(f"{level.price:.2f}", style=price_style),
                ask_bar,
                Text(ask_vol_text, style=ASK_COLOR),
                Text("│", style="dim"),
                Text(bid_flow, style=BID_COLOR),
                Text(ask_flow, style=ASK_COLOR),
                delta_text,
            )
        
        return table


class StatusBar(Static):
    """Status bar showing symbol, spread, and performance metrics."""

    DEFAULT_CSS = """
    StatusBar {
        dock: top;
        height: 3;
        padding: 0 2;
        background: #0f172a;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._snapshot: DOMSnapshot | None = None

    def update_snapshot(self, snapshot: DOMSnapshot) -> None:
        self._snapshot = snapshot
        self.refresh()

    def render(self) -> RenderableType:
        if self._snapshot is None:
            return Text("Connecting...", style="dim")

        snap = self._snapshot

        parts = [
            Text(f" {snap.symbol} ", style="bold white on #1e40af"),
            Text("  "),
            Text("Bid: ", style="dim"),
            Text(f"{snap.best_bid:.2f}", style=BID_COLOR),
            Text("  Ask: ", style="dim"),
            Text(f"{snap.best_ask:.2f}", style=ASK_COLOR),
            Text("  Spread: ", style="dim"),
            Text(f"{snap.spread_bps:.1f}bps", style="yellow"),
            Text("  │  ", style="dim"),
            Text("Updates/s: ", style="dim"),
            Text(f"{snap.updates_per_sec:.0f}", style="cyan"),
        ]

        result = Text()
        for p in parts:
            result.append(p)
        return result


class DOMApp(App):
    """Main DOM Viewer application."""

    CSS = """
    Screen {
        background: #0f172a;
    }

    #main-container {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reset_flows", "Reset Flows"),
    ]

    def __init__(self, snapshot_queue: asyncio.Queue) -> None:
        super().__init__()
        self.snapshot_queue = snapshot_queue
        self._status_bar: StatusBar | None = None
        self._dom_table: DOMTable | None = None

    def compose(self) -> ComposeResult:
        self._status_bar = StatusBar()
        self._dom_table = DOMTable()

        yield self._status_bar
        yield Container(self._dom_table, id="main-container")
        yield Footer()

    async def on_mount(self) -> None:
        """Start the snapshot consumer task."""
        self.run_worker(self._consume_snapshots(), exclusive=True)

    async def _consume_snapshots(self) -> None:
        """Consume snapshots from the queue and update UI."""
        while True:
            try:
                snapshot = await asyncio.wait_for(
                    self.snapshot_queue.get(),
                    timeout=1.0
                )

                if self._status_bar:
                    self._status_bar.update_snapshot(snapshot)
                if self._dom_table:
                    self._dom_table.update_snapshot(snapshot)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def action_reset_flows(self) -> None:
        """Reset flow data (bound to 'r' key)."""
        # This would need a reference to the flow engine
        pass


async def run_ui(snapshot_queue: asyncio.Queue) -> None:
    """Run the TUI application."""
    app = DOMApp(snapshot_queue)
    await app.run_async()
