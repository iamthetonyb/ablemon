"""Local operator chat CLI for ABLE."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from able.core.approval.workflow import ApprovalResult, ApprovalStatus, ApprovalWorkflow

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


def _c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if _COLOR else text


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
                            input,
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
]

_DISTILLATION_OPTIONS = [
    ("9b-fast-local", "Fast local/T4-first distillation and lighter demos."),
    ("27b-deep-h100", "Heavier H100 runs for deep-quality promotions."),
    ("hybrid", "Iterate on 9B and promote the best work to 27B."),
]


async def _choose_profile_option(title: str, options: list[tuple[str, str]]) -> str:
    print(f"\n  {_c(BOLD, title)}")
    for idx, (value, description) in enumerate(options, 1):
        print(f"    [{idx}] {value.replace('-', ' ')} — {description}")
    while True:
        choice = (await asyncio.to_thread(input, "  pick a number (Enter for recommended): ")).strip()
        if not choice:
            return options[0][0]
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
    print("  This sets the companion up for your main domain focus and preferred distillation lane.")
    focus = await _choose_profile_option("Primary focus", _FOCUS_OPTIONS)
    work_style = await _choose_profile_option("Work style", _WORK_STYLE_OPTIONS)
    distillation_track = await _choose_profile_option("Distillation track", _DISTILLATION_OPTIONS)
    profile = {
        "focus": focus,
        "work_style": work_style,
        "distillation_track": distillation_track,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    collection = update_collection_profile(profile)
    print(f"  {_c(GREEN, 'saved')} profile: {focus} · {work_style} · {distillation_track}")
    print(f"  {_c(DIM, _distillation_hint(distillation_track))}")
    return collection


async def _buddy_setup_flow(
    Species,
    create_starter_buddy,
    save_buddy,
    render_starter_selection,
    update_collection_profile,
    *,
    allow_skip: bool,
):
    """Quick inline buddy creation. Returns buddy or None."""
    species_list = list(Species)
    print(render_starter_selection())
    while True:
        try:
            choice = await asyncio.to_thread(
                input,
                f"  {_c(GREEN, 'pick')} [1-{len(species_list)}]"
                + (" or Enter to skip: " if allow_skip else ": "),
            )
            choice = choice.strip().lower()
            if allow_skip and (not choice or choice in {"s", "skip"}):
                return None
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(species_list):
                    chosen = species_list[idx]
                    default_name = chosen.value.capitalize()
                    name = (await asyncio.to_thread(input, f"  {_c(CYAN, 'name')} [{default_name}]: ")).strip() or default_name
                    phrase = (await asyncio.to_thread(input, "  catch phrase (Enter to use the default): ")).strip()
                    buddy = create_starter_buddy(
                        name=name,
                        species=chosen,
                        catch_phrase=phrase,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    save_buddy(buddy)
                    await _buddy_onboarding_flow(update_collection_profile)
                    shiny_tag = " shiny!" if buddy.is_shiny else ""
                    print(f"  {buddy.display_emoji} {name} the {buddy.meta['label']} joins you!{shiny_tag}")
                    return buddy
            if allow_skip:
                print(f"  {_c(YELLOW, 'choose 1-5 or press Enter to skip')}")
            else:
                print(f"  {_c(YELLOW, 'starter selection is required — choose 1-5')}")
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


# ── Slash command handler ─────────────────────────────────────────────────

async def _handle_slash(message, gateway, args, buddy, load_buddy, save_buddy,
                        load_buddy_collection, switch_active_buddy, update_collection_profile, record_collection_progress,
                        Species, create_starter_buddy, render_full, render_banner, render_backpack,
                        render_starter_selection, render_battle_result, render_evolution, render_legendary_unlock):
    """Handle slash commands. Returns (handled: bool, updated_buddy)."""
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
        print(f"  {_c(BOLD, '/exit')}       quit")
        print(_c(DIM, "  ─────────────────────────────────────"))
        return True, buddy

    if message == "/tools":
        for row in gateway.tool_registry.get_catalog():
            approval = _c(YELLOW, "approval") if row["requires_approval"] else _c(GREEN, "auto")
            print(f"  {row['name']:24s} {_c(DIM, row['category']):16s} {approval}")
        return True, buddy

    if message == "/status":
        session = gateway.session_mgr.get_or_create(args.session) if gateway.session_mgr else None
        providers = [p.name for p in gateway.provider_chain.providers]
        print(_c(DIM, "  ─────────────────────────────────────"))
        print(f"  session:    {args.session}")
        print(f"  messages:   {session.messages if session else 0}")
        print(f"  tokens:     {session.total_tokens if session else 0}")
        print(f"  cost:       ${session.cost_usd:.4f}" if session else "  cost:       $0.00")
        print(f"  providers:  {len(providers)}")
        print(f"  tools:      {gateway.tool_registry.tool_count}")
        print(_c(DIM, "  ─────────────────────────────────────"))
        return True, buddy

    if message == "/resources":
        try:
            from able.core.control_plane.resources import ResourcePlane
            rp = ResourcePlane()
            inv = rp.get_inventory()
            for r in inv.get("resources", []):
                state = r.get("state", "unknown")
                print(f"  {r['id']}  {r['type']:10s}  {state}")
            if not inv.get("resources"):
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
                        Species,
                        create_starter_buddy,
                        save_buddy,
                        render_starter_selection,
                        update_collection_profile,
                        allow_skip=False,
                    )
                    return True, buddy
                print(render_backpack(collection))
                await _buddy_onboarding_flow(update_collection_profile)
                print(_c(DIM, "  starter team kept. use /buddy switch <name> to rotate."))
                return True, load_buddy()
            buddy = await _buddy_setup_flow(
                Species,
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
                    Species,
                    create_starter_buddy,
                    save_buddy,
                    render_starter_selection,
                    update_collection_profile,
                    allow_skip=False,
                )
                return True, buddy
            await _buddy_onboarding_flow(update_collection_profile)
            collection = load_buddy_collection()
            buddy = load_buddy()
        if buddy:
            print(render_full(buddy))
            if collection and len(collection.buddies) > 1:
                print(render_backpack(collection))
        else:
            buddy = await _buddy_setup_flow(
                Species,
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
        create_starter_buddy,
    )
    from able.core.buddy.renderer import (
        render_backpack, render_banner, render_header, render_full, render_starter_selection,
        render_battle_result, render_evolution, render_legendary_unlock,
    )

    collection = load_buddy_collection()
    buddy = load_buddy() if _profile_is_complete(collection) else None
    if sys.stdin.isatty() and not _profile_is_complete(collection):
        if collection and collection.buddies and len(collection.buddies) <= 1:
            print(_c(DIM, "  starter selection is required to finish initial setup."))
            reset_buddy_collection()
        buddy = await _buddy_setup_flow(
            Species,
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

    # ── Chat loop ─────────────────────────────────────────────────
    spinner = _Spinner()
    try:
        while True:
            try:
                raw = await asyncio.to_thread(input, f"{_c(GREEN, '>')} ")
            except (EOFError, KeyboardInterrupt):
                print(f"\n{_c(DIM, '  bye')}")
                return 0

            message = raw.strip()
            if not message:
                continue

            # Slash commands
            if message.startswith("/"):
                handled, buddy = await _handle_slash(
                    message, gateway, args, buddy, load_buddy, save_buddy,
                    load_buddy_collection, switch_active_buddy, update_collection_profile, record_collection_progress,
                    Species, create_starter_buddy, render_full, render_banner, render_backpack,
                    render_starter_selection, render_battle_result, render_evolution, render_legendary_unlock,
                )
                if handled:
                    continue

            # Log inbound
            collection_before = load_buddy_collection()
            gateway.transcript_manager.log_message(
                args.client,
                {"user_id": args.session, "message": message,
                 "direction": "inbound", "channel": "cli"},
            )

            # ── Get response ──────────────────────────────────────────
            t0 = time.monotonic()

            if not args.no_stream:
                response_parts: list[str] = []
                # Show thinking indicator then stream
                spinner.start()
                first_chunk = True
                try:
                    async for chunk in gateway.stream_message(
                        message=message,
                        user_id=args.session,
                        client_id=args.client,
                        metadata={"source": "cli", "channel": "cli", "is_owner": True},
                    ):
                        if first_chunk:
                            spinner.stop()
                            await asyncio.sleep(0.05)  # Let spinner cleanup flush
                            sys.stdout.write(f"\n  {_c(CYAN, 'able')} ")
                            first_chunk = False
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                        response_parts.append(chunk)
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
