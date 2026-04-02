"""Local operator chat CLI for ABLE."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

from able.core.approval.workflow import ApprovalResult, ApprovalStatus, ApprovalWorkflow
from able.core.gateway.gateway import ABLEGateway


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
            # Show affected resources from details
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
        default=int(os.environ.get("ABLE_CLI_CONTROL_PORT", "8080")),
        help="Start the local health/control API on this port. Use 0 to disable.",
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
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="able chat",
        description="Run a local terminal chat session against the ABLE gateway pipeline.",
    )
    return configure_parser(parser)


async def run_chat(args: argparse.Namespace) -> int:
    gateway = ABLEGateway(require_telegram=False)
    gateway.approval_workflow = TerminalApprovalWorkflow(auto_approve=args.auto_approve)

    if args.control_port:
        try:
            await gateway.start_health_server(port=args.control_port)
        except OSError as exc:
            print(
                f"[warn] control plane did not start on :{args.control_port}: {exc}. "
                "Chat will continue without binding that port."
            )

    providers = [provider.name for provider in gateway.provider_chain.providers]
    if not providers:
        print(
            "No providers are configured. Set up OpenAI OAuth/API keys or a local Ollama lane "
            "before using `able chat`."
        )
        return 1

    # ── Buddy system ──────────────────────────────────────────────
    from able.core.buddy.model import load_buddy, save_buddy, Species, create_starter_buddy
    from able.core.buddy.renderer import (
        render_banner, render_full, render_starter_selection,
        render_battle_result, render_evolution, render_legendary_unlock,
    )

    buddy = load_buddy()
    if buddy is None:
        print(render_starter_selection())
        species_list = list(Species)
        while True:
            try:
                choice = await asyncio.to_thread(input, "Pick a starter (1-5): ")
                idx = int(choice.strip()) - 1
                if 0 <= idx < len(species_list):
                    chosen = species_list[idx]
                    break
                print(f"Enter 1-{len(species_list)}")
            except (ValueError, EOFError):
                print(f"Enter 1-{len(species_list)}")
        name = ""
        while not name:
            name = (await asyncio.to_thread(input, "Name your buddy: ")).strip()
        phrase = (await asyncio.to_thread(input, "Catch phrase (or enter to skip): ")).strip()
        buddy = create_starter_buddy(
            name=name,
            species=chosen,
            catch_phrase=phrase,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        save_buddy(buddy)
        print(f"\n  {buddy.display_emoji} {name} the {buddy.meta['label']} joins you!")
        print(f"  \"{buddy.catch_phrase}\"\n")
        if buddy.is_shiny:
            print("  ✨ rare hatch: shiny variant unlocked\n")

    # Apply needs decay since last session
    mood = buddy.apply_needs_decay()
    save_buddy(buddy)
    if mood in ("hungry", "neglected"):
        needs = buddy.get_needs()
        print(f"\n  {buddy.display_emoji} {buddy.name}: \"{needs.mood_message}\"")

    print("ABLE local chat")
    print(render_banner(buddy))
    print(f"session:  {args.session}")
    print(f"client:   {args.client}")
    print(f"providers:{' ' if providers else ''}{', '.join(providers)}")
    if args.control_port:
        print(f"control:  http://127.0.0.1:{args.control_port}/health")
    print("commands: /help /status /tools /resources /eval /evolve /buddy /battle /exit")

    while True:
        try:
            raw = await asyncio.to_thread(input, "\nyou> ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0

        message = raw.strip()
        if not message:
            continue
        if message in {"/exit", "/quit"}:
            print("bye")
            return 0
        if message == "/help":
            print("commands: /help /status /tools /resources /eval /evolve /buddy /battle /exit")
            print("all other input goes through the full gateway pipeline.")
            continue
        if message == "/tools":
            for row in gateway.tool_registry.get_catalog():
                print(
                    f"- {row['name']} [{row['category']}] "
                    f"approval={'yes' if row['requires_approval'] else 'no'} "
                    f"surface={row['surface']}"
                )
            continue
        if message == "/status":
            session = gateway.session_mgr.get_or_create(args.session) if gateway.session_mgr else None
            print(json.dumps(
                {
                    "session": args.session,
                    "client": args.client,
                    "messages": session.messages if session else 0,
                    "avg_complexity": round(session.avg_complexity, 3) if session else 0.0,
                    "total_tokens": session.total_tokens if session else 0,
                    "cost_usd": round(session.cost_usd, 6) if session else 0.0,
                    "providers": providers,
                    "tool_count": gateway.tool_registry.tool_count,
                },
                indent=2,
            ))
            continue
        if message == "/resources":
            try:
                from able.core.control_plane.resources import ResourcePlane
                rp = ResourcePlane()
                inv = rp.get_inventory()
                for r in inv.get("resources", []):
                    state = r.get("state", "unknown")
                    print(f"  {r['id']}  {r['type']:10s}  {state}")
                if not inv.get("resources"):
                    print("  (no resources registered)")
            except Exception as e:
                print(f"  error: {e}")
            continue
        if message == "/eval":
            try:
                from able.evals.collect_results import summarize_corpus_progress
                report = summarize_corpus_progress()
                print(json.dumps(report, indent=2))
            except Exception as e:
                print(f"  error: {e}")
            continue
        if message == "/evolve":
            if hasattr(gateway, "evolution_daemon") and gateway.evolution_daemon:
                print("  running single evolution cycle...")
                try:
                    result = await gateway.evolution_daemon.run_cycle()
                    print(f"  cycle {result.cycle_id}: {'OK' if result.success else 'FAILED'}")
                    print(f"  interactions: {result.interactions_analyzed}")
                    print(f"  problems: {result.problems_found}")
                    print(f"  deployed: {result.improvements_deployed}")
                    if result.error:
                        print(f"  error: {result.error}")
                    # Evolution cycle waters the buddy
                    buddy = load_buddy()
                    if buddy:
                        restored = buddy.water("evolve")
                        if restored > 0:
                            print(f"  {buddy.display_emoji} watered! Thirst +{restored:.0f}")
                        save_buddy(buddy)
                except Exception as e:
                    print(f"  cycle error: {e}")
            else:
                print("  evolution daemon not running")
            continue
        if message == "/buddy":
            buddy = load_buddy()
            if buddy:
                print(render_full(buddy))
            else:
                print("  no buddy yet — restart `able chat` to pick a starter")
            continue
        if message.startswith("/battle"):
            from able.core.buddy.battle import run_battle, list_available_battles

            buddy = load_buddy()
            if not buddy:
                print("  no buddy yet — restart `able chat` to pick a starter")
                continue

            parts = message.split(None, 1)
            if len(parts) < 2:
                available = list_available_battles()
                if available:
                    print(f"  available battles: {', '.join(available)}")
                    print("  usage: /battle <domain>  (or /battle <domain> --dry-run)")
                else:
                    print("  no eval configs found in able/evals/")
                continue

            args_str = parts[1].strip()
            dry_run = "--dry-run" in args_str
            domain = args_str.replace("--dry-run", "").strip()

            print(f"  {buddy.display_emoji} {buddy.name} enters battle: {domain}...")
            record = run_battle(buddy, domain, dry_run=dry_run)
            if record is None:
                print(f"  no eval config for domain '{domain}'")
                continue

            was_legendary = buddy.legendary_title
            buddy.record_battle(record)
            # Battle feeds the buddy (evals = food)
            restored = buddy.feed("battle")
            if restored > 0:
                print(f"  {buddy.display_emoji} fed! Hunger +{restored:.0f}")
            # Check for evolution after battle
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

            print(render_battle_result(buddy, record.domain, record.passed, record.total, record.result, record.xp_earned))
            print(render_banner(buddy))
            continue

        gateway.transcript_manager.log_message(
            args.client,
            {
                "user_id": args.session,
                "message": message,
                "direction": "inbound",
                "channel": "cli",
            },
        )

        # Stream response token-by-token when possible
        if not args.no_stream:
            import sys
            response_parts = []
            print("\nable> ", end="", flush=True)
            try:
                async for chunk in gateway.stream_message(
                    message=message,
                    user_id=args.session,
                    client_id=args.client,
                    metadata={"source": "cli", "channel": "cli", "is_owner": True},
                ):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    response_parts.append(chunk)
                response = "".join(response_parts)
                print()  # Newline after streaming
            except Exception:
                # Fallback to non-streaming if stream_message fails
                print("", end="\r")
                response = await gateway.process_message(
                    message=message,
                    user_id=args.session,
                    client_id=args.client,
                    metadata={"source": "cli", "channel": "cli", "is_owner": True},
                )
                print(f"able> {response}")
        else:
            response = await gateway.process_message(
                message=message,
                user_id=args.session,
                client_id=args.client,
                metadata={"source": "cli", "channel": "cli", "is_owner": True},
            )
            print(f"\nable> {response}")

        gateway.transcript_manager.log_message(
            args.client,
            {
                "user_id": "able",
                "message": response,
                "direction": "outbound",
                "channel": "cli",
            },
        )

        # ── Check for buddy level-up (XP awarded in gateway) ──
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
        except Exception:
            pass  # Buddy system is optional — never block chat


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(run_chat(args))


if __name__ == "__main__":
    raise SystemExit(main())
