"""
D6 — Storm AI Terminal Widgets: ApprovalPrompt
Risk-level-aware tool approval widget. Replaces bare y/n prompts with
a colored, contextualized panel that surfaces the action and its risk tier.
"""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Confirm

_RISK_STYLES: dict[str, tuple[str, str, str]] = {
    "low":      ("green",        "✔",  "LOW"),
    "medium":   ("yellow",       "⚠",  "MEDIUM"),
    "high":     ("bold red",     "✘",  "HIGH"),
    "critical": ("bold white on red", "☠", "CRITICAL"),
}

_DEFAULT_STYLE = ("dim white", "?", "UNKNOWN")


class ApprovalPrompt:
    """
    D6 widget — renders a risk-colored approval panel and returns
    True (approved) or False (denied).

    Usage::

        approved = ApprovalPrompt.ask(
            action="DELETE /data/corpus/*.jsonl (143 files)",
            risk="high",
        )
    """

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render(self, action: str, risk: str = "medium") -> None:
        """Print the approval panel without prompting."""
        style, icon, label = _RISK_STYLES.get(risk.lower(), _DEFAULT_STYLE)

        title = Text(f" {icon}  RISK: {label} ", style=style)
        body = Text.assemble(
            ("Action: ", "bold"),
            (action, "white"),
        )
        self.console.print(
            Panel(body, title=title, border_style=style, expand=False)
        )

    def ask(self, action: str, risk: str = "medium") -> bool:
        """Render panel + prompt. Returns True if approved."""
        self.render(action, risk)
        style, _, _ = _RISK_STYLES.get(risk.lower(), _DEFAULT_STYLE)
        prompt_text = Text("Approve?", style=style)
        # For critical risk, default to No
        default = risk.lower() not in ("high", "critical")
        return Confirm.ask(str(prompt_text), console=self.console, default=default)

    @classmethod
    def prompt(cls, action: str, risk: str = "medium",
               console: Console | None = None) -> bool:
        """Convenience classmethod — one-liner approval gate."""
        return cls(console).ask(action, risk)
