"""
D6 — Storm AI Terminal Widgets: ContextWindow
Visual progress bar showing context window utilization.
Segments: [cached (blue)] [used (green)] [free (dim)] — mirrors
ABLE's CVC context compaction thresholds (80% → compress 60%).
"""

from __future__ import annotations

from rich.console import Console
from rich.columns import Columns
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskID, TextColumn
from rich.text import Text

_WARN_THRESHOLD  = 0.70   # yellow above this
_COMPRESS_THRESHOLD = 0.80   # red / compaction zone


class ContextWindow:
    """
    D6 widget — renders a segmented progress bar for context utilization.

    Segments rendered left-to-right:
    - Cached tokens (blue  — prompt cache hits, free to re-use)
    - Used tokens   (green/yellow/red depending on fill %)
    - Free space    (dim grey)

    Usage::

        ctx = ContextWindow(max_tokens=200_000)
        ctx.update(used=142_000, cached=60_000)
        ctx.print()
    """

    def __init__(self, max_tokens: int = 200_000,
                 console: Console | None = None) -> None:
        self.max_tokens = max_tokens
        self.used: int = 0
        self.cached: int = 0
        self.console = console or Console()

    def update(self, used: int, cached: int = 0) -> None:
        """Set current token counts. cached must be ≤ used."""
        self.used   = min(used, self.max_tokens)
        self.cached = min(cached, self.used)

    @property
    def _pct_used(self) -> float:
        return self.used / self.max_tokens if self.max_tokens else 0.0

    @property
    def _pct_cached(self) -> float:
        return self.cached / self.max_tokens if self.max_tokens else 0.0

    def _bar_color(self) -> str:
        p = self._pct_used
        if p >= _COMPRESS_THRESHOLD:
            return "bold red"
        if p >= _WARN_THRESHOLD:
            return "yellow"
        return "green"

    def _status_label(self) -> str:
        p = self._pct_used
        if p >= _COMPRESS_THRESHOLD:
            return "[bold red]⚠ COMPACTION ZONE[/]"
        if p >= _WARN_THRESHOLD:
            return "[yellow]● FILLING[/]"
        return "[green]● OK[/]"

    def build(self) -> Panel:
        color = self._bar_color()
        pct = self._pct_used
        cached_pct = self._pct_cached
        free = 1.0 - pct

        # Build a simple text bar: [cached▓▓][used██][free░░]
        width = 40
        c_cells = int(cached_pct * width)
        u_cells = int((pct - cached_pct) * width)
        f_cells = width - c_cells - u_cells

        bar = Text()
        bar.append("▓" * c_cells, style="blue")
        bar.append("█" * u_cells, style=color)
        bar.append("░" * f_cells, style="dim white")

        stats = Text.assemble(
            ("  Cached: ", "dim"), (f"{self.cached:,}", "blue"),
            ("  Used: ",   "dim"), (f"{self.used:,}",   color),
            ("  Free: ",   "dim"), (f"{self.max_tokens - self.used:,}", "dim white"),
            ("  Max: ",    "dim"), (f"{self.max_tokens:,}", "white"),
            ("  ", ""),
            Text.from_markup(self._status_label()),
        )

        body = Text.assemble(
            "[", style="dim"
        )
        # rebuild cleanly
        full = Text()
        full.append("[", style="dim")
        full.append_text(bar)
        full.append("]", style="dim")
        full.append(f"  {pct*100:.1f}%", style=color)
        full.append("\n")
        full.append_text(stats)

        return Panel(full, title="[bold cyan]Context Window[/]",
                     subtitle=f"[dim]{self.max_tokens:,} token limit[/]",
                     expand=False)

    def print(self) -> None:
        self.console.print(self.build())
