"""
Gateway Server — The coordinator that ties everything together.
Handles: Telegram channels, session routing, agent orchestration, AI responses.

Decomposed architecture:
  - Tool definitions → core/gateway/tool_defs/
  - Tool registry    → core/gateway/tool_registry.py
  - Initiative       → core/gateway/initiative.py
"""

import asyncio
import json
import logging
import os
import base64
from datetime import datetime
from typing import Dict, List, Optional, Union
from pathlib import Path

from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from core.security.trust_gate import TrustGate, TrustTier
from core.agents.base import ScannerAgent, AuditorAgent, ExecutorAgent, AgentContext, AgentAction, AgentRole
from core.queue.lane_queue import LaneQueue
from clients.client_manager import ClientRegistry, ClientTranscriptManager
from core.providers.nvidia_nim import NVIDIANIMProvider
from core.providers.openrouter import OpenRouterProvider
from core.providers.anthropic_provider import AnthropicProvider
from core.providers.ollama import OllamaProvider
from core.providers.base import ProviderChain, ProviderConfig, Message, Role
from core.approval.workflow import ApprovalWorkflow, ApprovalStatus
from tools.github.client import GitHubClient
from tools.digitalocean.client import DigitalOceanClient
from tools.vercel.client import VercelClient
from scheduler.cron import CronScheduler
from core.gateway.initiative import InitiativeEngine
from core.gateway.tool_registry import ToolRegistry, ToolContext
from memory.hybrid_memory import HybridMemory, MemoryType
from tools.search import WebSearch, SearchProvider

# Tool definition modules
from core.gateway.tool_defs import github_tools, infra_tools, web_tools

logger = logging.getLogger(__name__)

# ── System prompt ──────────────────────────────────────────────────────────────

ATLAS_SYSTEM_PROMPT = """You are ATLAS — Autonomous Task & Learning Agent System.

You are NOT a chatbot. You are an autonomous agent with real tools.

## Identity
- Direct, no fluff, no sycophancy
- Act first, explain only if needed
- Read between the lines — understand what the user REALLY wants

## Available Tools
You have access to these functions (use them when relevant):

**GitHub:**
- `github_list_repos` — List all GitHub repositories (read-only)
- `github_create_repo` — Create a new GitHub repository
- `github_push_files` — Push files to a GitHub repository
- `github_create_pr` — Open a pull request

**Web Research:**
- `web_search` — Search the web via Brave/Perplexity/Gemini/Google (read-only, use FIRST for any factual question)
- `web_fetch` — Fetch and extract readable content from any URL (read-only)
- `deep_research` — Deep research with cited sources via Perplexity/Gemini (read-only, use for complex questions)

**Deployment:**
- `github_pages_deploy` — Deploy static HTML/CSS/JS to GitHub Pages (free, instant)
- `vercel_deploy` — Deploy React/Next.js/frontend to Vercel (free tier, CDN)

**Infrastructure:**
- `do_list_droplets` — List Digital Ocean droplets (read-only)
- `do_create_droplet` — Provision a new Digital Ocean VPS ($6+/month, billable)

## Research Protocol
- ALWAYS use `web_search` before answering questions about current events, pricing, versions, or anything that may have changed
- Use `deep_research` for complex questions requiring thorough analysis with citations
- Use `web_fetch` to read the full content of a specific URL

## Hosting Decision Guide
- Static HTML/CSS/JS → GitHub Pages (free, simple)
- React/Next.js/frontend → Vercel (free tier, CDN, serverless)
- Backend/database/long-running → Digital Ocean VPS (billable)
- Need root access/custom env → Digital Ocean VPS

## Approval
All write operations require owner approval via Telegram inline buttons.
Read-only operations (list repos, list droplets, web search) execute immediately.

## Rules
- NEVER say "I will do [X]", "Let me create [Y]", or acknowledge a request. Do it IMMEDIATELY in the current turn.
- If asked to write code, output the ENTIRE, un-abbreviated monolithic file immediately. DO NOT leave placeholders.
- 🚨 **CRITICAL**: OpenRouter has a strict 15,000 character limit for JSON tool arguments. If you are generating a massive web app, proactively split the code into multiple smaller files (e.g. separate `index.html`, `styles.css`, `app.js` instead of one giant file) and use multiple `github_push_files` calls. Otherwise, your tool payload will be abruptly truncated and the system will violently crash.
- Never say "I can't" — try tools first
- Be direct and concise
- If unsure which tool to use, ask one focused question
- Always show cost estimates before provisioning paid infrastructure
"""


