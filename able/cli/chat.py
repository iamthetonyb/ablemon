"""Local operator chat CLI for ABLE."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from able.core.approval.workflow import ApprovalResult, ApprovalStatus, ApprovalWorkflow

# ── Session writer (feeds CLISessionHarvester → distillation) ─────────────

_SESSIONS_DIR = Path.home() / ".able" / "sessions"


class _SessionWriter:
    """Append per-turn JSONL to ~/.able/sessions/{session_id}.jsonl.

    This is the bridge between CLI chat and the distillation pipeline.
    CLISessionHarvester reads these files during the nightly harvest.
    """

    def __init__(self, session_id: str) -> None:
        self._dir = _SESSIONS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{session_id}.jsonl"

    def write(self, role: str, content: str, **extra: object) -> None:
        record = {
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass  # Non-fatal — don't break chat for a write failure


# ── ANSI helpers ──────────────────────────────────────────────────────────
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()
_READLINE_READY = False
_READLINE_HISTORY_PATH = Path.home() / ".able" / "history" / "cli_history.txt"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if _COLOR else text


def _save_readline_history() -> None:
    try:
        import readline
    except ImportError:
        return
    try:
        _READLINE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(str(_READLINE_HISTORY_PATH))
    except OSError:
        return


def _enable_line_editing() -> None:
    """Enable arrow-key editing/history for local terminal prompts."""
    global _READLINE_READY
    if _READLINE_READY or not sys.stdin.isatty():
        return
    try:
        import readline
    except ImportError:
        _READLINE_READY = True
        return

    try:
        _READLINE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _READLINE_HISTORY_PATH.exists():
            readline.read_history_file(str(_READLINE_HISTORY_PATH))
        readline.set_history_length(1000)
        doc = (readline.__doc__ or "").lower()
        if "libedit" in doc:
            readline.parse_and_bind("bind ^I rl_insert")
        else:
            readline.parse_and_bind("tab: self-insert")
        atexit.register(_save_readline_history)
    except OSError:
        pass
    _READLINE_READY = True


def _prompt_input(prompt: str) -> str:
    _enable_line_editing()
    return input(prompt)


def _clear_terminal() -> None:
    if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    else:
        print("\n" * 3)


def _truncate_text(text: str, limit: int = 120) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _print_chat_header(buddy, provider_count: int, render_header) -> None:
    if buddy:
        print(f"\n{render_header(buddy, provider_count)}")
    else:
        print(f"\n    {_c(BOLD, 'ABLE')} | {provider_count} AI providers ready")
    print(f"    {_c(DIM, '/help for commands')}\n")


def _print_compact_view(gateway, args, buddy, render_header) -> None:
    _clear_terminal()
    providers = getattr(getattr(gateway, "provider_chain", None), "providers", []) or []
    _print_chat_header(buddy, len(providers), render_header)
    print(f"    {_c(DIM, 'compacted view — transcripts, routing logs, and distillation traces are preserved')}")
    session = gateway.session_mgr.get_or_create(args.session) if getattr(gateway, "session_mgr", None) else None
    if session:
        print(
            f"    {_c(DIM, f'session {args.session} · {session.messages} turns · {session.total_tokens} tokens · avg complexity {session.avg_complexity:.2f}')}"
        )
    recent = []
    try:
        recent = list(reversed(gateway.transcript_manager.get_recent_messages(args.client, limit=4)))
    except Exception:
        recent = []
    if recent:
        print("    recent context")
        for entry in recent:
            direction = str(entry.get("direction", "outbound")) if isinstance(entry, dict) else "outbound"
            label = "you" if direction == "inbound" else "able"
            content = entry.get("message", "") if isinstance(entry, dict) else str(getattr(entry, "content", ""))
            print(f"      {label}: {_truncate_text(content)}")
    print("")


class TerminalApprovalWorkflow(ApprovalWorkflow):
    """Approval workflow that resolves write actions directly in the terminal."""

    def __init__(self, *, auto_approve: bool = False, default_timeout: int = 300):
        super().__init__(owner_id=0, bot=None, default_timeout=default_timeout)
        self._auto_approve = auto_approve
        self._prompt_lock = asyncio.Lock()

    async def request_approval(
        self,
        operation: str,
        details: dict,
        requester_id: str = "system",
        client_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        risk_level: str = "medium",
        context: Optional[str] = None,
    ) -> ApprovalResult:
        if self._auto_approve:
            self._record_outcome(operation, ApprovalStatus.APPROVED, "cli-auto-approve")
            return ApprovalResult(
                request_id="cli-auto",
                status=ApprovalStatus.APPROVED,
                approved_by=0,
                approved_at=datetime.now(timezone.utc),
                reason="Approved automatically for this CLI session",
            )

        async with self._prompt_lock:
            risk_icon = {"low": "\u2714", "medium": "\u26a0", "high": "\u26d4", "critical": "\u2622"}.get(
                risk_level, "\u2022"
            )
            risk_bar = {"low": "\u2591\u2591\u2591\u2591", "medium": "\u2593\u2593\u2591\u2591", "high": "\u2588\u2593\u2591\u2591", "critical": "\u2588\u2588\u2588\u2588"}.get(
                risk_level, "\u2591\u2591\u2591\u2591"
            )
            print(f"\n{'=' * 48}")
            print(f"  {risk_icon} APPROVAL REQUEST  [{risk_bar}] {risk_level.upper()}")
            print(f"{'=' * 48}")
            print(f"  Operation:  {operation}")
            print(f"  Requester:  {requester_id}")
            if client_id:
                print(f"  Client:     {client_id}")
            if context:
                print(f"  Context:    {context}")
            affected = []
            for key in ("resource", "target", "file", "path", "url", "repo"):
                if key in details:
                    affected.append(f"{key}={details[key]}")
            if affected:
                print(f"  Affects:    {', '.join(affected)}")
            print(f"{'─' * 48}")
            details_str = json.dumps(details, indent=2, default=str)
            if len(details_str) > 2000:
                details_str = details_str[:2000] + "\n  ... (truncated)"
            print(details_str)
            print(f"{'─' * 48}")

            while True:
                try:
                    answer = await asyncio.wait_for(
                        asyncio.to_thread(
                            _prompt_input,
                            "approve? [y]es / [n]o / [a]lways for this session: ",
                        ),
                        timeout=timeout_seconds or self.default_timeout,
                    )
                except asyncio.TimeoutError:
                    self._record_outcome(operation, ApprovalStatus.TIMEOUT, "cli-timeout")
                    return ApprovalResult(
                        request_id="cli-timeout",
                        status=ApprovalStatus.TIMEOUT,
                        reason=f"No response within {timeout_seconds or self.default_timeout}s",
                    )
                except (EOFError, KeyboardInterrupt):
                    self._record_outcome(operation, ApprovalStatus.DENIED, "cli-interrupt")
                    return ApprovalResult(
                        request_id="cli-interrupt",
                        status=ApprovalStatus.DENIED,
                        approved_by=0,
                        approved_at=datetime.now(timezone.utc),
                        reason="Terminal input interrupted",
                    )

                normalized = answer.strip().lower()
                if normalized in {"y", "yes"}:
                    self._record_outcome(operation, ApprovalStatus.APPROVED, "cli-approved")
                    return ApprovalResult(
                        request_id="cli-approved",
                        status=ApprovalStatus.APPROVED,
                        approved_by=0,
                        approved_at=datetime.now(timezone.utc),
                        reason="Approved in local CLI",
                    )
                if normalized in {"n", "no"}:
                    self._record_outcome(operation, ApprovalStatus.DENIED, "cli-denied")
                    return ApprovalResult(
                        request_id="cli-denied",
                        status=ApprovalStatus.DENIED,
                        approved_by=0,
                        approved_at=datetime.now(timezone.utc),
                        reason="Denied in local CLI",
                    )
                if normalized in {"a", "all", "always"}:
                    self._auto_approve = True
                    self._record_outcome(operation, ApprovalStatus.APPROVED, "cli-always-approved")
                    return ApprovalResult(
                        request_id="cli-always",
                        status=ApprovalStatus.APPROVED,
                        approved_by=0,
                        approved_at=datetime.now(timezone.utc),
                        reason="Approved and auto-approve enabled for the rest of the session",
                    )
                print("enter y, n, or a")


def configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--session",
        default=os.environ.get("ABLE_CLI_SESSION", "local-cli"),
        help="Conversation/session id used for memory, routing logs, and transcripts.",
    )
    parser.add_argument(
        "--client",
        default=os.environ.get("ABLE_CLI_CLIENT", "master"),
        help="Client/tenant transcript bucket to use (default: master).",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=int(os.environ.get("ABLE_CLI_CONTROL_PORT", "0")),
        help="Start the local health/control API on this port. Defaults to 0 (disabled).",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve all write actions for this CLI session.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output (wait for full response before displaying).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full startup logs (provider registration, etc.).",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="able chat",
        description="Run a local terminal chat session against the ABLE gateway pipeline.",
    )
    return configure_parser(parser)


# ── Thinking spinner ──────────────────────────────────────────────────────

class _Spinner:
    """Async thinking indicator — dots that animate while waiting."""

    _FRAMES = ["\u2808", "\u2800\u2808", "\u2800\u2800\u2808", "\u2800\u2800\u2800\u2808"]

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    def start(self):
        self._task = asyncio.create_task(self._animate())

    async def _animate(self):
        try:
            i = 0
            while True:
                frame = self._FRAMES[i % len(self._FRAMES)]
                sys.stdout.write(f"\r  {_c(DIM, frame)} ")
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            sys.stdout.write("\r" + " " * 20 + "\r")
            sys.stdout.flush()

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None


class _ReasoningPreview:
    """Turn streamed <think> blocks into a short on-screen preview."""

    def __init__(self, limit: int = 220):
        self._in_think = False
        self._shown_chars = 0
        self._limit = limit

    def consume(self, chunk: str) -> tuple[str, str]:
        data = chunk or ""
        thought_parts: list[str] = []
        answer_parts: list[str] = []

        while data:
            if self._in_think:
                end = data.find("</think>")
                if end == -1:
                    thought_parts.append(data)
                    data = ""
                else:
                    thought_parts.append(data[:end])
                    data = data[end + len("</think>"):]
                    self._in_think = False
            else:
                start = data.find("<think>")
                if start == -1:
                    answer_parts.append(data)
                    data = ""
                else:
                    answer_parts.append(data[:start])
                    data = data[start + len("<think>"):]
                    self._in_think = True

        thought = self._clip(" ".join(" ".join(thought_parts).split()))
        answer = "".join(answer_parts)
        return thought, answer

    def _clip(self, text: str) -> str:
        if not text or self._shown_chars >= self._limit:
            return ""
        remaining = self._limit - self._shown_chars
        clipped = text[:remaining]
        self._shown_chars += len(clipped)
        return clipped


# ── Buddy helper (inline setup) ──────────────────────────────────────────

_FOCUS_OPTIONS = [
    ("coding", "Ship code, fix bugs, and lean on tools heavily."),
    ("research", "Gather sources, synthesize context, and investigate."),
    ("operations", "Run deploys, infra, automations, and steady operator work."),
    ("creative", "Write, message, position, and content."),
    ("security", "Audit, harden, and pressure-test the system."),
    ("general-business", "Mixed product, ops, research, and growth work."),
]

_WORK_STYLE_OPTIONS = [
    ("solo-operator", "One operator managing most of the stack."),
    ("builder", "Mostly engineering and product build work."),
    ("client-delivery", "Customer work, launches, and delivery pressure."),
    ("mixed-team", "A rotating mix of build, ops, and review."),
    ("all-terrain", "Dynamic mix of solo build, delivery, ops, and collaboration."),
]

_DISTILLATION_OPTIONS = [
    ("9b-fast-local", "Fast local/T4-first distillation and lighter demos."),
    ("27b-deep-h100", "Heavier H100 runs for deep-quality promotions."),
    ("hybrid", "Iterate on 9B and promote the best work to 27B."),
]


class _SetupExit(Exception):
    """Raised when the user wants to exit during setup."""


_EXIT_WORDS = {"/exit", "/quit", "/q", "exit", "quit"}


def _option_label(value: str, options: list[tuple[str, str]]) -> str:
    for option_value, description in options:
        if option_value == value:
            return option_value.replace("-", " ")
    return value.replace("-", " ")


async def _choose_profile_option(
    title: str,
    options: list[tuple[str, str]],
    *,
    default_value: str | None = None,
) -> str:
    recommended_index = 0
    if default_value:
        for idx, (value, _) in enumerate(options):
            if value == default_value:
                recommended_index = idx
                break
    print(f"\n  {_c(BOLD, title)}")
    for idx, (value, description) in enumerate(options, 1):
        recommended = " (recommended)" if idx - 1 == recommended_index else ""
        print(f"    [{idx}] {value.replace('-', ' ')} — {description}{recommended}")
    while True:
        choice = (await asyncio.to_thread(_prompt_input, "  pick a number (Enter for recommended): ")).strip()
        if choice.lower() in _EXIT_WORDS:
            raise _SetupExit()
        if not choice:
            return options[recommended_index][0]
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(options):
                return options[index][0]
        print(f"  {_c(YELLOW, 'choose one of the listed numbers')}")


def _distillation_hint(track: str) -> str:
    hints = {
        "9b-fast-local": "Distillation lane: start with the 9B T4/local path. It is the fastest way to build corpus and demo quickly.",
        "27b-deep-h100": "Distillation lane: keep collecting locally, then promote the strongest eval-backed corpus to the 27B H100 path.",
        "hybrid": "Distillation lane: iterate on 9B for speed, then promote the best work to 27B when the corpus is ready.",
    }
    return hints.get(track, hints["hybrid"])


async def _buddy_onboarding_flow(update_collection_profile):
    """Capture operator-facing preferences after the starter is chosen."""
    print(f"\n  {_c(BOLD, 'buddy onboarding')}")
    print("  This tunes the companion around your real work patterns and preferred distillation lane.")
    print(f"  {_c(DIM, 'Type /exit at any prompt to quit.')}")
    focus = await _choose_profile_option("Primary focus", _FOCUS_OPTIONS)
    work_style = await _choose_profile_option(
        "Work style",
        _WORK_STYLE_OPTIONS,
        default_value="all-terrain" if focus == "general-business" else "solo-operator",
    )
    distillation_track = await _choose_profile_option("Distillation track", _DISTILLATION_OPTIONS)
    profile = {
        "focus": focus,
        "work_style": work_style,
        "distillation_track": distillation_track,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    collection = update_collection_profile(profile)
    print(
        f"  {_c(GREEN, 'saved')} profile: "
        f"{_option_label(focus, _FOCUS_OPTIONS)} · "
        f"{_option_label(work_style, _WORK_STYLE_OPTIONS)} · "
        f"{_option_label(distillation_track, _DISTILLATION_OPTIONS)}"
    )
    print(f"  {_c(DIM, _distillation_hint(distillation_track))}")
    return collection


async def _buddy_setup_flow(
    STARTER_SPECIES,
    create_starter_buddy,
    save_buddy,
    render_starter_selection,
    update_collection_profile,
    *,
    allow_skip: bool,
):
    """Quick inline buddy creation. Returns buddy or None.

    Only the 5 starter species are selectable. Aether is a hidden unlock.
    Type /exit, /quit, or /q at any prompt to bail out.
    """
    starter_count = len(STARTER_SPECIES)
    print(render_starter_selection())
    while True:
        try:
            choice = await asyncio.to_thread(
                _prompt_input,
                f"  {_c(GREEN, 'pick')} [1-{starter_count}]"
                + (" or Enter to skip: " if allow_skip else ": "),
            )
            choice = choice.strip().lower()
            if choice in _EXIT_WORDS:
                print(_c(DIM, "  bye"))
                raise SystemExit(0)
            if allow_skip and (not choice or choice in {"s", "skip"}):
                return None
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < starter_count:
                    chosen = STARTER_SPECIES[idx]
                    default_name = chosen.value.capitalize()
                    name_input = (await asyncio.to_thread(_prompt_input, f"  {_c(CYAN, 'name')} [{default_name}]: ")).strip()
                    if name_input.lower() in _EXIT_WORDS:
                        print(_c(DIM, "  bye"))
                        raise SystemExit(0)
                    name = name_input or default_name
                    phrase_input = (await asyncio.to_thread(_prompt_input, "  catch phrase (Enter to use the default): ")).strip()
                    if phrase_input.lower() in _EXIT_WORDS:
                        print(_c(DIM, "  bye"))
                        raise SystemExit(0)
                    buddy = create_starter_buddy(
                        name=name,
                        species=chosen,
                        catch_phrase=phrase_input,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    save_buddy(buddy)
                    try:
                        await _buddy_onboarding_flow(update_collection_profile)
                    except _SetupExit:
                        print(_c(DIM, "  skipped onboarding — you can finish later with /buddy setup"))
                    shiny_tag = " shiny!" if buddy.is_shiny else ""
                    print(f"  {buddy.display_emoji} {name} the {buddy.meta['label']} joins you!{shiny_tag}")
                    return buddy
            if allow_skip:
                print(f"  {_c(YELLOW, f'choose 1-{starter_count} or press Enter to skip')}")
            else:
                print(f"  {_c(YELLOW, f'starter selection is required — choose 1-{starter_count}')}")
        except (EOFError, KeyboardInterrupt):
            break
    return None


def _collection_snapshot(collection) -> tuple[set[str], set[str], bool]:
    if not collection:
        return set(), set(), False
    species = set(collection.buddies.keys())
    badges = {badge.get("id", "") for badge in collection.badges}
    return species, badges, bool(collection.easter_egg_title)


def _profile_is_complete(collection) -> bool:
    return bool(collection and collection.operator_profile.get("completed_at"))


def _print_collection_updates(before_collection, after_collection) -> None:
    if not after_collection:
        return
    before_species, before_badges, before_easter = _collection_snapshot(before_collection)
    after_species, after_badges, after_easter = _collection_snapshot(after_collection)

    for species_id in sorted(after_species - before_species):
        active = after_collection.buddies.get(species_id)
        if not active:
            continue
        buddy = after_collection.get_active_buddy() if after_collection.active_species == species_id else None
        if buddy is None:
            from able.core.buddy.model import BuddyState
            buddy = BuddyState(**{
                k: v for k, v in active.items()
                if k in BuddyState.__dataclass_fields__
            })
        if species_id == "aether":
            print(f"  ✨ collection bonus unlocked: {buddy.display_emoji} {buddy.name} joined your backpack")
        else:
            print(f"  🎒 caught {buddy.display_emoji} {buddy.name} the {buddy.meta['label']}!")

    for badge in after_collection.badges:
        badge_id = badge.get("id", "")
        if badge_id and badge_id not in before_badges:
            print(f"  🏅 badge unlocked: {badge['title']}")

    if after_easter and not before_easter:
        print("  ✨ collection milestone unlocked")


# ── Slash context + handler ──────────────────────────────────────────────

class SlashCtx:
    """Bundle of runtime + buddy dependencies for slash command handling."""

    __slots__ = (
        "gateway", "args",
        "load_buddy", "save_buddy", "load_buddy_collection",
        "switch_active_buddy", "update_collection_profile", "record_collection_progress",
        "STARTER_SPECIES", "create_starter_buddy",
        "render_full", "render_banner", "render_backpack", "render_header",
        "render_starter_selection", "render_battle_result",
        "render_evolution", "render_legendary_unlock",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


async def _handle_slash(message, ctx, buddy):
    """Handle slash commands. Returns (handled: bool, updated_buddy)."""
    # Unpack context — keeps call site clean while body stays readable
    gateway = ctx.gateway
    args = ctx.args
    load_buddy = ctx.load_buddy
    save_buddy = ctx.save_buddy
    load_buddy_collection = ctx.load_buddy_collection
    switch_active_buddy = ctx.switch_active_buddy
    update_collection_profile = ctx.update_collection_profile
    record_collection_progress = ctx.record_collection_progress
    STARTER_SPECIES = ctx.STARTER_SPECIES
    create_starter_buddy = ctx.create_starter_buddy
    render_full = ctx.render_full
    render_banner = ctx.render_banner
    render_backpack = ctx.render_backpack
    render_header = getattr(ctx, "render_header", lambda *_args, **_kwargs: "")
    render_starter_selection = ctx.render_starter_selection
    render_battle_result = ctx.render_battle_result
    render_evolution = ctx.render_evolution
    render_legendary_unlock = ctx.render_legendary_unlock

    if message in {"/exit", "/quit", "/q"}:
        print(_c(DIM, "  bye"))
        raise SystemExit(0)

    if message in {"/help", "/h", "/?"}:
        print(_c(DIM, "  ─────────────────────────────────────"))
        print(f"  {_c(BOLD, '/help')}       this menu")
        print(f"  {_c(BOLD, '/status')}     session stats")
        print(f"  {_c(BOLD, '/tools')}      available tools")
        print(f"  {_c(BOLD, '/resources')}  control plane inventory")
        print(f"  {_c(BOLD, '/eval')}       distillation progress")
        print(f"  {_c(BOLD, '/evolve')}     run evolution cycle")
        print(f"  {_c(BOLD, '/buddy')}      active buddy stats")
        print(f"  {_c(BOLD, '/buddy bag')}  backpack + dex progress")
        print(f"  {_c(BOLD, '/buddy switch <name>')}  switch active buddy")
        print(f"  {_c(BOLD, '/battle')}     eval-based battle")
        print(f"  {_c(BOLD, '/image <path>')}  send an image to vision model")
        print(f"  {_c(BOLD, '/audio <path>')}  transcribe audio file")
        print(f"  {_c(BOLD, '/clear')}      clear the screen, keep scrollback")
        print(f"  {_c(BOLD, '/compact')}    clear + print compact session recap")
        print(f"  {_c(BOLD, '/exit')}       quit")
        print(_c(DIM, "  ─────────────────────────────────────"))
        return True, buddy

    if message in {"/clear", "-clear"}:
        provider_count = len(getattr(gateway.provider_chain, "providers", []) or [])
        _clear_terminal()
        _print_chat_header(buddy, provider_count, render_header)
        return True, buddy

    if message in {"/compact", "-compact"}:
        _print_compact_view(gateway, args, buddy, render_header)
        return True, buddy

    # ── Multimodal commands ────────────────────────────────────────
    if message.startswith("/image "):
        img_path = message[7:].strip().strip('"').strip("'")
        img_file = Path(img_path).expanduser()
        if not img_file.exists():
            print(f"  {_c(RED, 'error')}: file not found — {img_path}")
            return True, buddy
        try:
            import base64 as _b64
            img_bytes = img_file.read_bytes()
            ext = img_file.suffix.lower().lstrip(".")
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
            b64 = _b64.b64encode(img_bytes).decode()
            caption = (await asyncio.to_thread(
                _prompt_input, f"  {_c(CYAN, 'caption')} (Enter to skip): "
            )).strip() or "Describe this image."
            multimodal_msg = [
                {"type": "text", "text": caption},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
            print(f"  {_c(DIM, f'sending {img_file.name} ({len(img_bytes)//1024}KB)...')}")
            response = await gateway.process_message(
                message=multimodal_msg, user_id=args.session,
                client_id=args.client, metadata={"source": "cli", "channel": "cli", "is_owner": True},
            )
            print(f"\n  {_c(CYAN, 'able')} {response}\n")
        except Exception as e:
            print(f"  {_c(RED, 'error')}: {e}")
        return True, buddy

    if message.startswith("/audio "):
        audio_path = message[7:].strip().strip('"').strip("'")
        audio_file = Path(audio_path).expanduser()
        if not audio_file.exists():
            print(f"  {_c(RED, 'error')}: file not found — {audio_path}")
            return True, buddy
        try:
            from able.tools.voice.transcription import VoiceTranscriber
            transcriber = VoiceTranscriber()
            print(f"  {_c(DIM, f'transcribing {audio_file.name}...')}")
            result = await transcriber.transcribe_file(audio_file)
            print(f"  {_c(GREEN, 'transcribed')}: {result.text}")
            print(f"  {_c(DIM, f'{result.duration_seconds:.1f}s · {result.provider.value} · {result.processing_time_ms:.0f}ms')}")
            # Optionally send to gateway
            followup = (await asyncio.to_thread(
                _prompt_input, f"  {_c(CYAN, 'send to ABLE?')} [y/N]: "
            )).strip().lower()
            if followup in ("y", "yes"):
                response = await gateway.process_message(
                    message=result.text, user_id=args.session,
                    client_id=args.client, metadata={"source": "cli", "channel": "cli", "is_owner": True},
                )
                print(f"\n  {_c(CYAN, 'able')} {response}\n")
            await transcriber.close()
        except Exception as e:
            print(f"  {_c(RED, 'error')}: {e}")
        return True, buddy

    if message == "/tools":
        for row in gateway.tool_registry.get_catalog():
            approval = _c(YELLOW, "approval") if row["requires_approval"] else _c(GREEN, "auto")
            print(f"  {row['name']:24s} {_c(DIM, row['category']):16s} {approval}")
        return True, buddy

    if message == "/status":
        session = gateway.session_mgr.get_or_create(args.session) if gateway.session_mgr else None
        provider_rows = []
        for provider in gateway.provider_chain.providers:
            model = getattr(provider, "model", "")
            provider_rows.append(f"{provider.name} ({model})" if model else provider.name)
        print(_c(DIM, "  ─────────────────────────────────────"))
        print(f"  session:    {args.session}")
        print(f"  messages:   {session.messages if session else 0}")
        print(f"  tokens:     {session.total_tokens if session else 0}")
        print(f"  cost:       ${session.cost_usd:.4f}" if session else "  cost:       $0.00")
        print(f"  providers:  {len(provider_rows)}")
        if provider_rows:
            print(f"  roster:     {', '.join(provider_rows)}")
        print(f"  tools:      {gateway.tool_registry.tool_count}")
        print(_c(DIM, "  ─────────────────────────────────────"))
        return True, buddy

    if message == "/resources":
        try:
            from able.core.control_plane.resources import ResourcePlane
            rp = ResourcePlane()
            resources = rp.list_resources()
            for r in resources:
                state = r.get("state", "unknown")
                kind = r.get("kind", r.get("type", "unknown"))
                print(f"  {r['id']}  {kind:18s}  {state}")
            if not resources:
                print(_c(DIM, "  (no resources registered)"))
        except Exception as e:
            print(f"  {_c(RED, 'error')}: {e}")
        return True, buddy

    if message == "/eval":
        try:
            from able.evals.collect_results import summarize_corpus_progress
            report = summarize_corpus_progress()
            print(json.dumps(report, indent=2))
        except Exception as e:
            print(f"  {_c(RED, 'error')}: {e}")
        return True, buddy

    if message == "/evolve":
        if hasattr(gateway, "evolution_daemon") and gateway.evolution_daemon:
            print(_c(DIM, "  running evolution cycle..."))
            try:
                result = await gateway.evolution_daemon.run_cycle()
                status = _c(GREEN, "OK") if result.success else _c(RED, "FAILED")
                print(f"  cycle {result.cycle_id}: {status}")
                print(f"  analyzed {result.interactions_analyzed} interactions, {result.problems_found} problems, {result.improvements_deployed} deployed")
                if result.error:
                    print(f"  {_c(RED, 'error')}: {result.error}")
                buddy = load_buddy()
                if buddy:
                    restored = buddy.water("evolve")
                    if restored > 0:
                        print(f"  {buddy.display_emoji} watered! +{restored:.0f}")
                    save_buddy(buddy)
            except Exception as e:
                print(f"  {_c(RED, 'error')}: {e}")
        else:
            print(_c(DIM, "  evolution daemon not running"))
        return True, buddy

    if message.startswith("/buddy"):
        parts = message.split()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        collection = load_buddy_collection()

        if subcommand in {"bag", "backpack", "party", "dex", "badges"}:
            print(render_backpack(collection))
            return True, load_buddy()

        if subcommand in {"switch", "choose", "equip"}:
            if len(parts) < 3:
                print(_c(DIM, "  usage: /buddy switch <name|species>"))
                return True, buddy
            switched = switch_active_buddy(" ".join(parts[2:]))
            if switched:
                print(f"  active buddy set to {switched.display_emoji} {switched.name} the {switched.meta['label']}")
                print(render_banner(switched))
                return True, switched
            print(_c(DIM, "  buddy not found in backpack"))
            return True, buddy

        if subcommand in {"setup", "starter"}:
            if collection and collection.buddies:
                if not _profile_is_complete(collection) and len(collection.buddies) <= 1:
                    print(_c(DIM, "  starter selection is required to finish initial setup."))
                    from able.core.buddy.model import reset_buddy_collection
                    reset_buddy_collection()
                    buddy = await _buddy_setup_flow(
                        STARTER_SPECIES,
                        create_starter_buddy,
                        save_buddy,
                        render_starter_selection,
                        update_collection_profile,
                        allow_skip=False,
                    )
                    return True, buddy
                print(render_backpack(collection))
                try:
                    await _buddy_onboarding_flow(update_collection_profile)
                except _SetupExit:
                    print(_c(DIM, "  skipped onboarding — you can finish later with /buddy setup"))
                print(_c(DIM, "  starter team kept. use /buddy switch <name> to rotate."))
                return True, load_buddy()
            buddy = await _buddy_setup_flow(
                STARTER_SPECIES,
                create_starter_buddy,
                save_buddy,
                render_starter_selection,
                update_collection_profile,
                allow_skip=False,
            )
            return True, buddy

        buddy = load_buddy()
        if collection and collection.buddies and not _profile_is_complete(collection):
            if len(collection.buddies) <= 1:
                print(_c(DIM, "  starter selection is required before the buddy system goes live."))
                from able.core.buddy.model import reset_buddy_collection
                reset_buddy_collection()
                buddy = await _buddy_setup_flow(
                    STARTER_SPECIES,
                    create_starter_buddy,
                    save_buddy,
                    render_starter_selection,
                    update_collection_profile,
                    allow_skip=False,
                )
                return True, buddy
            try:
                await _buddy_onboarding_flow(update_collection_profile)
            except _SetupExit:
                print(_c(DIM, "  skipped onboarding — you can finish later with /buddy setup"))
            collection = load_buddy_collection()
            buddy = load_buddy()
        if buddy:
            print(render_full(buddy))
            if collection and len(collection.buddies) > 1:
                print(render_backpack(collection))
        else:
            buddy = await _buddy_setup_flow(
                STARTER_SPECIES,
                create_starter_buddy,
                save_buddy,
                render_starter_selection,
                update_collection_profile,
                allow_skip=False,
            )
        return True, buddy

    if message.startswith("/battle"):
        from able.core.buddy.battle import run_battle, list_available_battles

        buddy = load_buddy()
        if not buddy:
            print(_c(DIM, "  no buddy yet — use /buddy first"))
            return True, buddy

        parts = message.split(None, 1)
        if len(parts) < 2:
            available = list_available_battles()
            if available:
                print(f"  available: {', '.join(available)}")
                print(_c(DIM, "  usage: /battle <domain>  (add --dry-run to simulate)"))
            else:
                print(_c(DIM, "  no eval configs found"))
            return True, buddy

        args_str = parts[1].strip()
        dry_run = "--dry-run" in args_str
        domain = args_str.replace("--dry-run", "").strip()

        print(f"  {buddy.display_emoji} {buddy.name} enters battle: {domain}...")
        record = run_battle(buddy, domain, dry_run=dry_run)
        if record is None:
            print(f"  no eval config for '{domain}'")
            return True, buddy

        collection_before = load_buddy_collection()
        was_legendary = buddy.legendary_title
        buddy.record_battle(record)
        restored = buddy.feed("battle")
        if restored > 0:
            print(f"  {buddy.display_emoji} fed! +{restored:.0f}")
        new_stage = buddy.check_evolution()
        if new_stage:
            previous_stage = buddy.stage_enum
            buddy.evolve(new_stage)
            legendary_title = buddy.unlock_legendary()
            print(render_evolution(buddy, previous_stage, new_stage))
            if legendary_title:
                print(render_legendary_unlock(buddy))
        elif not was_legendary and buddy.legendary_title:
            print(render_legendary_unlock(buddy))
        save_buddy(buddy)
        collection_update = record_collection_progress(
            domain,
            points=4 if record.result == "win" else 2 if record.result == "draw" else 1,
        )
        print(render_battle_result(buddy, record.domain, record.passed, record.total, record.result, record.xp_earned))
        print(render_banner(buddy))
        if collection_update["new_buddies"] or collection_update["new_badges"] or collection_update["easter_egg_unlocked"]:
            _print_collection_updates(collection_before, load_buddy_collection())
        return True, buddy

    return False, buddy


# ── Main chat loop ────────────────────────────────────────────────────────

async def run_chat(args: argparse.Namespace) -> int:
    from able.core.buddy.model import (
        load_buddy,
        load_buddy_collection,
        reset_buddy_collection,
        save_buddy,
        switch_active_buddy,
        update_collection_profile,
        record_collection_progress,
        Species,
        STARTER_SPECIES,
        create_starter_buddy,
    )
    from able.core.buddy.renderer import (
        render_backpack, render_banner, render_header, render_full, render_starter_selection,
        render_battle_result, render_evolution, render_legendary_unlock,
    )

    _enable_line_editing()

    collection = load_buddy_collection()
    buddy = load_buddy() if _profile_is_complete(collection) else None
    if sys.stdin.isatty() and not _profile_is_complete(collection):
        if collection and collection.buddies and len(collection.buddies) <= 1:
            print(_c(DIM, "  starter selection is required to finish initial setup."))
            reset_buddy_collection()
        buddy = await _buddy_setup_flow(
            STARTER_SPECIES,
            create_starter_buddy,
            save_buddy,
            render_starter_selection,
            update_collection_profile,
            allow_skip=False,
        )

    gateway = None

    # ── Suppress ALL noise unless --verbose ──────────────────────
    if not args.verbose:
        import warnings
        warnings.filterwarnings("ignore")
        logging.getLogger().setLevel(logging.ERROR)
        for name in ("able", "httpx", "httpcore", "openai", "anthropic",
                      "ollama", "phoenix", "opentelemetry", "grpc", "urllib3",
                      "uvicorn", "starlette", "fastapi"):
            logging.getLogger(name).setLevel(logging.ERROR)
        # Silence Phoenix print() spam by redirecting stderr during init
        _real_stderr = sys.stderr
        sys.stderr = open(os.devnull, "w")

    try:
        from able.core.gateway.gateway import ABLEGateway
        gateway = ABLEGateway(require_telegram=False, skip_phoenix=True)
    finally:
        if not args.verbose:
            sys.stderr.close()
            sys.stderr = _real_stderr  # noqa: F821

    gateway.approval_workflow = TerminalApprovalWorkflow(auto_approve=args.auto_approve)

    if args.control_port:
        try:
            await gateway.start_health_server(port=args.control_port, quiet=not args.verbose)
        except OSError:
            pass

    providers = [p.name for p in gateway.provider_chain.providers]
    if not providers:
        print(f"  {_c(RED, 'No providers configured.')} Set up API keys or Ollama first.")
        await gateway.aclose()
        return 1

    if buddy:
        mood = buddy.apply_needs_decay()
        save_buddy(buddy)
        if mood in ("hungry", "neglected"):
            needs = buddy.get_needs()
            print(f"  {buddy.display_emoji} {buddy.name}: \"{needs.mood_message}\"")

    # ── Header ────────────────────────────────────────────────────
    if buddy:
        print(f"\n{render_header(buddy, len(providers))}")
    else:
        print(f"\n    {_c(BOLD, 'ABLE')} | {len(providers)} providers")
    print(f"    {_c(DIM, '/help for commands')}\n")

    # ── Slash command context ────────────────────────────────────
    slash_ctx = SlashCtx(
        gateway=gateway, args=args,
        load_buddy=load_buddy, save_buddy=save_buddy,
        load_buddy_collection=load_buddy_collection,
        switch_active_buddy=switch_active_buddy,
        update_collection_profile=update_collection_profile,
        record_collection_progress=record_collection_progress,
        STARTER_SPECIES=STARTER_SPECIES,
        create_starter_buddy=create_starter_buddy,
        render_full=render_full, render_banner=render_banner,
        render_backpack=render_backpack, render_header=render_header,
        render_starter_selection=render_starter_selection,
        render_battle_result=render_battle_result,
        render_evolution=render_evolution,
        render_legendary_unlock=render_legendary_unlock,
    )

    # ── Session writer (feeds distillation pipeline) ────────────
    session_writer = _SessionWriter(args.session)

    # ── Chat loop ─────────────────────────────────────────────────
    spinner = _Spinner()
    try:
        while True:
            try:
                raw = await asyncio.to_thread(_prompt_input, f"{_c(GREEN, '>')} ")
            except (EOFError, KeyboardInterrupt):
                print(f"\n{_c(DIM, '  bye')}")
                return 0

            message = raw.strip()
            if not message:
                continue

            # Slash commands
            command_word = message.split(None, 1)[0].lower()
            if message.startswith("/") or command_word in {"-clear", "-compact"}:
                handled, buddy = await _handle_slash(message, slash_ctx, buddy)
                if handled:
                    continue

            # Log inbound
            collection_before = load_buddy_collection()
            gateway.transcript_manager.log_message(
                args.client,
                {"user_id": args.session, "message": message,
                 "direction": "inbound", "channel": "cli"},
            )
            session_writer.write("user", message)

            # ── Get response ──────────────────────────────────────────
            t0 = time.monotonic()

            if not args.no_stream:
                response_parts: list[str] = []
                # Show thinking indicator then stream
                spinner.start()
                first_chunk = True
                started_output = False
                started_thinking = False
                started_answer = False
                reasoning_preview = _ReasoningPreview()
                try:
                    async for chunk in gateway.stream_message(
                        message=message,
                        user_id=args.session,
                        client_id=args.client,
                        metadata={"source": "cli", "channel": "cli", "is_owner": True},
                    ):
                        thought_chunk, visible_chunk = reasoning_preview.consume(chunk)
                        if first_chunk and (thought_chunk or visible_chunk):
                            spinner.stop()
                            await asyncio.sleep(0.05)  # Let spinner cleanup flush
                            sys.stdout.write("\n")
                            first_chunk = False
                            started_output = True
                        if thought_chunk:
                            if not started_thinking:
                                sys.stdout.write(f"  {_c(DIM, 'thinking')} ")
                                started_thinking = True
                            sys.stdout.write(_c(DIM, thought_chunk))
                            sys.stdout.flush()
                        if visible_chunk:
                            if started_thinking and not started_answer:
                                sys.stdout.write("\n")
                            if not started_answer:
                                if not started_output:
                                    spinner.stop()
                                    await asyncio.sleep(0.05)
                                    sys.stdout.write("\n")
                                    started_output = True
                                    first_chunk = False
                                sys.stdout.write(f"  {_c(CYAN, 'able')} ")
                                started_answer = True
                            sys.stdout.write(visible_chunk)
                            sys.stdout.flush()
                            response_parts.append(visible_chunk)
                except Exception:
                    spinner.stop()

                if response_parts:
                    response = "".join(response_parts)
                    elapsed = time.monotonic() - t0
                    print(f"\n{_c(DIM, f'  [{elapsed:.1f}s]')}")
                else:
                    # Streaming yielded nothing — fall back
                    if first_chunk:
                        spinner.stop()
                        await asyncio.sleep(0.05)
                    response = await gateway.process_message(
                        message=message,
                        user_id=args.session,
                        client_id=args.client,
                        metadata={"source": "cli", "channel": "cli", "is_owner": True},
                    )
                    elapsed = time.monotonic() - t0
                    print(f"\n  {_c(CYAN, 'able')} {response}")
                    print(_c(DIM, f"  [{elapsed:.1f}s]"))
            else:
                spinner.start()
                response = await gateway.process_message(
                    message=message,
                    user_id=args.session,
                    client_id=args.client,
                    metadata={"source": "cli", "channel": "cli", "is_owner": True},
                )
                spinner.stop()
                await asyncio.sleep(0.05)
                elapsed = time.monotonic() - t0
                print(f"\n  {_c(CYAN, 'able')} {response}")
                print(_c(DIM, f"  [{elapsed:.1f}s]"))

            # Log outbound
            gateway.transcript_manager.log_message(
                args.client,
                {"user_id": "able", "message": response,
                 "direction": "outbound", "channel": "cli"},
            )
            session_writer.write("assistant", response, model=getattr(
                gateway, '_last_provider', 'unknown'))

            # ── Buddy level-up check ─────────────────────────────────
            try:
                new_buddy = load_buddy()
                old_legendary = buddy.legendary_title if buddy else ""
                showed_legendary = False
                if new_buddy and buddy and new_buddy.level > buddy.level:
                    print(f"  {new_buddy.display_emoji} {new_buddy.name} leveled up to {new_buddy.level}!")
                    new_stage = new_buddy.check_evolution()
                    if new_stage:
                        previous_stage = new_buddy.stage_enum
                        new_buddy.evolve(new_stage)
                        legendary_title = new_buddy.unlock_legendary()
                        save_buddy(new_buddy)
                        print(render_evolution(new_buddy, previous_stage, new_stage))
                        if legendary_title:
                            print(render_legendary_unlock(new_buddy))
                            showed_legendary = True
                if new_buddy and not showed_legendary and not old_legendary and new_buddy.legendary_title:
                    print(render_legendary_unlock(new_buddy))
                buddy = new_buddy or buddy
                _print_collection_updates(collection_before, load_buddy_collection())
            except Exception:
                pass
    finally:
        spinner.stop()
        if gateway is not None:
            await gateway.aclose()


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(run_chat(args))


if __name__ == "__main__":
    raise SystemExit(main())
