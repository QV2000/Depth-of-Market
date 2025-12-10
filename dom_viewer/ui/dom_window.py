"""
DOM Ladder GUI using PyQtGraph - pops out as a standalone window.

High-performance real-time visualization with:
- Horizontal bar charts for bid/ask volume
- Price ladder in center
- Flow columns on the right
"""

from __future__ import annotations

import asyncio
import queue
import sys
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QFrame
)

if TYPE_CHECKING:
    from ..types import DOMSnapshot, FlowLevel

# Colors
BID_COLOR = QColor(34, 197, 94)      # Green
ASK_COLOR = QColor(239, 68, 68)      # Red
BG_COLOR = QColor(15, 23, 42)        # Dark blue-gray
HEADER_BG = QColor(30, 41, 59)
TEXT_COLOR = QColor(248, 250, 252)
DELTA_POS = QColor(34, 197, 94)
DELTA_NEG = QColor(239, 68, 68)


def format_qty(qty: float) -> str:
    """Format quantity for display."""
    if qty >= 1000:
        return f"{qty/1000:.1f}K"
    elif qty >= 1:
        return f"{qty:.1f}"
    elif qty > 0:
        return f"{qty:.2f}"
    return ""


class DOMWindow(QMainWindow):
    """Main DOM Viewer window."""

    def __init__(self, snapshot_queue: queue.Queue) -> None:
        super().__init__()
        self.snapshot_queue = snapshot_queue
        self._last_snapshot: DOMSnapshot | None = None
        self._table_items: list[list[QTableWidgetItem]] = []  # Cache table items

        self.setWindowTitle("DOM Viewer - BNBUSDT")
        self.setMinimumSize(900, 700)
        self.setStyleSheet(f"background-color: {BG_COLOR.name()}; color: {TEXT_COLOR.name()};")

        self._setup_ui()
        self._setup_timer()
    
    def _setup_ui(self) -> None:
        """Build the UI."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header
        self.header = QLabel("Connecting...")
        self.header.setFont(QFont("Consolas", 14, QFont.Weight.Bold))
        self.header.setStyleSheet(f"background-color: {HEADER_BG.name()}; padding: 10px;")
        layout.addWidget(self.header)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Bid Vol", "Bid Bar", "Price", "Ask Bar", "Ask Vol", 
            "│", "Bid Flow", "Ask Flow", "Delta"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {BG_COLOR.name()};
                gridline-color: {HEADER_BG.name()};
                font-family: Consolas;
                font-size: 12px;
            }}
            QHeaderView::section {{
                background-color: {HEADER_BG.name()};
                color: {TEXT_COLOR.name()};
                padding: 5px;
                border: none;
            }}
        """)
        layout.addWidget(self.table)
    
    def _setup_timer(self) -> None:
        """Setup timer to poll snapshot queue."""
        self.timer = QTimer()
        self.timer.timeout.connect(self._poll_snapshots)
        self.timer.start(16)  # ~60 FPS

    def _poll_snapshots(self) -> None:
        """Poll for new snapshots from the thread-safe queue."""
        try:
            # Drain queue, keep only latest
            latest = None
            while True:
                try:
                    latest = self.snapshot_queue.get_nowait()
                except queue.Empty:
                    break

            if latest:
                self._last_snapshot = latest
                self._update_display(latest)
        except Exception as e:
            print(f"Poll error: {e}")
    
    def _update_display(self, snap: DOMSnapshot) -> None:
        """Update the display with new snapshot. Optimized for speed."""
        # Update header
        self.header.setText(
            f"  {snap.symbol}  │  Bid: {snap.best_bid:.2f}  │  "
            f"Ask: {snap.best_ask:.2f}  │  Spread: {snap.spread_bps:.1f}bps  │  "
            f"Updates/s: {snap.updates_per_sec:.0f}"
        )

        levels = snap.levels
        if not levels:
            return

        # Ensure table has correct row count and cached items
        n_levels = len(levels)
        if len(self._table_items) != n_levels:
            self._init_table_items(n_levels)

        # Find max values for scaling
        max_rest = max(max(l.bid_resting_qty for l in levels),
                       max(l.ask_resting_qty for l in levels), 1)

        # Block signals during bulk update for performance
        self.table.blockSignals(True)

        for row, level in enumerate(levels):
            items = self._table_items[row]

            # Bid volume (col 0)
            items[0].setText(format_qty(level.bid_resting_qty))
            items[0].setForeground(BID_COLOR if level.bid_resting_qty > 0 else TEXT_COLOR)

            # Bid bar (col 1)
            bar_len = int(20 * level.bid_resting_qty / max_rest) if max_rest > 0 else 0
            items[1].setText("█" * bar_len)

            # Price (col 2)
            if level.price >= snap.best_ask:
                price_color = ASK_COLOR
            elif level.price <= snap.best_bid:
                price_color = BID_COLOR
            else:
                price_color = TEXT_COLOR
            items[2].setText(f"{level.price:.2f}")
            items[2].setForeground(price_color)

            # Ask bar (col 3)
            bar_len = int(20 * level.ask_resting_qty / max_rest) if max_rest > 0 else 0
            items[3].setText("█" * bar_len)

            # Ask volume (col 4)
            items[4].setText(format_qty(level.ask_resting_qty))
            items[4].setForeground(ASK_COLOR if level.ask_resting_qty > 0 else TEXT_COLOR)

            # Separator stays the same (col 5)

            # Bid flow (col 6)
            items[6].setText(format_qty(level.bid_traded_qty))
            items[6].setForeground(BID_COLOR if level.bid_traded_qty > 0 else TEXT_COLOR)

            # Ask flow (col 7)
            items[7].setText(format_qty(level.ask_traded_qty))
            items[7].setForeground(ASK_COLOR if level.ask_traded_qty > 0 else TEXT_COLOR)

            # Delta (col 8)
            if level.delta_qty > 0:
                items[8].setText(f"+{format_qty(level.delta_qty)}")
                items[8].setForeground(DELTA_POS)
            elif level.delta_qty < 0:
                items[8].setText(format_qty(level.delta_qty))
                items[8].setForeground(DELTA_NEG)
            else:
                items[8].setText("")

        self.table.blockSignals(False)
        self.table.viewport().update()

    def _init_table_items(self, n_rows: int) -> None:
        """Initialize table with reusable items."""
        self.table.setRowCount(n_rows)
        self._table_items = []

        for row in range(n_rows):
            row_items = []
            for col in range(9):
                item = QTableWidgetItem("")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 2:  # Price column
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 5:  # Separator
                    item.setText("│")
                    item.setForeground(QColor(100, 100, 100))
                if col == 1:  # Bid bar
                    item.setForeground(BID_COLOR)
                if col == 3:  # Ask bar
                    item.setForeground(ASK_COLOR)
                self.table.setItem(row, col, item)
                row_items.append(item)
            self._table_items.append(row_items)


def run_gui(snapshot_queue: queue.Queue, loop: asyncio.AbstractEventLoop) -> None:
    """Run the GUI application (blocking)."""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Modern look

    window = DOMWindow(snapshot_queue)
    window.show()

    app.exec()
