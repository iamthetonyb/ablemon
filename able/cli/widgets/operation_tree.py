"""
D6 — Storm AI Terminal Widgets: OperationTree
Live Rich Tree showing tool calls with status icons.
Active calls display a spinner; completed show ✔; failed show ✘.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.tree import Tree


class OpStatus(str, Enum):
    ACTIVE    = "active"
    COMPLETED = "completed"
    FAILED    = "failed"


@dataclass
class _Op:
    name: str
    args_summary: str = ""
    status: OpStatus = OpStatus.ACTIVE
    result_summary: str = ""
    children: list[_Op] = field(default_factory=list)


_STATUS_ICON: dict[OpStatus, str] = {
    OpStatus.ACTIVE:    "[yellow]⟳[/]",
    OpStatus.COMPLETED: "[green]✔[/]",
    OpStatus.FAILED:    "[red]✘[/]",
}


class OperationTree:
    """
    D6 widget — tracks tool calls in a Rich Tree with live status icons.

    Usage::

        tree = OperationTree("Agent run")
        op_id = tree.add("read_file", args="context.py")
        with tree.live():
            ...do work...
            tree.complete(op_id, "42 lines read")
    """

    def __init__(self, root_label: str = "Operations", console: Console | None = None) -> None:
        self._root_label = root_label
        self._ops: dict[str, _Op] = {}
        self._order: list[str] = []
        self.console = console or Console()

    def add(self, name: str, args_summary: str = "", op_id: str | None = None) -> str:
        """Register a new tool call. Returns op_id."""
        oid = op_id or f"{name}:{len(self._ops)}"
        self._ops[oid] = _Op(name=name, args_summary=args_summary)
        self._order.append(oid)
        return oid

    def complete(self, op_id: str, result: str = "") -> None:
        if op_id in self._ops:
            self._ops[op_id].status = OpStatus.COMPLETED
            self._ops[op_id].result_summary = result

    def fail(self, op_id: str, error: str = "") -> None:
        if op_id in self._ops:
            self._ops[op_id].status = OpStatus.FAILED
            self._ops[op_id].result_summary = error

    def _build(self) -> Tree:
        root = Tree(f"[bold cyan]{self._root_label}[/]")
        for oid in self._order:
            op = self._ops[oid]
            icon = _STATUS_ICON[op.status]
            label = Text.from_markup(
                f"{icon} [bold]{op.name}[/]"
                + (f"  [dim]{op.args_summary}[/]" if op.args_summary else "")
                + (f"  → [italic]{op.result_summary}[/]" if op.result_summary else "")
            )
            root.add(label)
        return root

    def live(self, refresh_per_second: int = 4) -> Live:
        """Return a Rich Live context manager that auto-refreshes the tree."""
        return Live(self._build, console=self.console,
                    refresh_per_second=refresh_per_second)

    def print(self) -> None:
        """Static one-shot render (no live updates)."""
        self.console.print(self._build())