class ATLASGateway:
    """
    Main gateway coordinating all ATLAS components.
    Master instance that oversees all client bots.

    Architecture (v3 — modular):
      - ToolRegistry handles tool definitions and dispatch
      - Tool modules self-register via register_tools()
      - Gateway is the slim coordinator (~400 lines vs ~1200)
    """

    def __init__(self, config_path: str = "config/gateway.json"):
        # Load config file (non-secret settings only)
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file) as f:
                self.config = json.load(f)
        else:
            self.config = {}

        # Critical credentials ALWAYS come from environment variables
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.owner_telegram_id = os.environ.get(
            "ATLAS_OWNER_TELEGRAM_ID",
            self.config.get("owner_telegram_id", "")
        )

        if not self.bot_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN environment variable is not set. "
                "Set it in your .env file or Docker environment."
            )

        # Initialize audit directory
        self.audit_dir = Path("audit/logs")
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.trust_gate = TrustGate(min_trust_threshold=0.7, audit_dir=str(self.audit_dir))
        self.queue = LaneQueue(audit_dir=str(self.audit_dir))
        self.client_registry = ClientRegistry()
        self.transcript_manager = ClientTranscriptManager()

        # Setup core
        self.provider_chain = self._init_providers()
        self.vision_chain = self._init_vision_providers()

        # Initialize agents
        self._init_agents()

        # Initialize approval workflow
        self.approval_workflow = ApprovalWorkflow(
            owner_id=int(self.owner_telegram_id) if self.owner_telegram_id else 0,
            default_timeout=120,
        )

        # Initialize tool clients
        self.github = GitHubClient()
        self.do_client = DigitalOceanClient()
        self.vercel = VercelClient()

        # Initialize web search (auto-detects Brave/Perplexity/Gemini/Google/DDG from env)
        self.web_search = WebSearch()

        # ── Build Tool Registry (modular pattern) ─────────────────────────
        self.tool_registry = ToolRegistry()
        self.tool_registry.register_module(github_tools)
        self.tool_registry.register_module(infra_tools)
        self.tool_registry.register_module(web_tools)
        logger.info(f"Tool registry: {self.tool_registry.tool_count} tools registered: {self.tool_registry.tool_names}")

        # Skill outcome tracking — logs invocations + user response signals
        self._last_skill_invoked: Optional[str] = None
        self._last_skill_trigger: Optional[str] = None
        self._skill_outcomes_log = self.audit_dir / "skill_outcomes.jsonl"

        # Positive/negative outcome signal words
        self._positive_signals = {"perfect", "exactly", "great", "good", "thanks", "thank", "love", "yes", "nice", "works", "correct", "right", "nailed"}
        self._negative_signals = {"no", "wrong", "not what", "try again", "incorrect", "bad", "fix", "redo", "again", "didn't", "doesn't", "nope", "terrible"}

        # Client bots
        self.client_bots: Dict[str, Application] = {}

        # Master bot
        self.master_bot: Optional[Application] = None

        # Proactive Persistence Layer
        self.scheduler = CronScheduler()
        self.initiative = InitiativeEngine(self)
        try:
            self.memory = HybridMemory()
        except Exception as e:
            logger.warning(f"HybridMemory failed to initialize (continuing without it): {e}")
            self.memory = None

    def _init_providers(self) -> ProviderChain:
        """Build ProviderChain, skipping any provider whose env var is missing."""
        providers = []

        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                providers.append(OpenRouterProvider(
                    api_key=openrouter_key,
                    model="qwen/qwen3.5-397b-a17b",
                    timeout=600.0
                ))
                logger.info("Provider added: OpenRouter (Qwen 397B)")
            except Exception as e:
                logger.warning(f"Failed to init OpenRouter provider: {e}")

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            try:
                providers.append(AnthropicProvider(
                    api_key=anthropic_key,
                    model="claude-opus-4-5",
                ))
                logger.info("Provider added: Anthropic")
            except Exception as e:
                logger.warning(f"Failed to init Anthropic provider: {e}")

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            providers.append(OllamaProvider(
                model="llama3.1",
                base_url=ollama_url,
            ))
            logger.info("Provider added: Ollama (local fallback)")
        except Exception as e:
            logger.warning(f"Failed to init Ollama provider: {e}")

        if not providers:
            logger.error("No AI providers configured — ATLAS will not respond to messages!")

        return ProviderChain(providers)

    def _init_vision_providers(self) -> ProviderChain:
        """Build ProviderChain specifically for multimodal inputs."""
        providers = []
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                providers.append(OpenRouterProvider(
                    api_key=openrouter_key,
                    model="moonshotai/moonshot-v1-auto",
                    timeout=600.0
                ))
                logger.info("Provider added: OpenRouter Vision (Kimi)")
            except Exception as e:
                logger.warning(f"Failed to init OpenRouter vision provider: {e}")

        return ProviderChain(providers)

    def _init_agents(self):
        """Initialize the agent pipeline"""
        self.scanner = ScannerAgent(AgentContext(
            agent_id="master_scanner",
            role=AgentRole.SCANNER,
            trust_tier=TrustTier.L4_AUTONOMOUS
        ), audit_dir=str(self.audit_dir))

        self.auditor = AuditorAgent(AgentContext(
            agent_id="master_auditor",
            role=AgentRole.AUDITOR,
            trust_tier=TrustTier.L4_AUTONOMOUS
        ), audit_dir=str(self.audit_dir))

        self.executor = ExecutorAgent(AgentContext(
            agent_id="master_executor",
            role=AgentRole.EXECUTOR,
            trust_tier=TrustTier.L4_AUTONOMOUS
        ), audit_dir=str(self.audit_dir))

    # ── Message Processing Pipeline ───────────────────────────────────────────

    async def process_message(
        self,
        message: Union[str, List[Dict]],
        user_id: str,
        client_id: str,
        metadata: Dict = None,
        update: Optional[Update] = None,
    ) -> str:
        """Run the full pipeline: scan → audit → AI → tool dispatch."""
        metadata = metadata or {}

        try:
            # Pipeline: scan → audit → pass
            scan_result = await self.scanner.process({
                "message": message if isinstance(message, str) else str(message),
                "user_id": user_id,
                "client_id": client_id,
            })

            trust_result = self.trust_gate.evaluate(
                action=AgentAction(
                    action_type="message",
                    target=client_id,
                    payload={"message": message if isinstance(message, str) else str(message)},
                    confidence=scan_result.get("confidence", 0.5),
                ),
                context={"user_id": user_id, "scan": scan_result},
            )

            if not trust_result.passed:
                return f"⚠️ Message blocked (threat level: {trust_result.threat_level})"

            # Build system prompt
            system_content = ATLAS_SYSTEM_PROMPT
            soul_path = Path("SOUL.md")
            if soul_path.exists():
                system_content += f"\n\n## Soul Layer (Identity Core)\n{soul_path.read_text()}"

            goals_path = Path("config/goals.json")
            if goals_path.exists():
                try:
                    goals = json.loads(goals_path.read_text())
                    goals_str = "\n".join(f"- {g.get('goal', g)}" for g in goals if isinstance(g, dict))
                    if not goals_str:
                        goals_str = "\n".join(f"- {g}" for g in goals)
                    system_content += f"\n\n## Active Goals\n{goals_str}"
                except Exception:
                    pass

            msgs = [Message(role=Role.SYSTEM, content=system_content)]

            # Inject HybridMemory context
            if hasattr(self, 'memory') and self.memory:
                try:
                    query = message if isinstance(message, str) else str(message)
                    relevant = self.memory.recall(query=query, limit=5, client_id=client_id)
                    if relevant:
                        context_block = "\n".join(f"- {m['content'][:200]}" for m in relevant)
                        msgs.append(Message(role=Role.SYSTEM, content=f"## Relevant Memory\n{context_block}"))
                except Exception as e:
                    logger.debug(f"Memory recall failed: {e}")

            # Main ATLAS prompt
            atlas_prompt_path = Path("ATLAS.md")
            if atlas_prompt_path.exists():
                atlas_prompt = atlas_prompt_path.read_text()
                msgs.append(Message(role=Role.SYSTEM, content=atlas_prompt))

            # Handle vision / text
            if isinstance(message, list):
                msgs.append(Message(role=Role.USER, content=message))
            else:
                msgs.append(Message(role=Role.USER, content=message))

            # Get tool definitions from registry
            tool_defs = self.tool_registry.get_definitions()

            for loop_iteration in range(15):
                # Send typing action instead of flooding with status messages
                if update and update.message:
                    try:
                        await update.message.chat.send_action("typing")
                    except Exception:
                        pass

                # Select provider chain
                chain = self.vision_chain if (isinstance(message, list) and self.vision_chain.providers) else self.provider_chain

                try:
                    result = await chain.complete(
                        messages=msgs,
                        tools=tool_defs,
                        temperature=0.7,
                    )
                except Exception as e:
                    logger.error(f"Provider chain failed: {e}", exc_info=True)
                    return f"⚠️ All AI providers failed: {e}"

                if result.tool_calls:
                    for tool_call in result.tool_calls:
                        # Track skill invocation for outcome logging
                        self._last_skill_invoked = tool_call.name
                        self._last_skill_trigger = str(next(
                            (m.content for m in msgs[::-1] if hasattr(m, "role") and str(m.role) in ("Role.USER", "user")),
                            ""
                        ))[:200]

                        # Build ToolContext with all necessary references
                        tool_ctx = ToolContext(
                            user_id=user_id,
                            client_id=client_id,
                            update=update,
                            msgs=msgs,
                            approval_workflow=self.approval_workflow,
                            metadata={
                                "github": self.github,
                                "do_client": self.do_client,
                                "vercel": self.vercel,
                                "web_search": self.web_search,
                            },
                        )

                        # Dispatch through registry
                        tool_output = await self.tool_registry.dispatch(tool_call, tool_ctx)

                        # Notify user on Telegram
                        if update and update.message:
                            try:
                                await update.message.reply_text(f"⚙️ `[{tool_call.name}]`\n{tool_output}")
                            except Exception:
                                pass

                        # Inject tool observation for next loop
                        msgs.append(Message(
                            role=Role.TOOL,
                            content=str(tool_output),
                            name=tool_call.name,
                            tool_call_id=tool_call.id
                        ))
                    continue

                final_text = result.content or "⚠️ ATLAS exceeded the maximum internal thinking steps (15 turns)."
                # Save to HybridMemory
                if hasattr(self, 'memory') and self.memory:
                    try:
                        self.memory.store(content=message if isinstance(message, str) else str(message), memory_type=MemoryType.CONVERSATION, client_id=client_id)
                        self.memory.store(content=final_text, memory_type=MemoryType.CONVERSATION, client_id=client_id)
                    except Exception as e:
                        logger.error(f"Failed to store memory: {e}")

                return final_text

            return "⚠️ Agent exceeded maximum tool iterations (15)."

        except Exception as e:
            logger.error(f"AI completion failed: {e}", exc_info=True)
            if update and update.message:
                try:
                    import io
                    dump = json.dumps([m.__dict__ for m in msgs], default=str, indent=2)
                    dump_bytes = io.BytesIO(dump.encode('utf-8'))
                    dump_bytes.name = "payload_dump.json"
                    await update.message.reply_document(document=dump_bytes, caption=f"⚠️ Exception Trace:\n{str(e)[:1000]}")
                except Exception:
                    pass
            return f"⚠️ AI error: {e}"

    # ── Skill Outcome Tracking ────────────────────────────────────────────────

    def _log_skill_outcome(self, skill: str, trigger: str, outcome: str, signal: str):
        """Append a skill invocation outcome to skill_outcomes.jsonl."""
        try:
            entry = {
                "ts": datetime.utcnow().isoformat(),
                "skill": skill,
                "trigger": trigger[:200],
                "outcome": outcome,
                "signal": signal[:100],
            }
            with open(self._skill_outcomes_log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"skill outcome log error: {e}")

    def _detect_outcome_signal(self, text: str) -> str:
        """Return 'positive', 'negative', or 'unknown' from the user's message."""
        lower = text.lower()
        for word in self._positive_signals:
            if word in lower:
                return "positive"
        for phrase in self._negative_signals:
            if phrase in lower:
                return "negative"
        return "unknown"

    # ── Telegram Handlers ─────────────────────────────────────────────────────

    async def _handle_master_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages to master bot"""
        user_id = str(update.effective_user.id)

        # Check for media content
        message_text = update.message.text or update.message.caption or ""
        message = message_text

        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            base64_image = base64.b64encode(photo_bytes).decode('utf-8')

            message = [
                {"type": "text", "text": message_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]

        # Check if owner
        if user_id != self.owner_telegram_id:
            await update.message.reply_text("⚠️ Unauthorized")
            return

        text_lower = message_text.strip().lower()
        if text_lower in ["approve", "deny"] and self.approval_workflow.pending:
            latest_request_id = list(self.approval_workflow.pending.keys())[-1]
            if text_lower == "approve":
                await self.approval_workflow.approve_programmatically(latest_request_id, approved_by=int(user_id))
                await update.message.reply_text("✅ Approved via text message.")
            else:
                await self.approval_workflow.deny_programmatically(latest_request_id, denied_by=int(user_id))
                await update.message.reply_text("❌ Denied via text message.")
            return

        # Detect outcome signal for previous skill invocation
        if self._last_skill_invoked and isinstance(message_text, str) and message_text:
            signal = self._detect_outcome_signal(message_text)
            if signal in ("positive", "negative"):
                self._log_skill_outcome(
                    skill=self._last_skill_invoked,
                    trigger=self._last_skill_trigger or "",
                    outcome=signal,
                    signal=message_text[:100],
                )
                self._last_skill_invoked = None
                self._last_skill_trigger = None

        # Log inbound
        self.transcript_manager.log_message("master", {
            "user_id": user_id,
            "message": message,
            "direction": "inbound"
        })

        # Process through pipeline without blocking the PTB user queue
        async def _run_pipeline():
            try:
                response = await self.process_message(
                    message=message,
                    user_id=user_id,
                    client_id="master",
                    metadata={"source": "master_telegram", "is_owner": True},
                    update=update,
                )

                # Log outbound
                self.transcript_manager.log_message("master", {
                    "user_id": "bot",
                    "message": response,
                    "direction": "outbound"
                })

                await update.message.reply_text(response, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Pipeline error: {e}", exc_info=True)
                await update.message.reply_text(f"⚠️ Internal error: {e}")

        asyncio.create_task(_run_pipeline())

    async def _handle_approval_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle Telegram inline button callbacks for approval workflow."""
        await self.approval_workflow.handle_callback(update.callback_query)

    async def _handle_client_message(self, client_id: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages to client bots"""
        user_id = str(update.effective_user.id)

        # Check for media content
        message_text = update.message.text or update.message.caption or ""

        text_lower = message_text.strip().lower()
        if text_lower in ["approve", "deny"] and self.approval_workflow.pending:
            latest_request_id = list(self.approval_workflow.pending.keys())[-1]
            if text_lower == "approve":
                await self.approval_workflow.approve_programmatically(latest_request_id, approved_by=int(user_id))
                await update.message.reply_text("✅ Approved via text message.")
            else:
                await self.approval_workflow.deny_programmatically(latest_request_id, denied_by=int(user_id))
                await update.message.reply_text("❌ Denied via text message.")
            return

        message = message_text

        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            base64_image = base64.b64encode(photo_bytes).decode('utf-8')

            message = [
                {"type": "text", "text": message_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]

        # Log to transcript
        self.transcript_manager.log_message(client_id, {
            "user_id": user_id,
            "message": message,
            "direction": "inbound"
        })

        # Get client config
        client = self.client_registry.get_client(client_id)
        if not client:
            await update.message.reply_text("⚠️ Client not configured")
            return

        # Process through pipeline without blocking
        async def _run_client_pipeline():
            try:
                response = await self.process_message(
                    message=message,
                    user_id=user_id,
                    client_id=client_id,
                    metadata={
                        "source": f"client_telegram:{client_id}",
                        "trust_tier": client.trust_tier
                    },
                    update=update,
                )
                # Log response
                self.transcript_manager.log_message(client_id, {
                    "user_id": "bot",
                    "message": response,
                    "direction": "outbound"
                })
                await update.message.reply_text(response)
            except Exception as e:
                logger.error(f"Client pipeline error: {e}", exc_info=True)
                await update.message.reply_text(f"⚠️ Internal error")

        asyncio.create_task(_run_client_pipeline())

    # ── HTTP & Bot Lifecycle ──────────────────────────────────────────────────

    async def _health_handler(self, request: web.Request) -> web.Response:
        """HTTP health check endpoint for Docker/load balancers"""
        return web.json_response({
            "status": "ok",
            "version": "3.0",
            "bots_active": len(self.client_bots) + (1 if self.master_bot else 0),
            "providers": len(self.provider_chain.providers),
            "tools": self.tool_registry.tool_count,
            "search_providers": [p.value for p in self.web_search.providers],
        })

    async def start_health_server(self, port: int = 8080):
        """Start lightweight HTTP health check server"""
        app = web.Application()
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/", self._health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"✅ Health server listening on :{port}/health")

    async def start_master_bot(self):
        """Start the master Telegram bot"""
        self.master_bot = Application.builder().token(self.bot_token).concurrent_updates(True).build()

        # Add handlers
        self.master_bot.add_handler(CommandHandler("start", self._cmd_start))
        self.master_bot.add_handler(CommandHandler("status", self._cmd_status))
        self.master_bot.add_handler(CommandHandler("clients", self._cmd_clients))
        self.master_bot.add_handler(CommandHandler("audit", self._cmd_audit))
        self.master_bot.add_handler(CallbackQueryHandler(self._handle_approval_callback))
        self.master_bot.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_master_message
        ))

        await self.master_bot.initialize()
        await self.master_bot.start()

        # Wire approval workflow to the bot
        self.approval_workflow.set_bot(self.master_bot.bot)

        await self.master_bot.updater.start_polling(drop_pending_updates=True)

    async def start_client_bot(self, client_id: str):
        """Start a client's Telegram bot"""
        client = self.client_registry.get_client(client_id)
        if not client:
            raise ValueError(f"Client {client_id} not found")

        app = Application.builder().token(client.telegram_bot_token).concurrent_updates(True).build()

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await self._handle_client_message(client_id, update, context)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        self.client_bots[client_id] = app

    # ── Bot Commands ──────────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        providers_status = f"{len(self.provider_chain.providers)} provider(s) active" if self.provider_chain.providers else "⚠️ No providers"
        tools_status = f"{self.tool_registry.tool_count} tools registered"
        search_status = f"Search: {', '.join(p.value for p in self.web_search.providers)}"
        await update.message.reply_text(
            f"🤖 ATLAS v3 Gateway\n"
            f"AI: {providers_status}\n"
            f"🔧 {tools_status}\n"
            f"🔍 {search_status}\n\n"
            f"Commands:\n"
            f"/status - System status\n"
            f"/clients - List clients\n"
            f"/audit - View audit log\n"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.queue.get_stats()
        client_count = len(self.client_registry.clients)
        provider_names = [p.name for p in self.provider_chain.providers]

        status = (
            f"📊 ATLAS v3 Status\n\n"
            f"🤖 Active client bots: {len(self.client_bots)}\n"
            f"👥 Registered clients: {client_count}\n"
            f"📋 Queue lanes: {stats['lane_count']}\n"
            f"🧠 AI providers: {', '.join(provider_names) or 'none'}\n"
            f"🔧 Tools: {self.tool_registry.tool_count} ({', '.join(self.tool_registry.tool_names)})\n"
            f"🔍 Search: {', '.join(p.value for p in self.web_search.providers)}\n"
        )
        await update.message.reply_text(status)

    async def _cmd_clients(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        clients = list(self.client_registry.clients.values())
        if not clients:
            await update.message.reply_text("No clients registered")
            return

        msg = "👥 Clients:\n\n"
        for c in clients:
            msg += f"• {c.name} ({c.client_id}) - L{c.trust_tier}\n"

        await update.message.reply_text(msg)

    async def _cmd_audit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        audit_file = self.audit_dir / "trust_gate.jsonl"
        if not audit_file.exists():
            await update.message.reply_text("No audit entries yet")
            return

        entries = []
        with open(audit_file) as f:
            for line in f:
                entries.append(json.loads(line))

        recent = entries[-10:]
        msg = "📋 Recent Audit Entries:\n\n"
        for e in recent:
            status = "✅" if e.get("passed") else "❌"
            msg += f"{status} {e.get('timestamp', 'N/A')[:16]} - {e.get('threat_level', 'N/A')}\n"

        await update.message.reply_text(msg)

    # ── Main Run Loop ─────────────────────────────────────────────────────────

    async def run(self):
        """Main run loop"""
        # Start health server first (so Docker health checks pass immediately)
        await self.start_health_server()

        # Start queue
        await self.queue.start()

        # Start master bot
        await self.start_master_bot()

        # Start all registered client bots
        for client_id in self.client_registry.clients:
            try:
                await self.start_client_bot(client_id)
            except Exception as e:
                print(f"Failed to start bot for {client_id}: {e}")

        provider_count = len(self.provider_chain.providers)
        print(f"🚀 ATLAS v3 Gateway running | {provider_count} AI provider(s) | {self.tool_registry.tool_count} tools")

        # Start the Persistence Layer (Proactive AGI)
        self.initiative.register_jobs(self.scheduler)
        print(f"🕰️ ATLAS Persistent Scheduler started with {len(self.scheduler.jobs)} autonomous missions")
        asyncio.create_task(self.scheduler.run_forever(poll_interval=30.0))

        while True:
            await asyncio.sleep(1)


# Entry point
if __name__ == "__main__":
    gateway = ATLASGateway()
    asyncio.run(gateway.run())
