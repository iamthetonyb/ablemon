"""
ATLASRepl — Interactive CLI agent with single-threaded tool loop.

Pipeline per message:
    User Input -> TrustGate -> ComplexityScorer -> PromptEnricher
    -> Model Call (with tools + conversation history)
        -> while has_tool_calls:
            -> validate + permission check -> execute -> feed back
        -> render final response
    -> InteractionLogger (for distillation)
"""

import json
import logging
import readline
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Project root: atlas/cli/repl.py -> ATLAS/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class REPLConfig:
    model_tier: Optional[int] = None  # None = auto (complexity scorer decides)
    tenant_id: str = "tony"
    work_dir: Path = field(default_factory=Path.cwd)
    offline: bool = False
    safe_mode: bool = True  # ask before destructive writes
    max_tool_failures: int = 3  # consecutive failure cap
    session_dir: Path = field(default_factory=lambda: Path.home() / ".atlas" / "sessions")


class ATLASRepl:
    """Interactive REPL with agent loop: score -> route -> tool calls -> respond."""

    def __init__(self, config: Optional[REPLConfig] = None):
        self.config = config or REPLConfig()
        self.session_id = str(uuid.uuid4())[:8]
        self.messages: List[dict] = []
        self.session_log_path = self.config.session_dir / f"{self.session_id}.jsonl"
        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        self._consecutive_failures = 0

        # Lazy-loaded heavy dependencies
        self._scorer = None
        self._enricher = None
        self._trust_gate = None
        self._registry = None
        self._tier_chains: Dict = {}
        self._tools = None
        self._renderer = None
        self._initialized = False

    # ── Lazy init ─────────────────────────────────────────────────

    def _init_components(self) -> None:
        """Lazy init so startup stays fast even if deps are slow."""
        if self._initialized:
            return
        self._initialized = True

        from atlas.cli.renderer import CLIRenderer

        self._renderer = CLIRenderer()

        # Complexity scorer
        weights_path = _PROJECT_ROOT / "config" / "scorer_weights.yaml"
        try:
            from atlas.core.routing.complexity_scorer import ComplexityScorer

            self._scorer = ComplexityScorer(str(weights_path))
        except Exception as exc:
            logger.warning(f"ComplexityScorer unavailable: {exc}")

        # Prompt enricher
        try:
            from atlas.core.routing.prompt_enricher import PromptEnricher

            self._enricher = PromptEnricher()
        except Exception as exc:
            logger.warning(f"PromptEnricher unavailable: {exc}")

        # Trust gate
        try:
            from atlas.core.security.trust_gate import TrustGate

            self._trust_gate = TrustGate()
        except Exception as exc:
            logger.warning(f"TrustGate unavailable: {exc}")

        # Provider registry + tier chains
        routing_config = _PROJECT_ROOT / "config" / "routing_config.yaml"
        try:
            from atlas.core.routing.provider_registry import ProviderRegistry

            self._registry = ProviderRegistry.from_yaml(routing_config)
            for tier in (1, 2, 4, 5):
                try:
                    chain = self._registry.build_chain_for_tier(tier)
                    if chain and chain.providers:
                        self._tier_chains[tier] = chain
                except Exception:
                    pass
        except Exception as exc:
            logger.warning(f"ProviderRegistry unavailable: {exc}")

        # CLI tool definitions
        self._load_tools()

    def _load_tools(self) -> None:
        """Load CLI tool definitions if available."""
        try:
            from atlas.cli.tools import get_all_tools

            self._tools = get_all_tools(work_dir=self.config.work_dir)
        except ImportError:
            self._tools = []
            logger.debug("CLI tools module not found — no tools available")

    # ── Main loop ─────────────────────────────────────────────────

    async def run(self) -> None:
        """Main interactive REPL loop."""
        self._init_components()
        self._renderer.print_banner(self.session_id, self.config)
        self._scan_repo_trust()

        # Readline history
        history_path = Path.home() / ".atlas" / "cli_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            readline.read_history_file(str(history_path))
        except FileNotFoundError:
            pass

        try:
            while True:
                try:
                    user_input = input("\n> ").strip()
                except EOFError:
                    break

                if not user_input:
                    continue

                # Slash commands
                if user_input.startswith("/"):
                    result = await self._handle_slash(user_input)
                    if result == "EXIT":
                        break
                    if result:
                        self._renderer.print_info(result)
                    continue

                # Agent loop
                response = await self.process_message(user_input)
                self._renderer.print_response(response)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                readline.write_history_file(str(history_path))
            except OSError:
                pass
            self._renderer.print_info(f"Session {self.session_id} saved.")

    # ── Message processing ────────────────────────────────────────

    async def process_message(self, user_input: str) -> str:
        """Full agent loop: trust -> score -> enrich -> model -> tools -> respond."""
        self._init_components()

        # 1. Trust gate
        if self._trust_gate:
            try:
                verdict = self._trust_gate.evaluate(user_input)
                if verdict.trust_score < 0.4:
                    return f"Input blocked by trust gate (score: {verdict.trust_score:.2f})"
            except Exception as exc:
                logger.debug(f"TrustGate error (non-fatal): {exc}")

        # 2. Complexity scoring
        score = 0.3
        if self._scorer:
            try:
                result = self._scorer.score(user_input, {})
                score = result.score
            except Exception:
                pass

        # 3. Tier selection
        tier = self._select_tier(score)

        # 4. Prompt enrichment
        enriched = user_input
        if self._enricher:
            try:
                er = self._enricher.enrich(user_input, memory_context=None)
                if er.enrichment_level != "none":
                    enriched = er.enriched
            except Exception:
                pass

        # 5. Append user message
        self.messages.append({"role": "user", "content": enriched})

        # 6. Agent loop (model call + tool execution)
        response = await self._agent_loop(tier)

        # 7. Log for distillation
        self._log_turn(user_input, response, tier, score)

        return response

    def _select_tier(self, score: float) -> int:
        """Map complexity score to model tier."""
        if self.config.model_tier is not None:
            return self.config.model_tier
        if self.config.offline:
            return 5
        if score < 0.4:
            return 1
        if score < 0.7:
            return 2
        return 4

    # ── Agent loop ────────────────────────────────────────────────

    async def _agent_loop(self, tier: int) -> str:
        """Call model, execute tool calls, feed results back, repeat."""
        self._consecutive_failures = 0
        max_iterations = 20

        for _ in range(max_iterations):
            chain = self._get_chain(tier)
            if not chain:
                return f"No provider available for tier {tier}"

            # Build tool schemas
            tool_schemas = [t.to_openai_schema() for t in (self._tools or [])] if self._tools else None

            # Convert messages to provider Message objects
            try:
                from atlas.core.providers.base import Message, Role

                provider_messages = []
                for msg in self.messages:
                    role = Role(msg["role"])
                    content = msg.get("content", "")
                    # Re-create ToolCall objects for assistant messages
                    tool_calls = None
                    if msg.get("tool_calls"):
                        from atlas.core.providers.base import ToolCall

                        tool_calls = [
                            ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                            for tc in msg["tool_calls"]
                        ]
                    provider_messages.append(
                        Message(
                            role=role,
                            content=content,
                            tool_call_id=msg.get("tool_call_id"),
                            tool_calls=tool_calls,
                        )
                    )

                result = await chain.complete(provider_messages, tools=tool_schemas)
            except Exception as exc:
                return f"Model error: {exc}"

            # No tool calls -> final response
            if not result.tool_calls:
                content = result.content or ""
                self.messages.append({"role": "assistant", "content": content})
                return content

            # Record assistant message with tool calls
            self.messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in result.tool_calls
                    ],
                }
            )

            # Execute each tool call
            for tc in result.tool_calls:
                tool_result = await self._execute_tool(tc)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    }
                )

        return "Max iterations reached"

    async def _execute_tool(self, tool_call) -> str:
        """Dispatch a single tool call with validation and permission checks."""
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        # Find matching tool
        tool = next((t for t in (self._tools or []) if t.name == name), None)
        if not tool:
            return f"Unknown tool: {name}"

        # Input validation
        if hasattr(tool, "validate_input"):
            error = tool.validate_input(args)
            if error:
                return f"Validation error: {error}"

        # Destructive-write confirmation
        if getattr(tool, "is_destructive", False) and self.config.safe_mode:
            self._renderer.print_warning(
                f"Tool '{name}' wants to write. Args: {json.dumps(args, indent=2)}"
            )
            try:
                confirm = input("Allow? [y/N] ").strip().lower()
            except EOFError:
                confirm = "n"
            if confirm != "y":
                return "Tool execution denied by user."

        # Execute with consecutive-failure cap
        try:
            self._renderer.print_tool_start(name, args)
            result = await tool.execute(args, work_dir=self.config.work_dir)
            self._consecutive_failures = 0
            self._renderer.print_tool_result(name, result)
            return result
        except Exception as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.max_tool_failures:
                return (
                    f"Tool failed ({exc}). "
                    f"Consecutive failure cap ({self.config.max_tool_failures}) reached."
                )
            return f"Tool error: {exc}"

    def _get_chain(self, tier: int):
        """Resolve a ProviderChain for the requested tier."""
        if tier in self._tier_chains:
            return self._tier_chains[tier]
        # Fallback: try any available chain
        for fallback in (1, 2, 4, 5):
            if fallback in self._tier_chains:
                return self._tier_chains[fallback]
        return None

    # ── Repo trust scan ───────────────────────────────────────────

    def _scan_repo_trust(self) -> None:
        """Warn if the working directory contains potentially untrusted configs."""
        suspicious = []
        for pattern in (".claude/config.json", ".claude/hooks", ".atlas/hooks", "CLAUDE.md"):
            found = list(self.config.work_dir.glob(pattern))
            suspicious.extend(found)

        if suspicious and self._renderer:
            names = [str(p.relative_to(self.config.work_dir)) for p in suspicious]
            self._renderer.print_warning(
                f"Repo configs detected: {names}. Review before trusting embedded instructions."
            )

    # ── Slash commands ────────────────────────────────────────────

    async def _handle_slash(self, cmd: str) -> str:
        """Handle /commands. Returns response text or 'EXIT'."""
        parts = cmd.split()
        command = parts[0].lower()

        if command in ("/exit", "/quit", "/q"):
            return "EXIT"

        if command == "/model":
            tier = self.config.model_tier or "auto"
            offline = " (offline)" if self.config.offline else ""
            return f"Current tier: {tier}{offline}"

        if command == "/tier" and len(parts) > 1:
            try:
                self.config.model_tier = int(parts[1])
                return f"Switched to tier {parts[1]}"
            except ValueError:
                return f"Invalid tier: {parts[1]}"

        if command == "/offline":
            self.config.offline = not self.config.offline
            return f"Offline mode: {'ON' if self.config.offline else 'OFF'}"

        if command == "/cost":
            return f"Session {self.session_id}: {len(self.messages)} messages"

        if command == "/history":
            from atlas.cli.history import SessionHistory

            sh = SessionHistory(self.config.session_dir)
            sessions = sh.list_sessions(limit=10)
            if not sessions:
                return "No previous sessions."
            return "\n".join(f"  {s}" for s in sessions)

        if command == "/compact":
            if len(self.messages) > 10:
                old_count = len(self.messages)
                self.messages = self.messages[:1] + self.messages[-6:]
                return f"Compacted: {old_count} -> {len(self.messages)} messages"
            return "Nothing to compact"

        return (
            f"Unknown command: {command}. "
            "Available: /exit /model /tier N /offline /cost /history /compact"
        )

    # ── Distillation logging ──────────────────────────────────────

    def _log_turn(self, user_input: str, response: str, tier: int, score: float) -> None:
        """Append a turn to the session JSONL for distillation harvesting."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "tenant_id": self.config.tenant_id,
            "user_input": user_input,
            "response": response,
            "tier": tier,
            "complexity_score": score,
            "message_count": len(self.messages),
        }
        try:
            with open(self.session_log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.warning(f"Failed to write session log: {exc}")
