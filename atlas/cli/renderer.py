"""
CLIRenderer — Rich terminal output with graceful fallback to plain text.
"""

import sys
from typing import Any, Dict

_BANNER = r"""
   ___  ______ __   ___   ____
  / _ |/_  __// /  / _ | / __/
 / __ | / /  / /__/ __ |_\ \
/_/ |_|/_/  /____/_/ |_/___/
"""


class CLIRenderer:
    """Terminal output handler. Uses rich if available, else plain text."""

    def __init__(self):
        try:
            from rich.console import Console

            self.console = Console()
            self._rich = True
        except ImportError:
            self._rich = False

    # ── public API ────────────────────────────────────────────────

    def print_banner(self, session_id: str, config: Any) -> None:
        tier_label = f"tier {config.model_tier}" if config.model_tier else "auto"
        mode = "offline" if config.offline else tier_label

        if self._rich:
            from rich.panel import Panel

            self.console.print(
                Panel(
                    f"[bold cyan]{_BANNER}[/bold cyan]\n"
                    f"  Session [bold]{session_id}[/bold]  |  Mode: [bold]{mode}[/bold]  |  /exit to quit",
                    border_style="cyan",
                    expand=False,
                )
            )
        else:
            print(_BANNER)
            print(f"  Session {session_id}  |  Mode: {mode}  |  /exit to quit")
            print()

    def print_response(self, text: str) -> None:
        if not text:
            return
        if self._rich:
            from rich.markdown import Markdown

            self.console.print()
            self.console.print(Markdown(text))
        else:
            print()
            print(text)

    def print_tool_start(self, name: str, args: Dict) -> None:
        summary = ", ".join(f"{k}={_trunc(v)}" for k, v in list(args.items())[:3])
        if self._rich:
            self.console.print(f"  [dim]>> {name}({summary})[/dim]")
        else:
            print(f"  >> {name}({summary})")

    def print_tool_result(self, name: str, result: str) -> None:
        preview = result[:200] + "..." if len(result) > 200 else result
        if self._rich:
            self.console.print(f"  [dim green]<< {name}: {preview}[/dim green]")
        else:
            print(f"  << {name}: {preview}")

    def print_warning(self, text: str) -> None:
        if self._rich:
            self.console.print(f"[bold yellow]WARNING:[/bold yellow] {text}")
        else:
            print(f"WARNING: {text}", file=sys.stderr)

    def print_info(self, text: str) -> None:
        if self._rich:
            self.console.print(f"[dim]{text}[/dim]")
        else:
            print(text)

    def print_error(self, text: str) -> None:
        if self._rich:
            self.console.print(f"[bold red]ERROR:[/bold red] {text}")
        else:
            print(f"ERROR: {text}", file=sys.stderr)


def _trunc(v: Any, limit: int = 40) -> str:
    s = str(v)
    return s[:limit] + "..." if len(s) > limit else s
