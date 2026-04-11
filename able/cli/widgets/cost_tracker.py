"""
D6 — Storm AI Terminal Widgets: CostTracker
Real-time token usage and estimated cost per provider tier.
Tracks ABLE's 5-tier routing model (see CLAUDE.md Model Routing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Cost per 1M tokens (input/output) — aligned with ABLE routing config
_TIER_META: dict[str, dict] = {
    "T1 Mini":    {"color": "dim green",  "input": 0.00,  "output": 0.00,  "label": "GPT 5.4 Mini / Nemotron"},
    "T2 GPT5.4":  {"color": "green",      "input": 0.00,  "output": 0.00,  "label": "GPT 5.4 / MiMo-V2-Pro"},
    "T3 MiniMax": {"color": "yellow",     "input": 0.30,  "output": 1.20,  "label": "MiniMax M2.7 (evolution)"},
    "T4 Opus":    {"color": "bold red",   "input": 15.00, "output": 75.00, "label": "Claude Opus 4.6"},
    "T5 Local":   {"color": "dim cyan",   "input": 0.00,  "output": 0.00,  "label": "Ollama Qwen 27B/9B"},
}


@dataclass
class _TierUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def cost(self, input_rate: float, output_rate: float) -> float:
        return (self.input_tokens / 1_000_000 * input_rate
                + self.output_tokens / 1_000_000 * output_rate)


class CostTracker:
    """
    D6 widget — tracks per-tier token counts and estimated cost.

    Usage::

        tracker = CostTracker()
        tracker.record("T4 Opus", input_tokens=1200, output_tokens=400)
        tracker.print()
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._usage: dict[str, _TierUsage] = {t: _TierUsage() for t in _TIER_META}

    def record(self, tier: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Add token usage for a tier. Creates entry if tier key unknown."""
        if tier not in self._usage:
            _TIER_META[tier] = {"color": "white", "input": 0.0, "output": 0.0, "label": tier}
            self._usage[tier] = _TierUsage()
        u = self._usage[tier]
        u.input_tokens += input_tokens
        u.output_tokens += output_tokens
        u.calls += 1

    def total_cost(self) -> float:
        return sum(
            u.cost(_TIER_META[t]["input"], _TIER_META[t]["output"])
            for t, u in self._usage.items()
        )

    def build_table(self) -> Table:
        tbl = Table(show_header=True, header_style="bold cyan", expand=False)
        tbl.add_column("Tier",   style="bold", no_wrap=True)
        tbl.add_column("Model",  style="dim")
        tbl.add_column("Calls",  justify="right")
        tbl.add_column("In (K)", justify="right")
        tbl.add_column("Out (K)",justify="right")
        tbl.add_column("Cost",   justify="right", style="yellow")

        for tier, meta in _TIER_META.items():
            u = self._usage[tier]
            if u.calls == 0:
                continue
            cost = u.cost(meta["input"], meta["output"])
            tbl.add_row(
                f"[{meta['color']}]{tier}[/]",
                meta["label"],
                str(u.calls),
                f"{u.input_tokens/1000:.1f}",
                f"{u.output_tokens/1000:.1f}",
                f"${cost:.4f}",
            )
        return tbl

    def print(self) -> None:
        tbl = self.build_table()
        total = self.total_cost()
        self.console.print(Panel(
            tbl,
            title="[bold cyan]Token Usage & Cost[/]",
            subtitle=f"[yellow]Total: ${total:.4f}[/]",
            expand=False,
        ))
