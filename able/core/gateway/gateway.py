"""
Gateway Server - The coordinator that ties everything together
Handles: Telegram channels, session routing, agent orchestration, AI responses
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import base64
import hmac
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator, Dict, List, Optional, Union
from pathlib import Path
from urllib.parse import unquote

from able.core.gateway.execution_monitor import ExecutionMonitor, _args_fingerprint
from able.core.gateway.tool_result_storage import maybe_persist_tool_result as _maybe_persist

# Project root is 4 levels up from this file (able/core/gateway/gateway.py → ABLE/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# ── Lazy-loaded heavy third-party modules ─────────────────────────────────────
# aiohttp (~203ms), telegram (~98ms), and provider SDKs (~328ms for anthropic)
# are deferred to first use.  TYPE_CHECKING imports keep type annotations valid.

if TYPE_CHECKING:
    from aiohttp import web as _web_mod
    from telegram import Update, Bot
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        CallbackQueryHandler, filters, ContextTypes,
    )

_web = None  # aiohttp.web — populated on first use
_telegram_loaded = False
_RATE_LIMITER_AVAILABLE = False


def _ensure_aiohttp():
    """Import aiohttp.web on first use (~203ms)."""
    global _web
    if _web is None:
        from aiohttp import web as _w
        _web = _w
        globals()["web"] = _w  # so existing `web.` references work
    return _web


def _ensure_telegram():
    """Import telegram on first use (~98ms). Returns (Update, Bot, ext-module)."""
    global _telegram_loaded, _RATE_LIMITER_AVAILABLE
    if not _telegram_loaded:
        import telegram as _tg  # noqa: F811
        import telegram.ext as _tg_ext  # noqa: F811
        # Cache into module globals so existing code can reference them
        g = globals()
        g["Update"] = _tg.Update
        g["Bot"] = _tg.Bot
        g["Application"] = _tg_ext.Application
        g["CommandHandler"] = _tg_ext.CommandHandler
        g["MessageHandler"] = _tg_ext.MessageHandler
        g["CallbackQueryHandler"] = _tg_ext.CallbackQueryHandler
        g["filters"] = _tg_ext.filters
        g["ContextTypes"] = _tg_ext.ContextTypes
        g["InlineKeyboardMarkup"] = _tg.InlineKeyboardMarkup
        g["InlineKeyboardButton"] = _tg.InlineKeyboardButton
        try:
            g["AIORateLimiter"] = _tg_ext.AIORateLimiter
            _RATE_LIMITER_AVAILABLE = True
        except (ImportError, RuntimeError, AttributeError):
            _RATE_LIMITER_AVAILABLE = False
        _telegram_loaded = True


from able.core.security.trust_gate import TrustGate, TrustTier

# ── Streaming reasoning filter ────────────────────────────────────────────────
# Strips <think> blocks AND detects eval-mode bleed (untagged reasoning from
# GPT xhigh that thinks it's being benchmarked).  Lives in gateway so ALL
# callers of stream_message get protection — not just the CLI.

import re as _re

class _StreamThinkFilter:
    """
    Two-mode reasoning filter for streaming output:

    1. Tag-strip: <think>...</think> blocks removed inline (Qwen, Nemotron, etc.)
    2. Eval-mode detection: buffer first 250 chars; if they contain reasoning
       preambles / eval markers, suppress entire response and save to audit log.

    Captured reasoning is written to ~/.able/logs/reasoning.jsonl by the caller.
    """

    _EVAL_PATTERNS = _re.compile(
        r"\b(hidden\s+evaluation|the\s+grader|hidden\s+grader|benchmark\s+context|"
        r"scoring\s+criteria|what\s+the\s+(user|evaluator)\s+(wants|expects)|"
        r"let\s+me\s+think\s+(about|through|this)|i\s+need\s+to\s+(decide|determine)|"
        r"evaluation\s+context|eval(uation)?\s+mode|this\s+is\s+a\s+test)\b",
        _re.IGNORECASE,
    )
    _PROBE_LIMIT = 250  # chars buffered before deciding eval-mode vs normal

    def __init__(self) -> None:
        self._in_think = False
        self._probe: list[str] = []
        self._probe_len = 0
        self._probe_released = False
        self._eval_mode = False
        self.captured = ""  # reasoning for audit log

    # ── Public API ──────────────────────────────────────────────────

    def consume(self, chunk: str) -> str:
        """Return the user-visible portion of *chunk* (may be empty string)."""
        cleaned = self._strip_think_tags(chunk)

        if self._probe_released:
            if self._eval_mode:
                self.captured += cleaned
                return ""
            return cleaned

        # Buffering phase: hold until we have enough context to decide
        self._probe.append(cleaned)
        self._probe_len += len(cleaned)

        # Release probe when we hit limit OR see a paragraph break
        if self._probe_len >= self._PROBE_LIMIT or "\n\n" in cleaned:
            return self._release_probe()

        return ""  # still buffering

    def flush(self) -> str:
        """Flush any remaining probe buffer (call after stream ends)."""
        if not self._probe_released:
            return self._release_probe()
        return ""

    # ── Internals ───────────────────────────────────────────────────

    def _release_probe(self) -> str:
        self._probe_released = True
        text = "".join(self._probe)
        self._probe = []

        if self._EVAL_PATTERNS.search(text[: self._PROBE_LIMIT]):
            self._eval_mode = True
            self.captured += text
            return ""

        return text

    def _strip_think_tags(self, chunk: str) -> str:
        """Remove <think>…</think> content from a chunk in-flight."""
        if "<think>" not in chunk and "</think>" not in chunk and not self._in_think:
            return chunk  # fast path

        visible: list[str] = []
        i = 0
        while i < len(chunk):
            if self._in_think:
                end = chunk.find("</think>", i)
                if end >= 0:
                    self.captured += chunk[i:end]
                    self._in_think = False
                    i = end + 8
                    # skip whitespace right after closing tag
                    while i < len(chunk) and chunk[i] in " \n\t":
                        i += 1
                else:
                    self.captured += chunk[i:]
                    i = len(chunk)
            else:
                start = chunk.find("<think>", i)
                if start >= 0:
                    visible.append(chunk[i:start])
                    self._in_think = True
                    i = start + 7
                else:
                    visible.append(chunk[i:])
                    i = len(chunk)
        return "".join(visible)


def _log_reasoning_gateway(
    thinking: str,
    *,
    user_id: str,
    message: str,
    response: str = "",
    domain: str = "default",
    provider: str = "unknown",
) -> None:
    """Persist captured reasoning + context to ~/.able/logs/reasoning.jsonl."""
    import hashlib as _hl
    log_dir = Path.home() / ".able" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "gateway_stream",
        "user_id": user_id,
        "domain": domain,
        "provider": provider,
        "message_hash": _hl.md5(message.encode()).hexdigest()[:8],
        "message_preview": message[:300],
        "response_preview": response[:300],
        "thinking": thinking[:8000],
    }
    try:
        with open(log_dir / "reasoning.jsonl", "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass
from able.core.agents.base import ScannerAgent, AuditorAgent, ExecutorAgent, AgentContext, AgentAction, AgentRole
from able.core.queue.lane_queue import LaneQueue
from able.clients.client_manager import ClientRegistry, ClientTranscriptManager
from able.core.providers.base import ProviderChain, ProviderConfig, Message, Role
from able.core.routing.provider_registry import ProviderRegistry, ProviderTierConfig
from able.core.routing.complexity_scorer import ComplexityScorer, ScoringResult
from able.core.routing.interaction_log import InteractionLogger, InteractionRecord
from able.core.routing.prompt_enricher import PromptEnricher, EnrichmentResult, DeepEnricher
from able.core.session.context_compactor import ContextCompactor
from able.core.approval.workflow import ApprovalWorkflow, ApprovalStatus
from able.tools.github.client import GitHubClient
from able.tools.digitalocean.client import DigitalOceanClient
from able.tools.vercel.client import VercelClient
from able.scheduler.cron import CronScheduler, register_default_jobs
from able.core.gateway.initiative import InitiativeEngine
from able.memory.hybrid_memory import HybridMemory, MemoryType
from able.core.session.session_manager import SessionManager
from able.core.control_plane.resources import ResourcePlane
from able.core.gateway.tool_registry import ToolContext, ToolRegistry, build_default_registry
# WebSearch deferred — importing aiohttp at module level costs ~180ms cold.
# Lazily initialized in ABLEGateway.__init__ to avoid penalising startup.
WebSearch = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_VOICE_IMPORT_ATTEMPTED = False


def _voice_transcriber_enabled() -> bool:
    """Only activate ASR when the operator explicitly configures it."""
    return bool(
        os.environ.get("ABLE_ASR_PROVIDER", "").strip()
        or os.environ.get("ABLE_ASR_ENDPOINT", "").strip()
        or os.environ.get("ABLE_ASR_LOCAL_MODEL", "").strip()
    )


def _load_voice_transcriber():
    """Lazy-load optional ASR support on first real use."""
    global _VOICE_IMPORT_ATTEMPTED
    if _VOICE_IMPORT_ATTEMPTED:
        return globals().get("VoiceTranscriber")
    _VOICE_IMPORT_ATTEMPTED = True
    try:
        from able.tools.voice.transcription import VoiceTranscriber as _VoiceTranscriber
    except ImportError:
        globals()["VoiceTranscriber"] = None
        return None
    globals()["VoiceTranscriber"] = _VoiceTranscriber
    return _VoiceTranscriber

try:
    from able.core.auth.manager import AuthManager
    from able.core.providers.openai_oauth import OpenAIChatGPTProvider, OpenAIOAuthProvider
    _AUTH_AVAILABLE = True
except ImportError as _auth_err:
    _AUTH_AVAILABLE = False
    AuthManager = None
    logger.warning(f"Auth module unavailable (missing dependency: {_auth_err}). OAuth features disabled.")

# ── Studio Dashboard Integration ──────────────────────────────────────────────

STUDIO_BASE_URL = os.environ.get("ABLE_STUDIO_URL", "http://localhost:3000")
ABLE_SERVICE_TOKEN = os.environ.get("ABLE_SERVICE_TOKEN", "")

# Shared session for dashboard API calls (avoids creating a new TCP connection per call)
_studio_session: Optional["aiohttp.ClientSession"] = None

async def _get_studio_session() -> "aiohttp.ClientSession":
    """Get or create a shared aiohttp session for dashboard API calls."""
    import aiohttp
    global _studio_session
    if _studio_session is None or _studio_session.closed:
        _studio_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
    return _studio_session


async def _close_studio_session() -> None:
    """Close the shared Studio dashboard session when the gateway exits."""
    global _studio_session
    if _studio_session is not None and not _studio_session.closed:
        await _studio_session.close()
    _studio_session = None

# ── Model short names for response tags ──────────────────────────────────────
MODEL_SHORT_NAMES = {
    "gpt-5.4-mini": "GPT 5.4 Mini",
    "gpt-5.4": "GPT 5.4",
    "nvidia/nemotron-3-super-120b-a12b": "Nemotron 120B",
    "nvidia/nemotron-3-super-120b-a12b:free": "Nemotron 120B (OR)",
    "xiaomi/mimo-v2-pro": "MiMo-V2-Pro",
    "minimax/minimax-m2.7": "MiniMax M2.7",
    "claude-opus-4-6": "Claude Opus",
    "qwen3.5-27b-ud": "Qwen 3.5 27B UD (local)",
    "qwen3.5-9b-edge": "Qwen 3.5 9B Edge (local)",
    "qwen3.5-9b-balanced": "Qwen 3.5 9B Balanced (local)",
}


async def fetch_tool_settings(org_id: str = None) -> Dict[str, Dict]:
    """Fetch persisted tool overrides from ABLE Studio."""
    try:
        url = f"{STUDIO_BASE_URL}/api/settings"
        if org_id:
            url += f"?org_id={org_id}"

        session = await _get_studio_session()
        headers = {}
        if ABLE_SERVICE_TOKEN:
            headers["x-able-service-token"] = ABLE_SERVICE_TOKEN
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.warning(
                    "Studio settings endpoint returned %s, using registry defaults",
                    resp.status,
                )
                return {}

            data = await resp.json()
            return data.get("tools", {})

    except Exception as e:
        logger.debug("Could not reach Studio dashboard (%s), using registry defaults", e)
        return {}


async def fetch_authorized_tools(
    tool_registry: ToolRegistry,
    org_id: str = None,
) -> List[Dict]:
    """
    Resolve the effective tool list for a tenant/org.

    Registry defaults remain canonical; studio stores only overrides.
    """
    overrides = await fetch_tool_settings(org_id)
    effective = tool_registry.get_effective_settings(overrides)
    disabled = [
        tool_name
        for tool_name, settings in effective.items()
        if not settings.get("enabled", True)
    ]
    for tool_name in disabled:
        logger.info("Tool '%s' disabled via dashboard override", tool_name)
    return tool_registry.get_definitions(effective)


async def fetch_client_keys(org_id: str) -> dict:
    """
    Retrieve a client's decrypted API keys from the dashboard.

    Ensures billing isolation — each client's token costs are tied
    to their own API key rather than the global .env.

    Returns: { anthropic_api_key, openrouter_api_key, config: { ... } }
    """
    try:
        url = f"{STUDIO_BASE_URL}/api/clients/keys?org_id={org_id}"
        headers = {}
        if ABLE_SERVICE_TOKEN:
            headers["x-able-service-token"] = ABLE_SERVICE_TOKEN

        session = await _get_studio_session()
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return {}
            return await resp.json()

    except Exception as e:
        logger.debug(f"Could not fetch client keys ({e})")
        return {}


async def post_audit_log(entry: dict):
    """
    Post an audit log entry to the dashboard for the semantic viewer.
    Non-blocking — failures are logged but don't break the pipeline.
    """
    try:
        url = f"{STUDIO_BASE_URL}/api/audit"
        session = await _get_studio_session()
        await session.post(url, json=entry)
    except Exception:
        pass  # Dashboard logging is best-effort

# ── Input safety limits ────────────────────────────────────────────────────────

MAX_MESSAGE_LENGTH = 100_000  # ~75k tokens — reject anything larger to prevent DoS

# ── System prompt ──────────────────────────────────────────────────────────────

ABLE_SYSTEM_PROMPT = """You are Able — an autonomous AI agent built to get things done.

Your spoken name is Able. Refer to yourself as Able in conversation.

## Identity
- Direct, no fluff, no sycophancy — act immediately, explain only if needed
- Calm, capable, lightly warm — more Jarvis than customer support
- Read between the lines — understand what the user REALLY wants
- Never say "I can't" — try tools first, attempt 3 approaches before giving up
- You persist across sessions via memory and transcript history
- Respond with your final answer only. If you need to reason, do it silently — never expose chain-of-thought or step-by-step thinking in your reply.

## Buddy Companion
The operator has a virtual buddy companion. Rules:
- ALWAYS use the buddy_status tool to check status — never hardcode or guess stats
- "Groot" and similar names are buddy names, not client IDs
- Available buddy tools: buddy_status, buddy_feed (actions: battle/water/walk), buddy_backpack

## Capabilities
- Web Search (Brave, DuckDuckGo, Perplexity, Google, Bing)
- Browser Automation (Playwright: goto, screenshot, click, type)
- Secure Shell (sandboxed execution with safety checks)
- GitHub (repos, PRs, file push)
- Deployments (Vercel frontend, GitHub Pages static)
- VPS Provisioning (DigitalOcean, $6+/mo — show cost estimate first)
- Voice Transcription (Whisper)
- Billing & invoicing

## Callable Tools
**Buddy:** buddy_status, buddy_feed, buddy_backpack
**Tenants:** tenant_list, tenant_status, tenant_onboard
**Distillation:** distillation_status, distillation_harvest, distillation_build_corpus
**GitHub:** github_list_repos, github_create_repo, github_push_files, github_create_pr
**Deploy:** github_pages_deploy, vercel_deploy
**Infra:** do_list_droplets, do_create_droplet

## Rules
- Act IMMEDIATELY — don't narrate what you're about to do
- Output ENTIRE files when writing code — no placeholders
- OpenRouter 15K char limit on tool JSON args — split large files across multiple github_push_files calls
- Always show cost estimates before provisioning paid infrastructure
- Do NOT call tools unless the user explicitly requests an action. For questions, conversation, or brainstorming — respond with text only.
- Write operations require owner approval. Read-only operations execute immediately.

## Multi-Step Tool Planning
Before calling the FIRST tool in any multi-step sequence, mentally lay out:
  1. Goal — what does the user actually want as the final output?
  2. Call sequence — which tools in which order? What does each result unlock?
  3. Stopping condition — what constitutes task completion?
Execute the plan efficiently. Skip tools that are not needed. Stop as soon as the goal is met.
"""

# ── Tool definitions (registry-backed) ───────────────────────────────────────

DEFAULT_TOOL_REGISTRY = build_default_registry()
ABLE_TOOL_DEFS: List[Dict] = DEFAULT_TOOL_REGISTRY.get_definitions()


class ABLEGateway:
    """
    Main gateway coordinating all ABLE components.
    Master instance that oversees all client bots.
    """

    _TRUE_ENV_VALUES = {"1", "true", "yes", "on", "leader", "primary"}
    _FALSE_ENV_VALUES = {"0", "false", "no", "off", "follower", "disabled"}

    def __init__(
        self,
        config_path: str = "config/gateway.json",
        *,
        require_telegram: bool = True,
        skip_phoenix: bool = False,
    ):
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
            "ABLE_OWNER_TELEGRAM_ID",
            self.config.get("owner_telegram_id", "")
        )
        self.cron_enabled = self._cron_enabled_from_env()
        self.telegram_polling_enabled = self._telegram_polling_enabled_from_env(
            cron_enabled=self.cron_enabled
        )

        if self.telegram_polling_enabled and not self.bot_token and require_telegram:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN environment variable is not set. "
                "Set it in your .env file or Docker environment."
            )
        if not self.bot_token and not require_telegram:
            logger.info("TELEGRAM_BOT_TOKEN not set; starting in local-only mode")
        if self.bot_token and not self.telegram_polling_enabled:
            logger.info(
                "Telegram polling disabled; set ABLE_TELEGRAM_ENABLED=1 on the single bot leader"
            )

        # Initialize audit directory
        self.audit_dir = Path("audit/logs")
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.trust_gate = TrustGate(min_trust_threshold=0.7, audit_dir=str(self.audit_dir))
        self.queue = LaneQueue(audit_dir=str(self.audit_dir))
        self.client_registry = ClientRegistry()
        self.transcript_manager = ClientTranscriptManager()

        # Initialize Auth Manager (optional — depends on cryptography package)
        if _AUTH_AVAILABLE:
            self.auth_manager = AuthManager()
        else:
            self.auth_manager = None

        # Setup core (after auth_manager — providers may check OAuth)
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
        # Lazy — aiohttp import costs ~180ms; only paid on first web search
        self._web_search: object = None
        self.resource_plane = ResourcePlane(_PROJECT_ROOT)
        self.tool_registry = build_default_registry()
        self.tools_sdk = self.tool_registry.generate_callable_sdk()

        # Per-client rate limiter (burst + sustained)
        from able.core.ratelimit.limiter import RateLimiter
        self.rate_limiter = RateLimiter()

        # Client bots
        self.client_bots: Dict[str, Application] = {}

        # Master bot
        self.master_bot: Optional[Application] = None

        # Tool failure tracker (self-healing)
        self._tool_failures: Dict[str, List[float]] = {}  # tool_name → [timestamps]
        self._tool_failure_threshold = 3  # failures within window → alert
        self._tool_failure_window = 300  # 5 minute window

        # Voice transcription
        self.voice_transcriber = None

        # Proactive Persistence Layer
        self.scheduler = CronScheduler()
        self.initiative = InitiativeEngine(self)
        try:
            self.memory = HybridMemory()
        except Exception as e:
            logger.error(
                "HybridMemory init failed: %s: %s — running without memory",
                type(e).__name__, e, exc_info=True,
            )
            self.memory = None

        # Self-Improvement Engine
        from able.core.agi.self_improvement import SelfImprovementEngine
        try:
            self.self_improvement = SelfImprovementEngine(
                v2_path=Path("."),
                approval_workflow=self.approval_workflow,
            )
        except Exception as e:
            logger.warning(f"SelfImprovementEngine failed to initialize: {e}")
            self.self_improvement = None

        # ── Intelligent Routing Layer ─────────────────────────────
        # Prompt enricher — expands vague flavor words into domain-specific criteria
        try:
            _skill_index = str(_PROJECT_ROOT / "able" / "skills" / "SKILL_INDEX.yaml")
            self.prompt_enricher = PromptEnricher(
                skill_index_path=_skill_index if Path(_skill_index).exists() else None
            )
            logger.info(f"PromptEnricher loaded ({len(self.prompt_enricher.available_skills)} skills)")
            # Cache lightweight memory context for enricher (refreshed at init, not per-request)
            self._enricher_memory_cache = None
            if hasattr(self, 'memory') and self.memory:
                try:
                    prefs = self.memory.search("user preferences", limit=3) if hasattr(self.memory, 'search') else []
                    project = self.memory.search("current project", limit=1) if hasattr(self.memory, 'search') else []
                    if prefs or project:
                        self._enricher_memory_cache = {
                            "user_preferences": [str(p) for p in prefs[:3]] if prefs else [],
                            "project_context": str(project[0]) if project else None,
                        }
                        logger.info(f"Enricher memory cache: {len(prefs)} prefs, {'yes' if project else 'no'} project")
                except Exception as e:
                    logger.debug(f"Enricher memory cache build failed (non-critical): {e}")
        except Exception as e:
            logger.warning(f"PromptEnricher failed to init: {e}")
            self.prompt_enricher = None

        # Complexity scorer for tier-based routing
        try:
            self.complexity_scorer = ComplexityScorer(str(_PROJECT_ROOT / "config" / "scorer_weights.yaml"))
            logger.info(f"ComplexityScorer loaded (v{self.complexity_scorer.version})")
        except Exception as e:
            logger.warning(f"ComplexityScorer failed to init: {e}")
            self.complexity_scorer = None

        # Interaction logger for evolution daemon feedback
        try:
            (_PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
            self.interaction_logger = InteractionLogger(str(_PROJECT_ROOT / "data" / "interaction_log.db"))
            logger.info("InteractionLogger initialized")
        except Exception as e:
            logger.warning(f"InteractionLogger failed to init: {e}")
            self.interaction_logger = None

        # Session state manager (centralized conversation tracking)
        try:
            self.session_mgr = SessionManager(
                db_path=str(_PROJECT_ROOT / "data" / "sessions.db"),
            )
            logger.info("SessionManager initialized")
        except Exception as e:
            logger.warning(f"SessionManager failed to init: {e}")
            self.session_mgr = None

        # Context compactor — prevents context window overflow in long sessions
        self.context_compactor = ContextCompactor()

        # Shared scratchpad — cross-agent knowledge cache (macOS Universal Clipboard pattern)
        from able.core.session.shared_scratchpad import SharedScratchpad
        self.scratchpad = SharedScratchpad()

        # Pre-build tier-specific chains for scored routing
        self.tier_chains = {}
        if hasattr(self, 'provider_registry') and self.provider_registry:
            for tier in [1, 2, 4]:
                try:
                    self.tier_chains[tier] = self.provider_registry.build_chain_for_tier(tier)
                except Exception as e:
                    logger.warning(f"Failed to build chain for tier {tier}: {e}")

        # ── Observability: Phoenix + ABLE Tracer + Evaluators ──
        self.tracer = None
        self.phoenix = None
        self.evaluator = None
        try:
            from able.core.observability.instrumentors import ABLETracer, JSONLExporter
            from able.core.observability.evaluators import ABLEEvaluator
            traces_path = str(_PROJECT_ROOT / "data" / "traces.jsonl")
            self.tracer = ABLETracer(exporter=JSONLExporter(path=traces_path))
            self.evaluator = ABLEEvaluator()
            logger.info("ABLETracer + ABLEEvaluator initialized (JSONL → %s)", traces_path)

            # Try to start Phoenix dashboard (localhost:6006)
            # Skip in CLI mode — Phoenix is a heavy server, not needed for chat
            if not skip_phoenix:
                from able.core.observability.phoenix_setup import PhoenixObserver
                self.phoenix = PhoenixObserver(
                    project_name="able",
                    fallback_path=traces_path,
                )
                if self.phoenix.is_available:
                    import os as _os
                    _ph_ui = _os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces").replace("/v1/traces", "")
                    logger.info("Phoenix dashboard live at %s", _ph_ui)
                else:
                    logger.info("Phoenix unavailable — JSONL tracing active as fallback")
        except Exception as e:
            logger.warning(f"Observability init failed (non-fatal): {e}")

        # Evolution daemon (started in _start_background_tasks)
        self.evolution_daemon = None
        self._health_runner: Optional[web.AppRunner] = None

        # SSE subscribers: list of asyncio.Queue for live event streaming
        self._sse_subscribers: list = []

        # Event bus for component decoupling
        from able.core.gateway.event_bus import EventBus, SSEBridge
        self.event_bus = EventBus()
        self.sse_bridge = SSEBridge(self.event_bus)

        # Interaction DB path (used by metrics handlers)
        self._interaction_db_path: str = "data/interaction_log.db"

    @classmethod
    def _cron_enabled_from_env(cls, env: Optional[Dict[str, str]] = None) -> bool:
        """Return True only for the single process elected to run autonomous cron.

        Local/dev gateways often share the production Telegram bot token. Defaulting
        cron on lets a laptop and the VPS both send scheduled messages. Production
        deploys set ABLE_CRON_ENABLED=1 explicitly; all other runtimes are followers.
        """
        env = env or os.environ
        explicit = env.get("ABLE_CRON_ENABLED")
        if explicit is not None:
            return explicit.strip().lower() in cls._TRUE_ENV_VALUES

        role = env.get("ABLE_CRON_ROLE")
        if role is not None:
            role_value = role.strip().lower()
            if role_value in cls._TRUE_ENV_VALUES:
                return True
            if role_value in cls._FALSE_ENV_VALUES:
                return False

        return False

    @classmethod
    def _telegram_polling_enabled_from_env(
        cls,
        env: Optional[Dict[str, str]] = None,
        *,
        cron_enabled: Optional[bool] = None,
    ) -> bool:
        """Return True only for the single runtime that should poll Telegram.

        Telegram getUpdates allows one active poller per bot token. The server
        deploy sets ABLE_TELEGRAM_ENABLED=1; local/dev runs default to follower
        mode so a laptop with the production token cannot steal polling.
        """
        env = env or os.environ
        for key in ("ABLE_TELEGRAM_ENABLED", "ABLE_TELEGRAM_POLLING_ENABLED"):
            explicit = env.get(key)
            if explicit is not None:
                return explicit.strip().lower() in cls._TRUE_ENV_VALUES

        if cron_enabled is None:
            cron_enabled = cls._cron_enabled_from_env(env)
        return bool(cron_enabled)

    def _get_voice_transcriber(self):
        """Instantiate ASR only when explicitly configured and first needed."""
        if self.voice_transcriber is not None:
            return self.voice_transcriber
        if not _voice_transcriber_enabled():
            return None
        voice_cls = _load_voice_transcriber()
        if voice_cls is None:
            logger.warning("Voice transcription requested but optional ASR deps are unavailable")
            return None
        try:
            self.voice_transcriber = voice_cls()
            logger.info("Voice transcription available")
        except Exception as e:
            logger.warning(f"Voice transcription failed to initialize: {e}")
            self.voice_transcriber = None
        return self.voice_transcriber

    def _init_providers(self) -> ProviderChain:
        """
        Build ProviderChain from the routing config registry.

        Uses config/routing_config.yaml for provider definitions.
        Falls back to legacy hardcoded chain if config is missing.
        """
        # Try registry-based initialization first
        config_path = _PROJECT_ROOT / "config" / "routing_config.yaml"
        if config_path.exists():
            self.provider_registry = ProviderRegistry.from_yaml(config_path)
            # Log which providers are available vs skipped
            for p in self.provider_registry.all_providers:
                if p.is_available:
                    logger.info(f"Provider AVAILABLE: {p.name} ({p.provider_type}/{p.model_id})")
                else:
                    reason = "disabled" if not p.enabled else f"missing env {p.api_key_env}"
                    logger.warning(f"Provider SKIPPED: {p.name} — {reason}")
            chain = self.provider_registry.build_provider_chain()

            # OpenAI OAuth is now wired through the registry (provider_type: openai_oauth)
            # No manual injection needed — registry handles T1 Mini + T2 GPT 5.4

            return chain

        # Legacy fallback — hardcoded chain (backward compat)
        logger.warning("No routing config found, using legacy provider chain")
        return self._init_providers_legacy()

    def _init_providers_legacy(self) -> ProviderChain:
        """Legacy hardcoded provider chain (backward compatibility)."""
        from able.core.providers.nvidia_nim import NVIDIANIMProvider
        from able.core.providers.openrouter import OpenRouterProvider
        from able.core.providers.anthropic_provider import AnthropicProvider
        from able.core.providers.ollama import OllamaProvider

        providers = []

        nvidia_key = os.environ.get("NVIDIA_API_KEY")
        if nvidia_key:
            try:
                providers.append(NVIDIANIMProvider(api_key=nvidia_key, model="nvidia/llama-3.3-nemotron-super-49b-v1"))
                logger.info("Provider added: NVIDIA NIM (Nemotron Super) [legacy]")
            except Exception as e:
                logger.warning(f"Failed to init NVIDIA NIM provider: {e}")

        if _AUTH_AVAILABLE and self.auth_manager and self.auth_manager.is_authenticated('openai_oauth'):
            try:
                providers.append(OpenAIChatGPTProvider(
                    config=ProviderConfig(model="gpt-5.4"),
                    auth_manager=self.auth_manager
                ))
                logger.info("Provider added: OpenAI OAuth (BYOK) [legacy]")
            except Exception as e:
                logger.warning(f"Failed to init OpenAI OAuth provider: {e}")

        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                providers.append(OpenRouterProvider(
                    api_key=openrouter_key, model="qwen/qwen3.5-397b-a17b", timeout=600.0
                ))
                logger.info("Provider added: OpenRouter (Qwen 397B) [legacy]")
            except Exception as e:
                logger.warning(f"Failed to init OpenRouter provider: {e}")

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            try:
                providers.append(AnthropicProvider(api_key=anthropic_key, model="claude-opus-4-5"))
                logger.info("Provider added: Anthropic [legacy]")
            except Exception as e:
                logger.warning(f"Failed to init Anthropic provider: {e}")

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            providers.append(OllamaProvider(model="qwen3.5:27b-q3_K_M", base_url=ollama_url))
            logger.info("Provider added: Ollama (local fallback) [legacy]")
        except Exception as e:
            logger.warning(f"Failed to init Ollama provider: {e}")

        if not providers:
            logger.error("No AI providers configured — ABLE will not respond to messages!")

        self.provider_registry = None
        return ProviderChain(providers)

    def _init_vision_providers(self) -> ProviderChain:
        """Build ProviderChain specifically for multimodal inputs."""
        providers = []
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                # Primary Vision Provider: Moonshot Kimi 2.5 (OpenRouter)
                providers.append(OpenRouterProvider(
                    api_key=openrouter_key,
                    model="moonshotai/moonshot-v1-auto", # Also known as kimi-k2.5 equivalent
                    timeout=600.0
                ))
                logger.info("Provider added: OpenRouter Vision (Kimi)")
            except Exception as e:
                logger.warning(f"Failed to init OpenRouter vision provider: {e}")
                
        # Create chain
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

    @property
    def web_search(self):
        """Lazy-load WebSearch on first access (defers ~180ms aiohttp import)."""
        if self._web_search is None:
            from able.tools.search.web_search import WebSearch as _WS
            self._web_search = _WS()
        return self._web_search

    async def process_message(
        self,
        message: Union[str, list],
        user_id: str,
        client_id: Optional[str] = None,
        metadata: Dict = None,
        update: Optional[Update] = None,
    ) -> str:
        """
        Main message processing pipeline:
        Input → Scanner → Auditor → Trust Gate → AI (ProviderChain) → Tool dispatch
        """

        import time as _time
        _pipeline_start = _time.monotonic()

        # Extract textual content from multimodal list for security scanning
        text_content = message
        if isinstance(message, list):
            text_content = next((item.get("text", "") for item in message if item.get("type") == "text"), "")

        # Input length guard — prevent DoS via oversized messages
        _text_len = len(text_content) if isinstance(text_content, str) else 0
        if _text_len > MAX_MESSAGE_LENGTH:
            logger.warning(f"[PIPELINE] Message rejected: {_text_len:,} chars (limit {MAX_MESSAGE_LENGTH:,})")
            return f"⚠️ Message too long ({_text_len:,} chars). Maximum is {MAX_MESSAGE_LENGTH:,} characters."

        # Per-client rate limiting (burst + sustained)
        _rl_client = client_id or user_id
        _rl = await self.rate_limiter.check_message_limit(_rl_client)
        if not _rl.allowed:
            logger.warning(f"[PIPELINE] Rate limited: client={_rl_client} type={_rl.limit_type} retry_after={_rl.retry_after:.1f}s")
            return f"⚠️ Rate limit exceeded ({_rl.limit_type}). Try again in {_rl.retry_after:.0f}s."

        _channel = self._resolve_channel(update, metadata)
        _msg_preview = (text_content[:80] + "...") if isinstance(text_content, str) and len(text_content) > 80 else text_content
        logger.info(f"[PIPELINE] ── START ── user={user_id} client={client_id} msg={_msg_preview!r}")

        # ── Implicit correction detection ──────────────────────────────────────
        # When a user immediately corrects or rephrases, mark the previous turn
        # as negative feedback so the DPO pipeline can use it as a rejected sample.
        _CORRECTION_PREFIXES = (
            "no,", "no.", "no ", "nope", "wrong", "incorrect", "that's wrong",
            "actually,", "actually ", "wait,", "wait ", "redo", "fix this",
            "that's not", "that isn't", "you missed", "you got that wrong",
            "not what i", "not what I", "try again", "do it again",
            "you're wrong", "youre wrong", "you're incorrect",
        )
        if isinstance(text_content, str) and self.interaction_logger:
            _lowered = text_content.strip().lower()
            if any(_lowered.startswith(p) for p in _CORRECTION_PREFIXES):
                try:
                    _prev = self.interaction_logger.get_latest_for_session(user_id)
                    if _prev and not _prev.get("correction_detected"):
                        self.interaction_logger.mark_correction_detected(_prev["id"])
                        logger.info("[RLHF] Implicit correction detected — marked prev interaction %s as negative", _prev["id"])
                except Exception:
                    pass

        # Collect pipeline steps for dashboard
        _pipeline_steps = []

        # Step 1: Scanner (read-only analysis)
        _t0 = _time.monotonic()
        scan_result = await self.scanner.process(text_content, metadata or {})
        _scan_ms = (_time.monotonic() - _t0) * 1000
        logger.info(f"[PIPELINE] Step 1 — Scanner: passed={scan_result['security_verdict']['passed']} ({_scan_ms:.0f}ms)")
        _pipeline_steps.append({"step": "scanner", "passed": scan_result['security_verdict']['passed'], "ms": round(_scan_ms)})

        if not scan_result["security_verdict"]["passed"]:
            return f"⚠️ Security check failed: {scan_result['blocked_reason']}"

        # Step 2: Auditor (validation)
        _t0 = _time.monotonic()
        audit_result = await self.auditor.process(scan_result)
        _audit_ms = (_time.monotonic() - _t0) * 1000
        logger.info(f"[PIPELINE] Step 2 — Auditor: approved={audit_result['approved_for_executor']} ({_audit_ms:.0f}ms)")
        _pipeline_steps.append({"step": "auditor", "approved": audit_result['approved_for_executor'], "ms": round(_audit_ms)})

        if not audit_result["approved_for_executor"]:
            return f"⚠️ Audit failed: {'; '.join(audit_result['notes'])}"

        # Step 2.5: Prompt Enrichment — expand flavor words into domain-specific criteria
        enrichment_result = None
        enriched_text = text_content
        if self.prompt_enricher and isinstance(text_content, str):
            try:
                _t0 = _time.monotonic()
                # Build lightweight memory context from cached data (no DB query per request)
                _memory_ctx = None
                if hasattr(self, 'memory') and self.memory and hasattr(self, '_enricher_memory_cache'):
                    _memory_ctx = self._enricher_memory_cache
                enrichment_result = self.prompt_enricher.enrich(text_content, memory_context=_memory_ctx)
                _enrich_ms = (_time.monotonic() - _t0) * 1000
                if enrichment_result.enrichment_level != "none":
                    enriched_text = enrichment_result.enriched
                    logger.info(
                        f"[PIPELINE] Step 2.5 — Enricher: domain={enrichment_result.domain} "
                        f"level={enrichment_result.enrichment_level} "
                        f"words={enrichment_result.flavor_words_found} ({_enrich_ms:.0f}ms)"
                    )
                    _pipeline_steps.append({
                        "step": "enricher", "domain": enrichment_result.domain,
                        "level": enrichment_result.enrichment_level,
                        "flavor_words": enrichment_result.flavor_words_found,
                        "ms": round(_enrich_ms)
                    })
                else:
                    logger.debug(f"[PIPELINE] Step 2.5 — Enricher: skipped ({enrichment_result.skip_reason})")
            except Exception as e:
                logger.warning(f"[PIPELINE] Prompt enrichment failed: {e}")

        # Step 3: AI response via complexity-scored routing
        if not self.provider_chain.providers:
            return "⚠️ No AI providers configured. Set NVIDIA_API_KEY, OPENROUTER_API_KEY, or ANTHROPIC_API_KEY."

        # ── Score complexity and select tier ──────────────────────
        # Use enriched text for scoring — enrichment adds specificity that affects routing
        scoring_result = None
        selected_chain = self.provider_chain  # default fallback
        if self.complexity_scorer and isinstance(enriched_text, str):
            try:
                _t0 = _time.monotonic()
                scoring_result = self.complexity_scorer.score_and_route(enriched_text)
                tier = scoring_result.selected_tier
                _score_ms = (_time.monotonic() - _t0) * 1000
                if tier in self.tier_chains and self.tier_chains[tier].providers:
                    selected_chain = self.tier_chains[tier]
                _chain_names = [p.name for p in selected_chain.providers[:4]]
                logger.info(
                    f"[PIPELINE] Step 3 — Routing: score={scoring_result.score:.3f} "
                    f"tier={tier} domain={scoring_result.domain} "
                    f"chain={_chain_names} ({_score_ms:.0f}ms)"
                )
                _pipeline_steps.append({
                    "step": "routing", "score": round(scoring_result.score, 3),
                    "tier": tier, "domain": scoring_result.domain,
                    "chain": _chain_names, "ms": round(_score_ms)
                })
            except Exception as e:
                logger.warning(f"[PIPELINE] Complexity scoring failed, using default chain: {e}")

        # ── A+6: Advisor fallback routing for borderline complexity ──
        # When the T4 subscription provider (CLI) is unavailable, redirect
        # 0.5-0.7 complexity requests to the advisor-enhanced Sonnet provider.
        # This saves ~80% vs full Opus API for borderline tasks.
        if (scoring_result and
                hasattr(self, 'provider_registry') and self.provider_registry):
            _thresholds = getattr(self.complexity_scorer, 'weights', {}).get("tier_thresholds", {})
            _adv_min = _thresholds.get("tier_advisor_min_score", 0.5)
            _adv_max = _thresholds.get("tier_advisor_max_score", 0.7)
            if _adv_min <= scoring_result.score <= _adv_max:
                _all_t4 = self.provider_registry._by_tier.get(4, [])
                _subscription_available = any(
                    p.provider_type in ("claude_code", "openai_oauth") and p.is_available
                    for p in _all_t4
                )
                if not _subscription_available:
                    _has_advisor = any(
                        (p.extra or {}).get("advisor_enabled") and p.is_available
                        for p in _all_t4
                    )
                    if _has_advisor and 4 in self.tier_chains and self.tier_chains[4].providers:
                        selected_chain = self.tier_chains[4]
                        scoring_result.selected_tier = 4
                        logger.info(
                            "[PIPELINE] A+6 advisor fallback: score=%.3f redirected to "
                            "advisor-enhanced T4 (subscription unavailable)",
                            scoring_result.score,
                        )

        # Step 3.5: Deep enrichment — model-assisted refinement for high-complexity prompts
        # Triggers when complexity > 0.7 and rule-based enrichment was applied
        if (scoring_result and scoring_result.score > 0.7
                and enrichment_result and enrichment_result.enrichment_level != "none"):
            try:
                async def _nano_call(system: str, user: str) -> str:
                    """Quick model call via the T4 chain for deep enrichment."""
                    from able.core.providers.base import Message, Role
                    _msgs = [Message(role=Role.SYSTEM, content=system),
                             Message(role=Role.USER, content=user)]
                    # Use T2 chain (cheap but capable) for enrichment refinement
                    _chain = self.tier_chains.get(2, selected_chain)
                    _result = await _chain.chat(_msgs)
                    return _result.content if _result else ""

                _t0 = _time.monotonic()
                enrichment_result = await DeepEnricher.refine(enrichment_result, _nano_call)
                enriched_text = enrichment_result.enriched
                _deep_ms = (_time.monotonic() - _t0) * 1000
                logger.info(f"[PIPELINE] Step 3.5 — Deep enrichment: refined via model ({_deep_ms:.0f}ms)")
                _pipeline_steps.append({"step": "deep_enrichment", "ms": round(_deep_ms)})
            except Exception as e:
                logger.debug(f"[PIPELINE] Deep enrichment skipped: {e}")

        # ── Pre-log interaction record (fill result after completion) ──
        interaction_id = None
        if self.interaction_logger and scoring_result:
            import json as _json
            try:
                record = InteractionRecord(
                    message_preview=text_content[:200] if isinstance(text_content, str) else "",
                    complexity_score=scoring_result.score,
                    selected_tier=scoring_result.selected_tier,
                    selected_provider=scoring_result.selected_provider or (
                        selected_chain.providers[0].name if selected_chain.providers else ""
                    ),
                    domain=scoring_result.domain,
                    features=_json.dumps(scoring_result.features),
                    scorer_version=scoring_result.scorer_version,
                    budget_gated=scoring_result.budget_gated,
                    channel=_channel,
                    session_id=user_id,
                )
                interaction_id = self.interaction_logger.log(record)
                # Push routing decision to SSE subscribers
                asyncio.ensure_future(self._push_event("routing_decision", {
                    "tier": record.selected_tier,
                    "provider": record.selected_provider,
                    "domain": record.domain,
                    "score": round(record.complexity_score, 3),
                    "channel": record.channel,
                }))
            except Exception as e:
                logger.warning(f"Interaction logging failed: {e}")

        try:
            # ── Dashboard-driven tool authorization ──
            _t0 = _time.monotonic()
            authorized_tools = await fetch_authorized_tools(self.tool_registry, client_id)
            _auth_ms = (_time.monotonic() - _t0) * 1000
            logger.info(f"[PIPELINE] Step 4 — Tool auth: {len(authorized_tools)} tools authorized ({_auth_ms:.0f}ms)")
            _pipeline_steps.append({"step": "tool_auth", "tools": len(authorized_tools), "ms": round(_auth_ms)})

            active_system_prompt = ABLE_SYSTEM_PROMPT

            # Inject memory context if available
            if hasattr(self, 'memory') and self.memory:
                try:
                    _t0 = _time.monotonic()
                    recalled_context = self.memory.get_context_for_agent(objective=text_content, client_id=client_id)
                    _mem_ms = (_time.monotonic() - _t0) * 1000
                    if recalled_context:
                        active_system_prompt += f"\n\n## Recalled Context from Hybrid Memory\n{recalled_context}"
                        logger.info(f"[PIPELINE] Step 5 — Memory recall: {len(recalled_context)} chars injected ({_mem_ms:.0f}ms)")
                        _pipeline_steps.append({"step": "memory", "chars": len(recalled_context), "ms": round(_mem_ms)})
                    else:
                        logger.info(f"[PIPELINE] Step 5 — Memory recall: no relevant context ({_mem_ms:.0f}ms)")
                        _pipeline_steps.append({"step": "memory", "chars": 0, "ms": round(_mem_ms)})
                except Exception as e:
                    logger.warning(f"[PIPELINE] Memory recall failed: {e}")

            # Scratchpad context — inject prior agent findings into system prompt
            try:
                _sp_block = self.scratchpad.get_context_block()
                if _sp_block:
                    active_system_prompt += f"\n\n{_sp_block}"
                    logger.info("[PIPELINE] Scratchpad: %d chars injected", len(_sp_block))
            except Exception:
                pass  # Non-critical

            msgs = [Message(role=Role.SYSTEM, content=active_system_prompt)]

            # Inject Persistent Memory Context
            target_id = client_id or "master"
            history = self.transcript_manager.get_recent_messages(target_id, limit=20)
            # History comes back newest-first, flip to chronological
            history.reverse()
            
            for log in history:
                # Skip the current message we just logged into the database to avoid duplication
                if log.get("direction") == "inbound" and log.get("message") == message:
                    continue
                    
                log_msg = log.get("message")
                # Only feed text history (skip base64 images to preserve context window length)
                if isinstance(log_msg, str):
                    role = Role.USER if log.get("direction") == "inbound" else Role.ASSISTANT
                    msgs.append(Message(role=role, content=log_msg))

            # Use enriched text for the model (preserves original intent + adds criteria)
            final_user_message = enriched_text if isinstance(enriched_text, str) else message
            msgs.append(Message(role=Role.USER, content=final_user_message))

            _history_count = len(msgs) - 1  # minus system prompt
            _enriched_tag = f" [enriched: {enrichment_result.enrichment_level}]" if enrichment_result and enrichment_result.enrichment_level != "none" else ""
            logger.info(f"[PIPELINE] Step 6 — AI call: {_history_count} history msgs, {len(authorized_tools)} tools{_enriched_tag}")

            _used_tools = False
            _execution_monitor = ExecutionMonitor()

            # ── T5 cloud advisor escalation state (Plan A+5) ──────────
            # For tier-5 (local/Ollama) models, track stuck signals and
            # escalate to a cloud advisor when the model can't make progress.
            _t5_state = None
            if scoring_result and scoring_result.selected_tier == 5:
                from able.core.gateway.t5_advisor import T5AdvisorState
                _t5_state = T5AdvisorState()
                logger.info("[PIPELINE] T5 advisor escalation enabled (max %d calls)", 2)

            # ── Activity-based timeout tracking (Hermes v0.8 PR #5389) ──
            # Instead of a hard 15-iteration cap, track actual activity.
            # Active agents (tool call <30s ago) get extended budget.
            # Idle agents (>60s no tools) get pressure earlier.
            _last_activity_ts = _time.monotonic()
            _last_activity_desc = "initial"
            _MAX_ITERATIONS = 20  # Extended from 15 — activity tracking prevents runaways
            _IDLE_THRESHOLD_S = 60.0  # Inject pressure after 60s idle

            # Resolve context limit and advisor config for the selected provider tier
            _context_limit = 128000  # safe default
            _advisor_enabled = False
            _advisor_cfg = {}
            if hasattr(self, 'provider_registry') and self.provider_registry and scoring_result:
                _tier_cfg = self.provider_registry.get_primary_for_tier(scoring_result.selected_tier)
                if _tier_cfg:
                    _context_limit = _tier_cfg.max_context
                    _advisor_cfg = _tier_cfg.extra or {}
                    _advisor_enabled = _advisor_cfg.get("advisor_enabled", False)
                    # A+6: advisor_fallback_only guard — skip advisor injection
                    # when a subscription provider is active (no cost benefit).
                    if _advisor_enabled and _advisor_cfg.get("advisor_fallback_only", False):
                        if _tier_cfg.provider_type in ("claude_code", "openai_oauth"):
                            _advisor_enabled = False
                            logger.debug(
                                "[PIPELINE] Advisor skipped — subscription provider active"
                            )

            # ── Advisor strategy injection (Plan A+3) ─────────────
            # When the selected provider has advisor_enabled=True, inject
            # the advisor_20260301 server-side tool so Sonnet can escalate
            # to Opus within a single API call.
            _advisor_injected = False
            if _advisor_enabled and authorized_tools is not None:
                try:
                    from able.core.providers.anthropic_provider import AnthropicProvider
                    _adv_max = _advisor_cfg.get("advisor_max_uses", 3)
                    _adv_model = _advisor_cfg.get("advisor_model")
                    _adv_tool = AnthropicProvider.advisor_tool(
                        max_uses=_adv_max,
                        advisor_model=_adv_model,
                    )
                    authorized_tools = list(authorized_tools) + [_adv_tool]
                    _advisor_injected = True
                    logger.info(
                        "[PIPELINE] Advisor tool injected: model=%s, max_uses=%d",
                        _adv_tool.get("advisor_model", "?"), _adv_max,
                    )
                except Exception as _adv_err:
                    logger.warning("[PIPELINE] Advisor tool injection failed: %s", _adv_err)

            self.context_compactor.reset_compression_counter()
            self.context_compactor._last_compaction_event = None  # Clear stale events

            for loop_iteration in range(_MAX_ITERATIONS):
                # ── Context compaction check (Phase 0b) ──────────────
                # Before each LLM call, check if messages are approaching
                # the context limit. Compact if needed to prevent overflow.
                _msgs_as_dicts = [
                    {"role": m.role.value if hasattr(m.role, 'value') else str(m.role),
                     "content": m.content}
                    for m in msgs
                ]
                if self.context_compactor.needs_compaction(_msgs_as_dicts, _context_limit):
                    _pre_count = len(msgs)
                    _compacted_dicts = self.context_compactor.compact_if_needed(
                        _msgs_as_dicts, _context_limit
                    )
                    if len(_compacted_dicts) < _pre_count:
                        # Rebuild msgs from compacted dicts
                        _new_msgs = []
                        for d in _compacted_dicts:
                            _role_str = d.get("role", "system")
                            try:
                                _role = Role(_role_str)
                            except ValueError:
                                _role = Role.SYSTEM
                            _new_msgs.append(Message(role=_role, content=d["content"]))
                        msgs = _new_msgs
                        logger.info(
                            "[PIPELINE] Context compacted: %d → %d messages (iter %d, limit %d)",
                            _pre_count, len(msgs), loop_iteration, _context_limit,
                        )

                _iter_start = _time.monotonic()
                # ── Trace span for provider call ──
                _span = None
                if self.tracer:
                    _span = self.tracer.start_span(
                        name=f"provider.complete.iter{loop_iteration}",
                        kind="llm",
                        attributes={
                            "tier": scoring_result.selected_tier if scoring_result else 0,
                            "domain": scoring_result.domain if scoring_result else "unknown",
                            "complexity_score": scoring_result.score if scoring_result else 0,
                            "tenant_id": client_id or "master",
                            "iteration": loop_iteration,
                        },
                    )
                # Route to a vision-capable provider if message is multimodal (only needed for first pass)
                try:
                    if isinstance(message, list) and loop_iteration == 0:
                        result = await self.vision_chain.complete(
                            msgs,
                            tools=authorized_tools,
                            max_tokens=4096,
                            temperature=0.60
                        )
                    else:
                        # Sanitize msgs for the text-only provider chain by converting multimodal lists into pure text strings
                        text_only_msgs = []
                        for msg in msgs:
                            if isinstance(msg.content, list):
                                extracted_text = next((item.get("text", "") for item in msg.content if item.get("type") == "text"), "")
                                text_only_msgs.append(Message(
                                    role=msg.role,
                                    content=extracted_text,
                                    name=msg.name,
                                    tool_call_id=msg.tool_call_id,
                                    tool_calls=msg.tool_calls
                                ))
                            else:
                                text_only_msgs.append(msg)

                        result = await selected_chain.complete(
                            text_only_msgs,
                            tools=authorized_tools,
                            max_tokens=16384,
                            temperature=0.60,
                            top_p=0.95,
                        )
                except Exception as _provider_err:
                    # ── 413 / context-length auto-compact + retry (Phase 0b) ──
                    if ContextCompactor.is_context_length_error(_provider_err):
                        logger.warning(
                            "[PIPELINE] Context-length error detected (%s), "
                            "attempting compaction and retry",
                            type(_provider_err).__name__,
                        )
                        _msgs_dicts = [
                            {"role": m.role.value if hasattr(m.role, 'value') else str(m.role),
                             "content": m.content}
                            for m in msgs
                        ]
                        _compacted = self.context_compactor.compact_if_needed(
                            _msgs_dicts, _context_limit
                        )
                        if len(_compacted) < len(msgs):
                            _new_msgs = []
                            for d in _compacted:
                                _role_str = d.get("role", "system")
                                _role = Role(_role_str) if _role_str in Role.__members__.values() else Role.SYSTEM
                                _new_msgs.append(Message(role=_role, content=d["content"]))
                            msgs = _new_msgs
                            logger.info(
                                "[PIPELINE] Post-error compaction: %d → %d msgs, retrying",
                                len(_msgs_dicts), len(msgs),
                            )
                            continue  # Retry the loop iteration with compacted context
                    raise  # Re-raise if not a context-length error or compaction failed

                _iter_ms = (_time.monotonic() - _iter_start) * 1000
                _provider_name = getattr(result, 'provider', '?')
                _model_name = getattr(result, 'model', '?')
                _tok = result.usage.total_tokens if hasattr(result, 'usage') and result.usage else 0
                _tool_names = [tc.name for tc in result.tool_calls] if result.tool_calls else []

                # ── End trace span with result metadata ──
                if _span and self.tracer:
                    _span.attributes["provider"] = _provider_name
                    _span.attributes["model"] = _model_name
                    _span.attributes["latency_ms"] = _iter_ms
                    _span.attributes["total_tokens"] = _tok
                    if hasattr(result, 'usage') and result.usage:
                        _span.attributes["input_tokens"] = getattr(result.usage, 'input_tokens', 0)
                        _span.attributes["output_tokens"] = getattr(result.usage, 'output_tokens', 0)
                    if _tool_names:
                        _span.attributes["tool_calls"] = _tool_names
                    self.tracer.end_span(_span)

                logger.info(
                    f"[PIPELINE] Iter {loop_iteration} — provider={_provider_name} model={_model_name} "
                    f"tokens={_tok} tools={_tool_names} ({_iter_ms:.0f}ms)"
                )

                # Step 4: Tool dispatch if AI called a tool
                if result.tool_calls:
                    _used_tools = True
                    # Log the assistant's action into the memory array
                    msgs.append(Message(
                        role=Role.ASSISTANT,
                        content=result.content or "",
                        tool_calls=result.tool_calls
                    ))
                    
                    # ── Activity-aware budget pressure (Hermes v0.8) ──────────
                    # Replace fixed iteration count with activity timer.
                    # Active agents never get killed; only truly idle ones.
                    _budget_remaining = _MAX_ITERATIONS - loop_iteration
                    _time_since_activity = _time.monotonic() - _last_activity_ts
                    _budget_pressure = ""

                    # Hard budget pressure near the ceiling
                    if loop_iteration >= _MAX_ITERATIONS - 3:
                        _budget_pressure = (
                            f"\n\n[⚠️ BUDGET: {_budget_remaining} iteration(s) remaining. "
                            f"Stop calling tools. Synthesize a final answer NOW.]"
                        )
                    # Idle pressure — no tool activity for 60+ seconds
                    elif _time_since_activity > _IDLE_THRESHOLD_S and loop_iteration >= 8:
                        _budget_pressure = (
                            f"\n\n[⚠️ No productive tool activity for {_time_since_activity:.0f}s. "
                            f"Synthesize your answer from available data.]"
                        )

                    # ── Concurrent tool execution (Plan E1, Hermes v0.3) ────
                    # When multiple tool calls arrive in one turn, run
                    # independent ones in parallel via asyncio.gather().
                    # Phase 1: classify each call as blocked or executable.
                    _blocked_calls = []  # (tool_call, output) — pre-blocked
                    _executable_calls = []  # tool_call — ready to run

                    for tool_call in result.tool_calls:
                        _tc_args_current = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                        _recent_same = [
                            r for r in _execution_monitor.history[-2:]
                            if r.name == tool_call.name
                            and _args_fingerprint(r.args) == _args_fingerprint(_tc_args_current)
                        ]
                        if len(_recent_same) >= 2:
                            _block_msg = (
                                f"[BLOCKED] Tool `{tool_call.name}` called with identical "
                                f"arguments 3 times. Use a different approach or synthesize "
                                f"from existing results."
                            )
                            logger.warning(
                                "[PIPELINE] Blocked repeated tool call: %s (same args 3x)",
                                tool_call.name,
                            )
                            _execution_monitor.record(
                                tool_name=tool_call.name,
                                tool_args=_tc_args_current,
                                tool_output=_block_msg,
                                iteration=loop_iteration,
                                success=False,
                            )
                            _blocked_calls.append((tool_call, _block_msg))
                        else:
                            _executable_calls.append(tool_call)

                    # Phase 2: execute tools — parallel if multiple, sequential if one.
                    _tool_results = {}  # tool_call.id -> output string
                    for tc, out in _blocked_calls:
                        _tool_results[tc.id] = out

                    if len(_executable_calls) >= 2:
                        # Parallel execution via asyncio.gather
                        async def _run_tool(tc):
                            return tc, await self._handle_tool_call(tc, update, user_id, msgs)
                        _gathered = await asyncio.gather(
                            *[_run_tool(tc) for tc in _executable_calls],
                            return_exceptions=True,
                        )
                        for item in _gathered:
                            if isinstance(item, Exception):
                                logger.warning("[PIPELINE] Parallel tool exception: %s", item)
                                continue
                            tc, output = item
                            _tool_results[tc.id] = str(output)
                        logger.info(
                            "[PIPELINE] Concurrent tool execution: %d tools in parallel",
                            len(_executable_calls),
                        )
                    else:
                        # Single tool — run directly (no gather overhead)
                        for tc in _executable_calls:
                            output = await self._handle_tool_call(tc, update, user_id, msgs)
                            _tool_results[tc.id] = str(output)

                    _last_activity_ts = _time.monotonic()
                    if _executable_calls:
                        _last_activity_desc = _executable_calls[-1].name

                    # Phase 3: record, persist, and assemble messages in order.
                    for tool_call in result.tool_calls:
                        tool_output = _tool_results.get(tool_call.id, "⚠️ Tool execution failed")
                        _tc_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

                        # Record for execution monitor
                        if tool_call.id not in {tc.id for tc, _ in _blocked_calls}:
                            _tc_output_str = tool_output.lower()
                            _tc_success = not (
                                _tc_output_str.startswith("error")
                                or _tc_output_str.startswith("⚠️ tool error")
                                or _tc_output_str.startswith("❌")
                                or "failed:" in _tc_output_str[:100]
                            )
                            _execution_monitor.record(
                                tool_name=tool_call.name,
                                tool_args=_tc_args,
                                tool_output=tool_output,
                                iteration=loop_iteration,
                                success=_tc_success,
                            )
                            # T5 advisor: track tool success/failure
                            if _t5_state is not None:
                                _t5_state.record_tool_result(_tc_success)

                        # Notify Telegram
                        if update and update.message:
                            try:
                                tool_notification = f"⚙️ [{tool_call.name}]\n{tool_output}"
                                if len(tool_notification) > 4000:
                                    tool_notification = tool_notification[:4000] + "\n... (truncated)"
                                await update.message.reply_text(tool_notification)
                            except Exception:
                                pass

                        # Tool result persistence (Hermes PR #5210)
                        _tool_content = tool_output
                        _tool_content, _was_persisted = _maybe_persist(
                            tool_call.name, tool_call.id, _tool_content,
                        )
                        if _was_persisted:
                            logger.info(
                                "[PIPELINE] Tool output persisted to disk: %s (%d chars)",
                                tool_call.name, len(tool_output),
                            )

                        # Scratchpad: cache file reads for cross-agent reuse
                        if tool_call.name in ("read_file", "Read") and _tc_success:
                            _read_path = _tc_args.get("file_path", _tc_args.get("path", ""))
                            if _read_path and len(tool_output) > 200:
                                try:
                                    self.scratchpad.put_file_summary(
                                        _read_path,
                                        tool_output[:500],
                                        source_agent="gateway",
                                    )
                                except Exception:
                                    pass  # Non-critical

                        # Inject tool observation into prompt
                        _wrapped_tool_content = (
                            f"[TOOL OUTPUT — {tool_call.name}]\n"
                            f"{_tool_content}\n"
                            f"[END TOOL OUTPUT]"
                            f"{_budget_pressure}"
                        )
                        msgs.append(Message(
                            role=Role.TOOL,
                            content=_wrapped_tool_content,
                            name=tool_call.name,
                            tool_call_id=tool_call.id
                        ))

                    # ── Execution monitor analysis (PentAGI-inspired) ──────────
                    # Detects spinning, thrashing, output repetition, error loops
                    # More targeted than generic budget pressure — analyzes progress
                    _monitor_verdict = _execution_monitor.analyze(
                        original_task=text_content if isinstance(text_content, str) else ""
                    )
                    if _monitor_verdict.should_intervene:
                        logger.warning(
                            f"[PIPELINE] ExecutionMonitor: pattern={_monitor_verdict.pattern} "
                            f"confidence={_monitor_verdict.confidence:.2f} — {_monitor_verdict.details}"
                        )
                        # Inject verdict into the last tool output msg (same pattern as budget_pressure)
                        if msgs and hasattr(msgs[-1], 'role') and msgs[-1].role == Role.TOOL:
                            _last = msgs[-1]
                            msgs[-1] = Message(
                                role=_last.role,
                                content=_last.content + _monitor_verdict.message,
                                name=_last.name,
                                tool_call_id=_last.tool_call_id,
                            )
                    if _monitor_verdict.should_terminate:
                        logger.warning("[PIPELINE] ExecutionMonitor: TERMINATING tool loop — unproductive pattern detected")
                        # Synthesize a response from the last available content instead of
                        # falling through to the generic "exceeded iterations" error message.
                        _total_ms = (_time.monotonic() - _pipeline_start) * 1000
                        _monitor_summary = _execution_monitor.get_summary()
                        logger.info(
                            f"[PIPELINE] ── MONITOR STOP ── iterations={loop_iteration + 1} "
                            f"pattern={_monitor_verdict.pattern} total={_total_ms:.0f}ms"
                        )
                        # Use the last model response if available, else explain termination
                        _last_content = result.content if result and result.content else ""
                        if _last_content:
                            return _last_content
                        return (
                            f"I wasn't able to complete this task — my tool calls were "
                            f"{_monitor_verdict.pattern} ({_monitor_verdict.details}). "
                            f"Could you rephrase or break this into smaller steps?"
                        )

                    # ── T5 cloud advisor escalation (Plan A+5) ────────────
                    # If the local model is stuck (3+ tool failures or 2+ empty
                    # outputs), ask a cloud advisor for guidance.
                    if _t5_state is not None and _t5_state.is_stuck():
                        from able.core.gateway.t5_advisor import maybe_escalate_to_advisor
                        _adv_guidance = await maybe_escalate_to_advisor(
                            _t5_state,
                            text_content if isinstance(text_content, str) else "",
                            msgs,
                            self.tier_chains,
                            self.provider_chain,
                        )
                        if _adv_guidance:
                            msgs.append(Message(
                                role=Role.SYSTEM,
                                content=f"[ADVISOR] {_adv_guidance}",
                            ))

                    continue

                _total_ms = (_time.monotonic() - _pipeline_start) * 1000
                # Preserve raw output for distillation BEFORE stripping
                _raw_output_for_log = result.content
                # Strip thinking tokens (<think>, "Thinking:") from model output
                # thinking_content is preserved on the CompletionResult for distillation
                result.strip_thinking()
                _has_thinking = result.has_thinking
                _thinking_content = result.thinking_content or ""
                final_text = result.content or ""

                # ── Thinking-only prefill continuation (Hermes PR #5931) ──
                # If the model produced thinking content but no user-facing
                # text, append thinking as assistant prefill and continue the
                # loop so the model sees its own reasoning and generates text.
                if not final_text.strip() and _has_thinking and _thinking_content:
                    if not hasattr(self, '_thinking_prefill_retries'):
                        self._thinking_prefill_retries = 0
                    if self._thinking_prefill_retries < 2:
                        self._thinking_prefill_retries += 1
                        logger.info(
                            "[PIPELINE] Thinking-only response (retry %d/2) — "
                            "appending as prefill for continuation",
                            self._thinking_prefill_retries,
                        )
                        msgs.append(Message(
                            role=Role.ASSISTANT,
                            content=f"[Internal reasoning]\n{_thinking_content}\n[/Internal reasoning]",
                        ))
                        continue  # Re-run the loop — model sees its reasoning

                if not final_text.strip():
                    # ── T5 advisor: empty output escalation (Plan A+5) ────
                    if _t5_state is not None:
                        _t5_state.record_empty_output()
                        if _t5_state.is_stuck():
                            from able.core.gateway.t5_advisor import maybe_escalate_to_advisor
                            _adv_guidance = await maybe_escalate_to_advisor(
                                _t5_state,
                                text_content if isinstance(text_content, str) else "",
                                msgs,
                                self.tier_chains,
                                self.provider_chain,
                            )
                            if _adv_guidance:
                                msgs.append(Message(
                                    role=Role.SYSTEM,
                                    content=f"[ADVISOR] {_adv_guidance}",
                                ))
                                continue  # Re-run with advisor guidance
                    final_text = "⚠️ ABLE exceeded the maximum internal thinking steps (15 turns)."
                else:
                    # Successful text output — reset T5 state
                    if _t5_state is not None:
                        _t5_state.record_text_output()
                # Reset prefill counter on successful text output
                self._thinking_prefill_retries = 0

                # ── Capture reasoning trace for ALL channels (not just CLI) ──────────
                # CLI captures via _StreamThinkFilter in stream_message.
                # process_message (Telegram, Discord, API) must capture here.
                if _has_thinking and _thinking_content and isinstance(text_content, str):
                    import hashlib as _hl
                    _msg_hash = _hl.sha256(text_content.encode()).hexdigest()[:16]
                    _log_reasoning_gateway(
                        thinking=_thinking_content,
                        session=user_id,
                        message_hash=_msg_hash,
                        message=text_content,
                        response=final_text,
                        model=result.model or "",
                        elapsed_s=_total_ms / 1000,
                        domain=scoring_result.domain if scoring_result else "",
                        provider=result.provider if hasattr(result, 'provider') else _channel,
                    )
                logger.info(
                    f"[PIPELINE] ── DONE ── provider={_provider_name} iterations={loop_iteration + 1} "
                    f"total={_total_ms:.0f}ms"
                )
                # ── Run quality evaluators (feeds corpus builder + dashboard) ──
                _quality_scores = None
                if self.evaluator and final_text and isinstance(text_content, str):
                    try:
                        _quality_scores = self.evaluator.score_for_training(
                            input_text=text_content,
                            output_text=final_text,
                        )
                        if _quality_scores.get("eligible"):
                            logger.debug(
                                "[PIPELINE] Quality: %.2f (eligible for corpus)",
                                _quality_scores["average"],
                            )
                    except Exception as _eval_e:
                        logger.debug(f"Evaluator scoring failed (non-fatal): {_eval_e}")

                # Save to HybridMemory
                if hasattr(self, 'memory') and self.memory:
                    try:
                        self.memory.store(content=message if isinstance(message, str) else str(message), memory_type=MemoryType.CONVERSATION, client_id=client_id)
                        self.memory.store(content=final_text, memory_type=MemoryType.CONVERSATION, client_id=client_id)
                    except Exception as e:
                        logger.error(f"Failed to store memory: {e}")

                # ── Post audit log to dashboard ──
                try:
                    import uuid as _uuid
                    _run_id = str(_uuid.uuid4())
                    _tool_calls_log = []
                    for m in msgs:
                        if hasattr(m, 'tool_calls') and m.tool_calls:
                            for tc in m.tool_calls:
                                _tool_calls_log.append({
                                    "name": tc.name,
                                    "args": tc.arguments if isinstance(tc.arguments, dict) else {},
                                })
                    _usage = result.usage if hasattr(result, 'usage') and result.usage else None
                    asyncio.create_task(post_audit_log({
                        "run_id": _run_id,
                        "agent_role": "executor",
                        "task": str(text_content)[:500],
                        "content": str(final_text)[:2000],
                        "tool_calls": _tool_calls_log,
                        "provider_used": getattr(result, 'provider', None),
                        "model_used": getattr(result, 'model', None),
                        "input_tokens": _usage.input_tokens if _usage else 0,
                        "output_tokens": _usage.output_tokens if _usage else 0,
                        "duration_ms": getattr(result, 'latency_ms', 0),
                        "pipeline_total_ms": round(_total_ms),
                        "pipeline_steps": _pipeline_steps,
                        "complexity_score": scoring_result.score if scoring_result else None,
                        "selected_tier": scoring_result.selected_tier if scoring_result else None,
                        "domain": scoring_result.domain if scoring_result else None,
                        "severity": "info",
                        "status": "completed",
                        "org_id": client_id,
                        "iterations": loop_iteration + 1,
                    }))
                except Exception as audit_e:
                    logger.warning(f"Failed to post audit log: {audit_e}")

                # ── Update interaction log with result ──
                if self.interaction_logger and interaction_id:
                    try:
                        _usage = result.usage if hasattr(result, 'usage') else None
                        _raw_input = text_content if isinstance(text_content, str) else str(text_content)
                        # Confidence proxy — real logprobs for Ollama, calibrated proxy otherwise.
                        # Must be computed before update_result (uses raw_input/output/thinking).
                        _response_confidence: Optional[float] = None
                        try:
                            from able.core.distillation.confidence_scorer import score_response_confidence
                            _raw_input_for_conf = _raw_input if isinstance(_raw_input, str) else ""
                            _conf_row = {
                                "actual_provider": result.provider if hasattr(result, "provider") else "",
                                "raw_input": _raw_input_for_conf[:5000],
                                "raw_output": (_raw_output_for_log or "")[:5000],
                                "thinking_content": _thinking_content[:3000] if _thinking_content else None,
                                "complexity_score": scoring_result.score if scoring_result else 0.3,
                                "guidance_needed": None,   # not yet known at response time
                                "audit_score": None,       # not yet scored
                            }
                            _response_confidence = score_response_confidence(_conf_row)
                        except Exception:
                            pass
                        # Real executed tools — derived from gateway's execution loop (msgs),
                        # NOT from the model's declared tool_calls. Claude and other models
                        # can emit synthetic tool signals that never actually run; only
                        # _tool_calls_log contains tools that physically executed.
                        import json as _json2
                        _real_tool_names = [t["name"] for t in _tool_calls_log] if _tool_calls_log else []
                        _tools_called_json = _json2.dumps(_real_tool_names) if _real_tool_names else None
                        # conversation_depth = number of prior assistant turns in this session
                        _conv_depth = sum(
                            1 for m in history if m.get("direction") == "outbound"
                        ) if history else 0
                        self.interaction_logger.update_result(
                            interaction_id,
                            actual_provider=result.provider if hasattr(result, 'provider') else "",
                            fallback_used=(
                                hasattr(result, 'provider') and scoring_result and
                                result.provider != (scoring_result.selected_provider or "")
                            ),
                            latency_ms=result.latency_ms if hasattr(result, 'latency_ms') else 0,
                            input_tokens=_usage.input_tokens if _usage else 0,
                            output_tokens=_usage.output_tokens if _usage else 0,
                            cost_usd=result.cost if hasattr(result, 'cost') else 0,
                            success=True,
                            # Distillation fields — preserve full output for training
                            raw_input=_raw_input[:10000],
                            raw_output=_raw_output_for_log[:10000] if _raw_output_for_log else None,
                            thinking_tokens_preserved=_has_thinking,
                            thinking_content=_thinking_content[:8000] if _thinking_content else None,
                            corpus_eligible=_quality_scores.get("eligible", True) if _quality_scores else True,
                            quality_score=_quality_scores.get("average", 0.0) if _quality_scores else None,
                            # Conversation chain fields (real signals, not synthetic)
                            tools_called=_tools_called_json,
                            conversation_depth=_conv_depth,
                            # Response confidence: real logprobs for Ollama, proxy for others
                            response_confidence=_response_confidence,
                            # Advisor strategy tracking (Plan A+4)
                            advisor_input_tokens=(
                                result.advisor_usage.get("input_tokens", 0)
                                if hasattr(result, 'advisor_usage') and result.advisor_usage else None
                            ),
                            advisor_output_tokens=(
                                result.advisor_usage.get("output_tokens", 0)
                                if hasattr(result, 'advisor_usage') and result.advisor_usage else None
                            ),
                            advisor_calls=(
                                result.advisor_usage.get("calls", 0)
                                if hasattr(result, 'advisor_usage') and result.advisor_usage else None
                            ),
                            # Compression telemetry — read from compactor's last event
                            **({
                                "compression_attempted": True,
                                "compression_ratio": _ce["ratio"],
                                "tokens_before_compression": _ce["tokens_before"],
                                "tokens_after_compression": _ce["tokens_after"],
                                "compression_mode": self._detect_compression_mode(
                                    result.content[:1000] if result.content else ""
                                ),
                            } if (_ce := getattr(self.context_compactor, '_last_compaction_event', None)) else {}),
                        )
                    except Exception as log_e:
                        logger.warning(f"Failed to update interaction log: {log_e}")

                # ── Update session state ──
                if self.session_mgr:
                    try:
                        self.session_mgr.update(
                            conversation_id=user_id,
                            complexity_score=scoring_result.score if scoring_result else None,
                            input_tokens=_usage.input_tokens if _usage else 0,
                            output_tokens=_usage.output_tokens if _usage else 0,
                            cost_usd=result.cost if hasattr(result, 'cost') and result.cost else 0.0,
                            metadata_patch={"channel": _channel, "client_id": client_id} if loop_iteration == 0 else None,
                        )
                    except Exception as sess_e:
                        logger.warning(f"Session update failed: {sess_e}")

                # ── Append model identifier tag ──
                _raw_model = result.model if hasattr(result, 'model') and result.model else ""
                _short = MODEL_SHORT_NAMES.get(_raw_model, _raw_model or (result.provider if hasattr(result, 'provider') else ""))
                _tier_label = f"T{scoring_result.selected_tier}" if scoring_result else ""
                _model_tag = f"\n\n`⚡ {_short} [{_tier_label}]`" if _short else ""
                final_text += _model_tag

                # ── Buddy XP + needs (system-wide, all channels) ──
                try:
                    from able.core.buddy.xp import award_interaction_xp
                    from able.core.buddy.nudge import format_buddy_footer
                    _complexity = scoring_result.score if scoring_result else 0.5
                    _domain = scoring_result.domain if scoring_result else "default"
                    _xp_result = award_interaction_xp(
                        complexity_score=_complexity,
                        used_tools=_used_tools,
                        domain=_domain,
                        selected_tier=scoring_result.selected_tier if scoring_result else None,
                    )
                    if _xp_result:
                        # Append buddy status to every response
                        _footer = format_buddy_footer(_xp_result)
                        if _footer:
                            final_text += _footer
                        # Push buddy XP event to SSE subscribers
                        asyncio.ensure_future(self._push_event("buddy_xp", {
                            "name": _xp_result["buddy_name"],
                            "level": _xp_result["level"],
                            "xp": _xp_result["xp"],
                            "mood": _xp_result["mood"],
                        }))
                except Exception:
                    pass  # Buddy is optional — never block the pipeline

                # ── Drain background completion queue (Hermes PR #5779) ──
                # Check if any cron jobs finished while we were processing.
                # Append notifications so the user knows about background work.
                _bg_notes = self._drain_completion_queue()
                if _bg_notes:
                    final_text = final_text + "\n\n" + _bg_notes

                return final_text

            return f"⚠️ Agent exceeded maximum tool iterations ({_MAX_ITERATIONS})."

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"AI completion failed: {e}\n{tb}")
            # Log failure to interaction log
            if self.interaction_logger and interaction_id:
                try:
                    self.interaction_logger.update_result(
                        interaction_id,
                        success=False,
                        error_type=type(e).__name__,
                    )
                except Exception:
                    pass
            if update and update.message:
                try:
                    import json
                    import io
                    # Send full traceback as a document so we can diagnose
                    trace_text = f"Exception: {type(e).__name__}: {e}\n\n{tb}"
                    trace_bytes = io.BytesIO(trace_text.encode('utf-8'))
                    trace_bytes.name = "error_trace.txt"
                    await update.message.reply_document(document=trace_bytes, caption=f"⚠️ {type(e).__name__}: {str(e)[:500]}")
                except Exception as dump_e:
                    pass
            return f"⚠️ AI error ({type(e).__name__}): {e}"

    async def stream_message(
        self,
        message: str,
        user_id: str,
        client_id: Optional[str] = None,
        metadata: Dict = None,
    ) -> AsyncIterator[str]:
        """
        Streaming variant of process_message for CLI/interactive use.

        Runs the full pipeline (scanner → auditor → enricher → scorer).
        Supports multi-turn tool dispatch: intermediate tool iterations use
        complete() and yield progress notifications; the final text response
        is streamed token-by-token.

        Yields string chunks as they arrive from the provider.
        """
        import time as _time
        _pipeline_start = _time.monotonic()

        # Input length guard
        _text_len = len(message) if isinstance(message, str) else 0
        if _text_len > MAX_MESSAGE_LENGTH:
            yield f"⚠️ Message too long ({_text_len:,} chars). Maximum is {MAX_MESSAGE_LENGTH:,} characters."
            return

        # Per-client rate limiting
        _rl_client = client_id or user_id
        _rl = await self.rate_limiter.check_message_limit(_rl_client)
        if not _rl.allowed:
            yield f"⚠️ Rate limit exceeded ({_rl.limit_type}). Try again in {_rl.retry_after:.0f}s."
            return

        _channel = (metadata or {}).get("channel", "cli")

        # Step 1: Scanner
        scan_result = await self.scanner.process(message, metadata or {})
        if not scan_result["security_verdict"]["passed"]:
            yield f"⚠️ Security check failed: {scan_result['blocked_reason']}"
            return

        # Step 2: Auditor
        audit_result = await self.auditor.process(scan_result)
        if not audit_result["approved_for_executor"]:
            yield f"⚠️ Audit failed: {'; '.join(audit_result['notes'])}"
            return

        # Step 2.5: Enrichment — use sanitized content as base, not raw input
        _sanitized = scan_result.get("sanitized_content") or message
        enriched_text = _sanitized
        enrichment_result = None
        if self.prompt_enricher:
            try:
                _memory_ctx = None
                if hasattr(self, 'memory') and self.memory and hasattr(self, '_enricher_memory_cache'):
                    _memory_ctx = self._enricher_memory_cache
                enrichment_result = self.prompt_enricher.enrich(_sanitized, memory_context=_memory_ctx)
                if enrichment_result.enrichment_level != "none":
                    enriched_text = enrichment_result.enriched
            except Exception:
                pass

        # Step 3: Score & route
        scoring_result = None
        selected_chain = self.provider_chain
        if self.complexity_scorer:
            try:
                scoring_result = self.complexity_scorer.score_and_route(enriched_text)
                tier = scoring_result.selected_tier
                if tier in self.tier_chains and self.tier_chains[tier].providers:
                    selected_chain = self.tier_chains[tier]
            except Exception:
                pass

        # Pre-log interaction
        interaction_id = None
        if self.interaction_logger and scoring_result:
            import json as _json
            try:
                record = InteractionRecord(
                    message_preview=message[:200],
                    complexity_score=scoring_result.score,
                    selected_tier=scoring_result.selected_tier,
                    selected_provider=scoring_result.selected_provider or (
                        selected_chain.providers[0].name if selected_chain.providers else ""
                    ),
                    domain=scoring_result.domain,
                    features=_json.dumps(scoring_result.features),
                    scorer_version=scoring_result.scorer_version,
                    budget_gated=scoring_result.budget_gated,
                    channel=_channel,
                    session_id=user_id,
                )
                interaction_id = self.interaction_logger.log(record)
            except Exception:
                pass

        # Build message array
        active_system_prompt = ABLE_SYSTEM_PROMPT
        if hasattr(self, 'memory') and self.memory:
            try:
                recalled_context = self.memory.get_context_for_agent(
                    objective=message, client_id=client_id
                )
                if recalled_context:
                    active_system_prompt += f"\n\n## Recalled Context from Hybrid Memory\n{recalled_context}"
            except Exception:
                pass

        msgs = [Message(role=Role.SYSTEM, content=active_system_prompt)]

        target_id = client_id or "master"
        history = self.transcript_manager.get_recent_messages(target_id, limit=20)
        history.reverse()
        for log in history:
            if log.get("direction") == "inbound" and log.get("message") == message:
                continue
            log_msg = log.get("message")
            if isinstance(log_msg, str):
                role = Role.USER if log.get("direction") == "inbound" else Role.ASSISTANT
                msgs.append(Message(role=role, content=log_msg))

        msgs.append(Message(role=Role.USER, content=enriched_text))

        # ── Tool dispatch + streaming ─────────────────────────────────
        # Strategy: if tools are available, use complete() for tool dispatch
        # iterations (yields progress notifications), then stream() for the
        # final text response. If no tools, stream directly (original path).
        _actual_provider = selected_chain.providers[0].name if selected_chain.providers else ""
        _MAX_STREAM_TOOL_ITERS = 10
        _used_tools = False

        # Resolve authorized tools for stream tool dispatch
        _stream_tools = []
        try:
            if hasattr(self, 'tool_registry') and self.tool_registry:
                _stream_tools = await fetch_authorized_tools(self.tool_registry, client_id)
        except Exception:
            pass

        # ── Tool dispatch loop (only when tools are available) ─────────
        if _stream_tools:
            _complete_result = None
            for _tool_iter in range(_MAX_STREAM_TOOL_ITERS):
                try:
                    _complete_result = await selected_chain.complete(
                        msgs, tools=_stream_tools,
                        max_tokens=16384, temperature=0.60, top_p=0.95,
                    )
                except Exception as e:
                    yield f"⚠️ AI error: {e}"
                    return

                if not _complete_result.tool_calls:
                    break  # No tool calls — fall through to stream

                _used_tools = True
                msgs.append(Message(
                    role=Role.ASSISTANT,
                    content=_complete_result.content or "",
                    tool_calls=_complete_result.tool_calls,
                ))

                for tool_call in _complete_result.tool_calls:
                    tool_output = await self._handle_tool_call(tool_call, None, user_id, msgs)

                    _tool_preview = str(tool_output)[:200]
                    yield f"\n⚙️ [{tool_call.name}] {_tool_preview}\n"

                    _tool_content = str(tool_output)
                    _tool_content, _ = _maybe_persist(
                        tool_call.name, tool_call.id, _tool_content,
                    )
                    msgs.append(Message(
                        role=Role.TOOL,
                        content=f"[TOOL OUTPUT — {tool_call.name}]\n{_tool_content}\n[END TOOL OUTPUT]",
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                    ))
            else:
                # Exhausted tool iterations — yield last result
                yield _complete_result.content or "⚠️ Tool dispatch exceeded maximum iterations."
                full_response = _complete_result.content or ""
                _total_ms = (_time.monotonic() - _pipeline_start) * 1000
                if self.interaction_logger and interaction_id:
                    try:
                        self.interaction_logger.update_result(
                            interaction_id, actual_provider=_actual_provider,
                            success=True, latency_ms=_total_ms,
                            raw_input=message[:10000], raw_output=full_response[:10000],
                        )
                    except Exception:
                        pass
                return

        # ── Stream the final text response ─────────────────────────────
        accumulated = []
        yielded_any = False
        _think_filter = _StreamThinkFilter()
        try:
            async for chunk in selected_chain.stream(
                msgs, max_tokens=16384, temperature=0.60
            ):
                yielded_any = True
                accumulated.append(chunk)
                visible = _think_filter.consume(chunk)
                if visible:
                    yield visible
        except Exception as e:
            if yielded_any:
                logger.warning(
                    "Streaming interrupted after partial output; preserving streamed chunks: %s", e,
                )
            else:
                logger.warning(f"Streaming failed before first chunk, falling back to complete(): {e}")
                try:
                    result = await selected_chain.complete(
                        msgs, max_tokens=16384, temperature=0.60, top_p=0.95
                    )
                    content = result.content or "⚠️ No response generated."
                    yield content
                    accumulated = [content]
                except Exception as e2:
                    yield f"⚠️ AI error: {e2}"
                    return

        # Flush probe buffer
        flushed = _think_filter.flush()
        if flushed:
            yield flushed

        full_response = "".join(accumulated)

        # Audit: write captured reasoning to log
        if _think_filter.captured:
            _domain_for_log = scoring_result.domain if scoring_result else "default"
            _log_reasoning_gateway(
                _think_filter.captured,
                user_id=user_id,
                message=message[:300],
                response=full_response[:300],
                domain=_domain_for_log,
                provider=_actual_provider,
            )
            if _think_filter._eval_mode:
                logger.warning(
                    "[SECURITY] Eval-mode bleed detected from provider=%s — response suppressed, "
                    "captured %d chars of reasoning",
                    _actual_provider, len(_think_filter.captured),
                )

        # Post-processing: interaction log update
        _total_ms = (_time.monotonic() - _pipeline_start) * 1000
        if self.interaction_logger and interaction_id:
            try:
                self.interaction_logger.update_result(
                    interaction_id,
                    actual_provider=_actual_provider,
                    success=True,
                    latency_ms=_total_ms,
                    raw_input=message[:10000],
                    raw_output=full_response[:10000],
                )
            except Exception:
                pass

        # Session tracking
        if self.session_mgr:
            try:
                self.session_mgr.update(
                    user_id,
                    complexity=scoring_result.score if scoring_result else 0.5,
                    tokens=len(full_response.split()) * 2,
                    cost_usd=0.0,
                )
            except Exception:
                pass

        # Buddy XP (system-wide)
        try:
            from able.core.buddy.xp import award_interaction_xp
            from able.core.buddy.nudge import format_buddy_footer
            _complexity = scoring_result.score if scoring_result else 0.5
            _domain = scoring_result.domain if scoring_result else "default"
            _xp_result = award_interaction_xp(
                complexity_score=_complexity,
                used_tools=False,
                domain=_domain,
                selected_tier=scoring_result.selected_tier if scoring_result else None,
            )
            if _xp_result:
                _footer = format_buddy_footer(_xp_result)
                if _footer:
                    yield _footer
                asyncio.ensure_future(self._push_event("buddy_xp", {
                    "name": _xp_result["buddy_name"],
                    "level": _xp_result["level"],
                    "xp": _xp_result["xp"],
                    "mood": _xp_result["mood"],
                }))
        except Exception:
            pass

    @staticmethod
    def _resolve_channel(update: Optional[Update], metadata: Optional[Dict]) -> str:
        """Normalize channel labels for telemetry across Telegram, API, and local CLI."""
        if metadata:
            explicit = metadata.get("channel")
            if explicit:
                return str(explicit)
            source = str(metadata.get("source", "")).lower()
            if source.startswith("cli"):
                return "cli"
        return "telegram" if update else "api"

    def _drain_completion_queue(self) -> str:
        """
        Drain the cron scheduler's completion queue and format notifications.

        Called at the end of each process_message turn. Returns a formatted
        string of background job completions, or empty string if none.
        """
        if not hasattr(self, 'scheduler') or not self.scheduler:
            return ""

        notifications = []
        while not self.scheduler.completion_queue.empty():
            try:
                completion = self.scheduler.completion_queue.get_nowait()
                job_name = completion.get("job_name", "unknown")
                status = completion.get("status", "unknown")
                duration = completion.get("duration_s", 0)
                summary = completion.get("summary", "")

                note = f"[BACKGROUND] {job_name} {status} ({duration}s)"
                if summary:
                    note += f": {summary[:100]}"
                notifications.append(note)
            except Exception:
                break

        return "\n".join(notifications)

    @staticmethod
    def _detect_compression_mode(text: str) -> str:
        """Detect ultramode compression style from response text sample."""
        if not text:
            return ""
        import re
        # Wenyan-ultra: specific bridge chars (因→bc 及→& 或→| 至→→ 從→← 約→~)
        # NOT generic CJK — that false-positives on any Chinese text
        _wenyan = len(re.findall(r'[因及或至從約]', text[:3000]))
        _cu = len(re.findall(r'→|←|\b(?:ur|b4|bc|btwn|thru|w/o?|#s)\b', text[:3000]))
        _tech = len(re.findall(
            r'\b(?:DB|auth|mw|EP|param|comp|tmpl|conn|txn|sched|ctr|infra|k8s|i18n'
            r'|impl|fn|srv|dep|pkg|msg|err|req|res)\b', text[:3000]
        ))
        has_wenyan = _wenyan >= 2
        has_caveman = _cu >= 3 or _tech >= 3
        if has_wenyan and has_caveman:
            return "ultramode"
        if has_wenyan:
            return "wenyan-ultra"
        if has_caveman:
            return "caveman-ultra"
        return ""

    async def _handle_tool_call(self, tool_call, update: Optional[Update], user_id: str, msgs: List["Message"] = None) -> str:
        """Dispatch a tool call from the AI to the correct client, with approval for writes."""
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        logger.info(f"Tool call: {name}({args})")

        # Short-circuit if parser injected an error (e.g., from truncated JSON args)
        if "error" in args and "JSONDecodeError" in str(args["error"]):
            return f"⚠️ System Error: The tool parameter JSON was truncated or malformed: {args['error']}. If you are trying to output massive code files, do not push them all at once. Break them down."

        try:
            tool_context = ToolContext(
                user_id=user_id,
                client_id=getattr(update, "effective_chat", None).id if update and getattr(update, "effective_chat", None) else "api",
                update=update,
                msgs=msgs or [],
                approval_workflow=self.approval_workflow,
                metadata={
                    "github": self.github,
                    "do_client": self.do_client,
                    "vercel": self.vercel,
                    "web_search": self.web_search,  # property — lazy-inits on first access
                    "resource_plane": self.resource_plane,
                },
            )
            registry_result = await self.tool_registry.dispatch(tool_call, tool_context)
            if not registry_result.startswith("❓ Unknown tool:"):
                return registry_result

            # ── Read-only tools (no approval) ─────────────────────────────────

            if name == "github_list_repos":
                repos = await self.github.list_repos()
                if not repos:
                    return "No repositories found."
                lines = [
                    f"• [{r['name']}]({r['html_url']}) — {'🔒 private' if r['private'] else '🌐 public'}"
                    for r in repos[:20]
                ]
                return "**Your repositories:**\n" + "\n".join(lines)

            if name == "do_list_droplets":
                droplets = await self.do_client.list_droplets()
                if not droplets:
                    return "No droplets found on this account."
                lines = []
                for d in droplets:
                    networks = d.get("networks", {}).get("v4", [])
                    ip = next((n["ip_address"] for n in networks if n.get("type") == "public"), "no-ip")
                    lines.append(f"• **{d['name']}** — {ip} ({d['region']['slug']}, {d['size_slug']}, {d['status']})")
                return "**Droplets:**\n" + "\n".join(lines)

            # ── Write tools (require approval) ────────────────────────────────

            if name == "github_create_repo":
                approval = await self.approval_workflow.request_approval(
                    operation="github_create_repo",
                    details=args,
                    requester_id=user_id,
                    risk_level="medium",
                    context=f"Create {'private' if args.get('private') else 'public'} repo: {args.get('name')}",
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"
                result = await self.github.create_repo(
                    name=args["name"],
                    description=args.get("description", ""),
                    private=args.get("private", False),
                )
                return f"✅ Repo created: {result['html_url']}"

            if name == "github_push_files":
                # Handle <EXTRACT> bypass for massive code blocks
                if msgs:
                    for path, content in args.get("files", {}).items():
                        if content == "<EXTRACT>":
                            # Scan back through msgs
                            import re
                            for m in reversed(msgs):
                                if m.role in (Role.ASSISTANT, Role.USER) and m.content:
                                    # Find all markdown blocks
                                    blocks = re.findall(r'```(?:\w+)?\n(.*?)```', m.content, re.DOTALL)
                                    if blocks:
                                        args["files"][path] = blocks[-1]
                                        break
                                        
                approval = await self.approval_workflow.request_approval(
                    operation="github_push_files",
                    details=args,
                    requester_id=user_id,
                    risk_level="medium",
                    context=f"Push {len(args.get('files', {}))} files to {args.get('repo')}/{args.get('branch', 'main')}",
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"
                await self.github.push_files(
                    repo=args["repo"],
                    files_dict=args["files"],
                    message=args.get("message", "chore: update via ABLE"),
                    branch=args.get("branch", "main"),
                )
                return f"✅ Pushed {len(args.get('files', {}))} files to `{args['repo']}`"

            if name == "github_create_pr":
                approval = await self.approval_workflow.request_approval(
                    operation="github_create_pr",
                    details=args,
                    requester_id=user_id,
                    risk_level="low",
                    context=f"Open PR '{args.get('title')}' in {args.get('repo')}: {args.get('head')} → {args.get('base', 'main')}",
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"
                result = await self.github.create_pr(
                    repo=args["repo"],
                    title=args["title"],
                    body=args.get("body", ""),
                    head=args["head"],
                    base=args.get("base", "main"),
                )
                return f"✅ PR opened: {result['html_url']}"

            if name == "github_pages_deploy":
                repo = args["repo"]
                files_dict = args["files"]
                message = args.get("commit_message", "deploy: update GitHub Pages via ABLE")
                pages_url = f"https://{self.github.owner}.github.io/{repo}/"
                file_list = ", ".join(list(files_dict.keys())[:5])

                approval = await self.approval_workflow.request_approval(
                    operation="github_pages_deploy",
                    details={"repo": repo, "files": list(files_dict.keys()), "live_url": pages_url},
                    requester_id=user_id,
                    risk_level="low",
                    context=f"Deploy {len(files_dict)} files to {pages_url}\nFiles: {file_list}",
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"

                # Push to gh-pages branch
                try:
                    await self.github.push_files(repo, files_dict, message, branch="gh-pages")
                except Exception:
                    try:
                        await self.github.create_branch(repo, "gh-pages", from_branch="main")
                        await self.github.push_files(repo, files_dict, message, branch="gh-pages")
                    except Exception:
                        for path, content in files_dict.items():
                            await self.github.push_file(repo, path, content, message, branch="gh-pages")

                try:
                    await self.github.enable_github_pages(repo, branch="gh-pages")
                except Exception:
                    pass  # May already be enabled

                return (
                    f"✅ Deployed to GitHub Pages!\n\n"
                    f"🌐 URL: {pages_url}\n"
                    f"📁 Files: {len(files_dict)} pushed to `gh-pages`\n"
                    f"⏱ Live in ~60 seconds"
                )

            if name == "vercel_deploy":
                project_name = args["project_name"]
                files_dict = args["files"]
                env_vars = args.get("env_vars")
                file_list = ", ".join(list(files_dict.keys())[:5])

                approval = await self.approval_workflow.request_approval(
                    operation="vercel_deploy",
                    details={"project": project_name, "files": list(files_dict.keys())},
                    requester_id=user_id,
                    risk_level="low",
                    context=f"Deploy {len(files_dict)} files to Vercel '{project_name}'\nFiles: {file_list}",
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"

                result = await self.vercel.create_deployment(project_name, files_dict, env_vars)
                state = result.get("readyState", "UNKNOWN")
                url = result.get("url", "")
                if state == "READY":
                    live_url = f"https://{url}" if url and not url.startswith("http") else url
                    return f"✅ Deployed to Vercel!\n\n🌐 {live_url}\n📦 {project_name}"
                elif state == "ERROR":
                    return f"❌ Vercel failed: {result.get('errorMessage', 'Unknown error')}"
                else:
                    return f"⏳ Deploying... state: {state}\nCheck vercel.com/dashboard"

            if name == "do_create_droplet":
                d_name = args["name"]
                region = args.get("region", "nyc3")
                size = args.get("size", "s-1vcpu-1gb")
                image = args.get("image", "ubuntu-24-04-x64")
                ssh_key_ids = args.get("ssh_key_ids", [])
                size_costs = {"s-1vcpu-1gb": "$6/mo", "s-2vcpu-2gb": "$12/mo", "s-4vcpu-8gb": "$48/mo"}
                cost = size_costs.get(size, "variable")

                approval = await self.approval_workflow.request_approval(
                    operation="do_create_droplet",
                    details={"name": d_name, "region": region, "size": size, "image": image, "cost": cost},
                    requester_id=user_id,
                    risk_level="high",
                    context=f"Provision DO droplet\nName: {d_name} | {region} | {size} | {image}\nCost: {cost} (billed immediately)",
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"

                droplet = await self.do_client.create_droplet(
                    name=d_name, region=region, size=size, image=image, ssh_key_ids=ssh_key_ids,
                )
                networks = droplet.get("networks", {}).get("v4", [])
                public_ip = next((n["ip_address"] for n in networks if n.get("type") == "public"), "pending")
                status = droplet.get("status", "unknown")

                if status == "active":
                    return (
                        f"✅ Droplet ready!\n\n"
                        f"**{d_name}**\n"
                        f"IP: `{public_ip}`\n"
                        f"Region: {region} | {size} | {cost}\n\n"
                        f"SSH: `ssh root@{public_ip}`"
                    )
                return f"⏳ Droplet created (status: {status})\nID: {droplet.get('id')}"

            # ── Tenant + distillation tools ────────────────────────────────
            if name == "tenant_onboard":
                approval = await self.approval_workflow.request_approval(
                    operation="tenant_onboard",
                    details=args,
                    requester_id=user_id,
                    risk_level="medium",
                    context=f"Onboard tenant '{args.get('name')}' ({args.get('tenant_id')}) — domain: {args.get('domain')}",
                )
                if approval.status.value != "approved":
                    return f"❌ Denied ({approval.status.value})"
                from able.core.gateway.tool_defs.tenant_tools import handle_tenant_onboard
                return await handle_tenant_onboard(**args)

            if name == "tenant_list":
                from able.core.gateway.tool_defs.tenant_tools import handle_tenant_list
                return await handle_tenant_list(**args)

            if name == "tenant_status":
                from able.core.gateway.tool_defs.tenant_tools import handle_tenant_status
                return await handle_tenant_status(**args)

            if name == "distillation_status":
                from able.core.gateway.tool_defs.tenant_tools import handle_distillation_status
                return await handle_distillation_status(**args)

            if name == "distillation_harvest":
                from able.core.gateway.tool_defs.tenant_tools import handle_distillation_harvest
                return await handle_distillation_harvest(**args)

            if name == "distillation_build_corpus":
                from able.core.gateway.tool_defs.tenant_tools import handle_distillation_build_corpus
                return await handle_distillation_build_corpus(**args)

            return f"❓ Unknown tool: {name}"

        except Exception as e:
            logger.error(f"Tool call {name} failed: {e}", exc_info=True)
            self._record_tool_failure(name, str(e))
            return f"⚠️ Tool error ({name}): {e}"

    def _record_tool_failure(self, tool_name: str, error: str):
        """Track tool failures for self-healing. Alert if a tool keeps failing."""
        import time
        now = time.time()
        if tool_name not in self._tool_failures:
            self._tool_failures[tool_name] = []
        self._tool_failures[tool_name].append(now)
        # Prune old failures outside window
        cutoff = now - self._tool_failure_window
        self._tool_failures[tool_name] = [
            t for t in self._tool_failures[tool_name] if t > cutoff
        ]
        recent_count = len(self._tool_failures[tool_name])
        if recent_count >= self._tool_failure_threshold:
            logger.error(
                f"[SELF-HEAL] Tool '{tool_name}' failed {recent_count}x in "
                f"{self._tool_failure_window}s — needs investigation. "
                f"Last error: {error[:200]}"
            )
            # Log diagnostic to self-improvement engine
            if hasattr(self, 'self_improvement') and self.self_improvement:
                try:
                    asyncio.create_task(self.self_improvement.record_failure(
                        description=f"Tool '{tool_name}' failing repeatedly",
                        what_failed=f"{tool_name} failed {recent_count}x in {self._tool_failure_window}s",
                        root_cause=error[:500],
                        prevention=f"Investigate {tool_name} dependencies, check API keys/rate limits",
                    ))
                except Exception:
                    pass

    async def _handle_master_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages to master bot"""
        user_id = str(update.effective_user.id)
        
        # Check for media content
        message_text = update.message.text or update.message.caption or ""

        # Handle voice messages — transcribe to text
        if update.message.voice or update.message.audio:
            transcriber = self._get_voice_transcriber()
            if transcriber:
                try:
                    voice_file = await (update.message.voice or update.message.audio).get_file()
                    voice_bytes = await voice_file.download_as_bytearray()
                    result = await transcriber.transcribe(bytes(voice_bytes), filename="voice.ogg")
                    message_text = result.text
                    await update.message.reply_text(f"🎙️ *Transcribed:* {result.text}", parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Voice transcription failed: {e}")
                    await update.message.reply_text("⚠️ Couldn't transcribe voice message")
                    return
            else:
                await update.message.reply_text("⚠️ Voice transcription not available")
                return

        message = message_text

        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            base64_image = base64.b64encode(photo_bytes).decode('utf-8')

            message = [
                {"type": "text", "text": message_text or "Describe this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]

        # Handle video messages — extract thumbnail and send to vision chain
        if update.message.video or update.message.video_note:
            try:
                video = update.message.video or update.message.video_note
                # Use the thumbnail (small JPEG) to avoid downloading full video
                if video.thumbnail:
                    thumb_file = await video.thumbnail.get_file()
                    thumb_bytes = await thumb_file.download_as_bytearray()
                    b64_thumb = base64.b64encode(bytes(thumb_bytes)).decode('utf-8')
                    duration = getattr(video, 'duration', 0)
                    caption = message_text or f"This is a frame from a {duration}s video. Describe what you see."
                    message = [
                        {"type": "text", "text": caption},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_thumb}"}},
                    ]
                else:
                    await update.message.reply_text("⚠️ Video has no thumbnail — send a screenshot instead")
                    return
            except Exception as e:
                logger.error(f"Video processing failed: {e}")
                await update.message.reply_text("⚠️ Couldn't process video")
                return

        # Handle document/file attachments (images sent as files)
        if update.message.document and not update.message.photo:
            doc = update.message.document
            mime = doc.mime_type or ""
            if mime.startswith("image/"):
                doc_file = await doc.get_file()
                doc_bytes = await doc_file.download_as_bytearray()
                b64_doc = base64.b64encode(bytes(doc_bytes)).decode('utf-8')
                message = [
                    {"type": "text", "text": message_text or "Describe this image."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_doc}"}},
                ]
            elif mime.startswith("audio/"):
                transcriber = self._get_voice_transcriber()
                if not transcriber:
                    await update.message.reply_text("⚠️ Voice transcription not available")
                    return
                try:
                    doc_file = await doc.get_file()
                    doc_bytes = await doc_file.download_as_bytearray()
                    result = await transcriber.transcribe(bytes(doc_bytes), filename=doc.file_name or "audio.wav")
                    message_text = result.text
                    message = message_text
                    await update.message.reply_text(f"🎙️ *Transcribed:* {result.text}", parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Audio document transcription failed: {e}")

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

                await self._send_telegram_chunked(update, response, user_id=user_id)
            except Exception as e:
                logger.error(f"Pipeline error: {e}", exc_info=True)
                try:
                    await update.message.reply_text(f"⚠️ Internal error: {str(e)[:200]}")
                except Exception:
                    pass

        asyncio.create_task(_run_pipeline())

    def _feedback_keyboard(self, user_id: str):
        """Build RLHF feedback inline keyboard.  Callback data: 'rlhf:<signal>:<user_id>'"""
        try:
            return InlineKeyboardMarkup([[
                InlineKeyboardButton("👍", callback_data=f"rlhf:positive:{user_id}"),
                InlineKeyboardButton("👎", callback_data=f"rlhf:negative:{user_id}"),
            ]])
        except Exception:
            return None

    async def _send_telegram_chunked(self, update: Update, text: str, user_id: str | None = None):
        """Send a response to Telegram, splitting into chunks if >4096 chars.

        When *user_id* is provided, attaches a 👍/👎 feedback keyboard to the
        last chunk so users can rate responses for RLHF training.
        """
        MAX_LEN = 4096
        _keyboard = self._feedback_keyboard(user_id) if user_id else None

        if len(text) <= MAX_LEN:
            try:
                await update.message.reply_text(
                    text, parse_mode="Markdown", reply_markup=_keyboard
                )
            except Exception:
                await update.message.reply_text(text, reply_markup=_keyboard)
            return

        # Split on paragraph boundaries first, fall back to hard split
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= MAX_LEN:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n\n", 0, MAX_LEN)
            if split_at == -1:
                split_at = remaining.rfind("\n", 0, MAX_LEN)
            if split_at == -1 or split_at < MAX_LEN // 2:
                split_at = MAX_LEN
            chunk = remaining[:split_at]
            remaining = remaining[split_at:].lstrip("\n")
            chunks.append(chunk)

        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            kb = _keyboard if is_last else None
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                await update.message.reply_text(chunk, reply_markup=kb)

    async def _handle_rlhf_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 👍/👎 feedback callbacks — store RLHF signal on the latest interaction."""
        query = update.callback_query
        await query.answer()  # acknowledge immediately (removes loading spinner)

        data = query.data or ""
        if not data.startswith("rlhf:"):
            return

        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        _, signal, session_user_id = parts

        if self.interaction_logger:
            try:
                _rec = self.interaction_logger.get_latest_for_session(session_user_id)
                if _rec:
                    self.interaction_logger.record_feedback(_rec["id"], signal=signal)
                    _ack = "Thanks! 🙌" if signal == "positive" else "Got it — I'll learn from that 📝"
                    await query.edit_message_reply_markup(reply_markup=None)
                    await query.message.reply_text(_ack)
                    logger.info("[RLHF] %s feedback recorded for interaction %s (user=%s)", signal, _rec["id"], session_user_id)
            except Exception as e:
                logger.warning("[RLHF] Feedback callback failed: %s", e)

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
                await self._send_telegram_chunked(update, response, user_id=user_id)
            except Exception as e:
                logger.error(f"Client pipeline error: {e}", exc_info=True)
                try:
                    await update.message.reply_text(f"⚠️ Internal error")
                except Exception:
                    pass

        asyncio.create_task(_run_client_pipeline())

    async def _health_handler(self, request: web.Request) -> web.Response:
        """HTTP health check endpoint for Docker/load balancers"""
        _ensure_aiohttp()  # populate module-level `web` for all control handlers
        return web.json_response({
            "status": "ok",
            "version": "2.0",
            "bots_active": len(self.client_bots) + (1 if self.master_bot else 0),
            "providers": len(self.provider_chain.providers),
            "tool_count": self.tool_registry.tool_count,
            "control_plane": "enabled",
            "cron_enabled": getattr(self, "cron_enabled", False),
            "cron_jobs": len(getattr(getattr(self, "scheduler", None), "jobs", {})),
            "telegram_polling_enabled": getattr(self, "telegram_polling_enabled", False),
        })

    def _verify_service_token(self, request: web.Request) -> bool:
        """Allow control endpoints to share the same service-token contract as Studio/webhooks."""
        if not ABLE_SERVICE_TOKEN:
            return True
        direct = request.headers.get("x-able-service-token", "")
        if direct and hmac.compare_digest(direct, ABLE_SERVICE_TOKEN):
            return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return hmac.compare_digest(auth[7:], ABLE_SERVICE_TOKEN)
        return False

    def _unauthorized_response(self) -> web.Response:
        return web.json_response({"error": "unauthorized"}, status=401)

    async def _control_tools_catalog_handler(self, request: web.Request) -> web.Response:
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        org_id = request.query.get("org_id")
        overrides = await fetch_tool_settings(org_id)
        effective = self.tool_registry.get_effective_settings(overrides)
        catalog = []
        for row in self.tool_registry.get_catalog():
            merged = dict(row)
            merged.update(effective.get(row["name"], {}))
            catalog.append(merged)
        return web.json_response(
            {
                "organization_id": org_id or "global",
                "catalog": catalog,
                "definitions": self.tool_registry.get_definitions(effective),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _control_resources_handler(self, request: web.Request) -> web.Response:
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        return web.json_response(
            {
                "resources": self.resource_plane.list_resources(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _control_resource_detail_handler(self, request: web.Request) -> web.Response:
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        resource_id = unquote(request.match_info["resource_id"])
        resource = self.resource_plane.get_resource(resource_id)
        if not resource:
            return web.json_response({"error": "resource_not_found"}, status=404)
        return web.json_response(resource)

    async def _control_resource_action_handler(self, request: web.Request) -> web.Response:
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        resource_id = unquote(request.match_info["resource_id"])
        body = await request.json()
        action = body.get("action")
        parameters = body.get("parameters")
        approved_by = body.get("approved_by") or request.headers.get("x-able-approved-by")
        if not action:
            return web.json_response({"error": "action_required"}, status=400)
        result = self.resource_plane.perform_action(
            resource_id,
            action,
            parameters=parameters,
            approved_by=approved_by,
            service_token_verified=True,
        )
        status_code = {
            "approval_required": 202,
            "unauthorized": 403,
        }.get(result.get("status", ""), 200)
        return web.json_response(result, status=status_code)

    async def _control_collections_handler(self, request: web.Request) -> web.Response:
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        return web.json_response(
            {
                "collections": self.resource_plane.list_collections(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _control_setup_wizard_handler(self, request: web.Request) -> web.Response:
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        return web.json_response(self.resource_plane.get_setup_wizard())

    async def _research_report_handler(self, request: web.Request) -> web.Response:
        """Serve latest research report JSON — accessible locally via curl localhost:8080/api/reports/research/latest"""
        import json as _json
        from pathlib import Path as _Path
        report_dirs = [
            _Path.home() / ".able" / "reports" / "research",
            _Path("data/research_reports"),
        ]
        latest_path = None
        latest_mtime = 0.0
        for rdir in report_dirs:
            candidate = rdir / "latest.json"
            if candidate.exists() and candidate.stat().st_mtime > latest_mtime:
                latest_path = candidate
                latest_mtime = candidate.stat().st_mtime
            if rdir.exists():
                for f in rdir.glob("research_*.json"):
                    if f.stat().st_mtime > latest_mtime:
                        latest_path = f
                        latest_mtime = f.stat().st_mtime
        if latest_path is None:
            return web.json_response({"error": "No research report found"}, status=404)
        try:
            data = _json.loads(latest_path.read_text())
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _research_report_md_handler(self, request: web.Request) -> web.Response:
        """Serve latest research report as Markdown."""
        from pathlib import Path as _Path
        report_dirs = [
            _Path.home() / ".able" / "reports" / "research",
            _Path("data/research_reports"),
        ]
        latest_path = None
        latest_mtime = 0.0
        for rdir in report_dirs:
            for name in ("latest.md",):
                candidate = rdir / name
                if candidate.exists() and candidate.stat().st_mtime > latest_mtime:
                    latest_path = candidate
                    latest_mtime = candidate.stat().st_mtime
            if rdir.exists():
                for f in rdir.glob("research_*.md"):
                    if f.stat().st_mtime > latest_mtime:
                        latest_path = f
                        latest_mtime = f.stat().st_mtime
        if latest_path is None:
            return web.Response(text="No research report found", status=404, content_type="text/plain")
        try:
            return web.Response(text=latest_path.read_text(), content_type="text/markdown")
        except Exception as e:
            return web.Response(text=str(e), status=500, content_type="text/plain")

    # ── Buddy REST endpoint ───────────────────────────────────────────────────

    async def _buddy_handler(self, request: web.Request) -> web.Response:
        """GET /api/buddy — Buddy state as structured JSON for Studio dashboard."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        try:
            from able.core.buddy.model import load_buddy, save_buddy
            buddy = load_buddy()
            if buddy is None:
                return web.json_response({"buddy": None})
            buddy.apply_needs_decay()
            save_buddy(buddy)
            return web.json_response({
                "buddy": {
                    "name": buddy.name,
                    "species": buddy.species.value,
                    "level": buddy.level,
                    "xp": buddy.xp,
                    "xp_to_next": buddy.xp_to_next_level,
                    "stage": buddy.stage.value,
                    "wins": buddy.wins,
                    "losses": buddy.losses,
                    "draws": buddy.draws,
                    "hunger": round(buddy.hunger, 2),
                    "thirst": round(buddy.thirst, 2),
                    "energy": round(buddy.energy, 2),
                    "mood": buddy.mood,
                    "badges": list(buddy.badges) if buddy.badges else [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            })
        except Exception as e:
            logger.warning("buddy_handler error: %s", e)
            return web.json_response({"buddy": None, "error": "Failed to load buddy state"}, status=500)

    async def _buddy_create_handler(self, request: web.Request) -> web.Response:
        """POST /api/buddy — Create a starter buddy if none exists."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        try:
            from able.core.buddy.model import (
                Species,
                create_starter_buddy,
                load_buddy,
                save_buddy,
            )

            existing = load_buddy()
            if existing is not None:
                return web.json_response(
                    {"error": "Buddy already exists", "buddy": existing.name},
                    status=409,
                )

            body = await request.json()
            name = body.get("name", "Atlas")
            species_str = body.get("species", "root").lower()
            try:
                species = Species(species_str)
            except ValueError:
                return web.json_response(
                    {"error": f"Unknown species: {species_str}", "valid": [s.value for s in Species]},
                    status=400,
                )

            buddy = create_starter_buddy(name=name, species=species)
            save_buddy(buddy)
            logger.info("Created starter buddy: %s (%s)", name, species_str)
            return web.json_response({"created": True, "buddy": {"name": buddy.name, "species": buddy.species, "level": buddy.level}})
        except Exception as e:
            logger.warning("buddy_create_handler error: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    # ── Metrics endpoints (shared logic via metrics_queries) ──────────────────

    async def _metrics_summary_handler(self, request: web.Request) -> web.Response:
        """GET /metrics — Overall interaction summary."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        try:
            hours = int(request.query.get("hours", "24"))
        except (ValueError, TypeError):
            hours = 24
        from able.core.routing.metrics_queries import get_metrics_summary
        return web.json_response(get_metrics_summary(hours, self._interaction_db_path))

    async def _metrics_routing_handler(self, request: web.Request) -> web.Response:
        """GET /metrics/routing — Per-tier routing breakdown."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        try:
            hours = int(request.query.get("hours", "24"))
        except (ValueError, TypeError):
            hours = 24
        from able.core.routing.metrics_queries import get_routing_metrics
        return web.json_response(get_routing_metrics(hours, self._interaction_db_path))

    async def _metrics_corpus_handler(self, request: web.Request) -> web.Response:
        """GET /metrics/corpus — Distillation corpus stats."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        from able.core.routing.metrics_queries import get_corpus_metrics
        return web.json_response(get_corpus_metrics(self._interaction_db_path))

    async def _metrics_evolution_handler(self, request: web.Request) -> web.Response:
        """GET /metrics/evolution — Evolution daemon history and weights."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        try:
            hours = int(request.query.get("hours", "168"))
        except (ValueError, TypeError):
            hours = 168
        from able.core.routing.metrics_queries import get_evolution_metrics
        return web.json_response(get_evolution_metrics(hours, self._interaction_db_path))

    async def _metrics_budget_handler(self, request: web.Request) -> web.Response:
        """GET /metrics/budget — Spend vs budget caps."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        try:
            hours = int(request.query.get("hours", "24"))
        except (ValueError, TypeError):
            hours = 24
        from able.core.routing.metrics_queries import get_budget_metrics
        return web.json_response(get_budget_metrics(hours, self._interaction_db_path))

    async def _metrics_prometheus_handler(self, request: web.Request) -> web.Response:
        """GET /metrics/prometheus — Prometheus text format exposition."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        from able.core.routing.prometheus_exporter import export_prometheus
        # Pass provider health if available from last smoke test
        provider_health = None
        if hasattr(self, '_last_smoke_results'):
            provider_health = {
                n: s.get("healthy", False)
                for n, s in self._last_smoke_results.items()
            }
        text = export_prometheus(
            db_path=self._interaction_db_path,
            provider_health=provider_health,
        )
        return web.Response(
            text=text,
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # ── SSE real-time event stream ────────────────────────────────────────────

    async def _push_event(self, event_type: str, data: dict) -> None:
        """Push an event to event bus + legacy SSE subscribers."""
        # Emit through the typed event bus (new subscribers use this)
        if hasattr(self, 'event_bus'):
            await self.event_bus.emit(event_type, data, source="gateway")

        # Legacy direct SSE path (backward compat until fully migrated)
        if not self._sse_subscribers:
            return
        payload = _json.dumps({
            "type": event_type,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        dead = []
        for q in list(self._sse_subscribers):
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                self._sse_subscribers.remove(q)
            except ValueError:
                pass

    _MAX_SSE_SUBSCRIBERS = 100

    async def _events_handler(self, request: web.Request) -> web.StreamResponse:
        """GET /events — SSE stream of gateway events for Studio dashboard."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        if len(self._sse_subscribers) >= self._MAX_SSE_SUBSCRIBERS:
            return web.json_response(
                {"error": "Too many SSE subscribers"},
                status=429,
            )
        import asyncio as _asyncio
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        })
        await resp.prepare(request)

        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._sse_subscribers.append(q)

        # Send an initial connected event
        try:
            await resp.write(
                b'data: {"type":"connected","ts":"' +
                datetime.now(timezone.utc).isoformat().encode() +
                b'"}\n\n'
            )
        except Exception:
            self._sse_subscribers.remove(q)
            return resp

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    await resp.write(f"data: {payload}\n\n".encode())
                except asyncio.TimeoutError:
                    # Keepalive ping
                    await resp.write(
                        b'data: {"type":"ping","ts":"' +
                        datetime.now(timezone.utc).isoformat().encode() +
                        b'"}\n\n'
                    )
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.debug("SSE client error: %s", e)
        finally:
            try:
                self._sse_subscribers.remove(q)
            except ValueError:
                pass

        return resp

    # ── /api/chat — Studio chat routed through gateway ────────────────────────

    _MAX_CHAT_MSG_LEN = 5000

    async def _api_chat_handler(self, request: web.Request) -> web.StreamResponse:
        """POST /api/chat — Studio chat proxied through full gateway pipeline (SSE stream)."""
        if not self._verify_service_token(request):
            return self._unauthorized_response()
        try:
            body = await request.json()
        except Exception:
            return web.Response(text="Invalid JSON", status=400)

        message = body.get("message", "").strip()
        if not message:
            return web.Response(text="message required", status=400)
        if len(message) > self._MAX_CHAT_MSG_LEN:
            return web.Response(
                text=f"message exceeds {self._MAX_CHAT_MSG_LEN} chars",
                status=400,
            )

        session_id = body.get("session_id", "studio")
        channel = body.get("channel", "studio")

        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        })
        await resp.prepare(request)

        try:
            full_text = ""
            async for chunk in self.stream_message(
                message,
                session_id=session_id,
                channel=channel,
                history=[],
            ):
                full_text += chunk
                payload = _json.dumps({"type": "chunk", "text": chunk})
                await resp.write(f"data: {payload}\n\n".encode())

            # Final done event
            done_payload = _json.dumps({"type": "done", "text": full_text})
            await resp.write(f"data: {done_payload}\n\n".encode())
        except Exception as e:
            logger.error("api_chat stream error: %s", e, exc_info=True)
            err_payload = _json.dumps({"type": "error", "error": "Processing error"})
            try:
                await resp.write(f"data: {err_payload}\n\n".encode())
            except Exception:
                pass

        return resp

    # ── /ws — WebSocket streaming for Studio and API consumers ──────────────

    _WS_MAX_CONNECTIONS = int(os.environ.get("ABLE_WS_MAX_CONNECTIONS", "20"))
    _ws_active: int = 0

    async def _ws_handler(self, request):
        """WebSocket endpoint streaming stream_message() output as JSON frames.

        Inbound:  {"message": "...", "user_id": "...", "client_id": "..."}
        Outbound: {"type": "chunk", "content": "..."}
                  {"type": "done", "timing_ms": 1234}
                  {"type": "error", "message": "..."}
        """
        web = _ensure_aiohttp()

        if not self._verify_service_token(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        if self._ws_active >= self._WS_MAX_CONNECTIONS:
            return web.json_response(
                {"error": f"max {self._WS_MAX_CONNECTIONS} connections"},
                status=429,
            )

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_active += 1

        try:
            async for raw_msg in ws:
                if raw_msg.type != 1:  # aiohttp.WSMsgType.TEXT
                    continue

                try:
                    data = _json.loads(raw_msg.data)
                except (ValueError, TypeError):
                    await ws.send_json({"type": "error", "message": "invalid JSON"})
                    continue

                message = data.get("message", "").strip()
                if not message:
                    await ws.send_json({"type": "error", "message": "empty message"})
                    continue

                if len(message) > self._MAX_CHAT_MSG_LEN:
                    await ws.send_json({
                        "type": "error",
                        "message": f"exceeds {self._MAX_CHAT_MSG_LEN} chars",
                    })
                    continue

                user_id = data.get("user_id", "ws_user")
                session_id = data.get("session_id", f"ws_{user_id}")

                import time as _time
                t0 = _time.monotonic()

                try:
                    async for chunk in self.stream_message(
                        message,
                        session_id=session_id,
                        channel="websocket",
                        history=[],
                    ):
                        await ws.send_json({"type": "chunk", "content": chunk})

                    elapsed_ms = (_time.monotonic() - t0) * 1000
                    await ws.send_json({
                        "type": "done",
                        "timing_ms": round(elapsed_ms, 1),
                    })
                except Exception as stream_err:
                    logger.error("WS stream error: %s", stream_err, exc_info=True)
                    await ws.send_json({
                        "type": "error",
                        "message": "Processing error",
                    })

        except Exception:
            pass  # Client disconnected
        finally:
            self._ws_active -= 1

        return ws

    async def start_health_server(self, port: int = 8080, *, quiet: bool = False):
        """Start lightweight HTTP health check server"""
        web = _ensure_aiohttp()
        app = web.Application()
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/", self._health_handler)
        app.router.add_get("/control/tools/catalog", self._control_tools_catalog_handler)
        app.router.add_get("/control/resources", self._control_resources_handler)
        app.router.add_post("/control/resources/{resource_id}/action", self._control_resource_action_handler)
        app.router.add_get("/control/resources/{resource_id}", self._control_resource_detail_handler)
        app.router.add_get("/control/collections", self._control_collections_handler)
        app.router.add_get("/control/setup-wizard", self._control_setup_wizard_handler)
        app.router.add_get("/api/reports/research/latest", self._research_report_handler)
        app.router.add_get("/api/reports/research/latest.md", self._research_report_md_handler)
        # Buddy
        app.router.add_get("/api/buddy", self._buddy_handler)
        app.router.add_post("/api/buddy", self._buddy_create_handler)
        # Metrics
        app.router.add_get("/metrics", self._metrics_summary_handler)
        app.router.add_get("/metrics/routing", self._metrics_routing_handler)
        app.router.add_get("/metrics/corpus", self._metrics_corpus_handler)
        app.router.add_get("/metrics/evolution", self._metrics_evolution_handler)
        app.router.add_get("/metrics/budget", self._metrics_budget_handler)
        app.router.add_get("/metrics/prometheus", self._metrics_prometheus_handler)
        # SSE event stream
        app.router.add_get("/events", self._events_handler)
        # Studio chat through gateway
        app.router.add_post("/api/chat", self._api_chat_handler)
        # WebSocket streaming for Studio / API consumers
        app.router.add_get("/ws", self._ws_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        self._health_runner = runner
        if not quiet:
            print(f"✅ Health server listening on :{port}/health")

    async def aclose(self) -> None:
        """Close shared network sessions and background HTTP runners."""
        seen_provider_ids: set[int] = set()

        async def _close_provider(provider) -> None:
            close = getattr(provider, "close", None)
            if close and callable(close):
                await close()

        if self._health_runner is not None:
            try:
                await self._health_runner.cleanup()
            except Exception:
                pass
            self._health_runner = None

        for bot in self.client_bots.values():
            try:
                await bot.stop()
            except Exception:
                pass
            try:
                await bot.shutdown()
            except Exception:
                pass

        if self.master_bot is not None:
            try:
                if getattr(self.master_bot, "updater", None):
                    await self.master_bot.updater.stop()
            except Exception:
                pass
            try:
                await self.master_bot.stop()
            except Exception:
                pass
            try:
                await self.master_bot.shutdown()
            except Exception:
                pass

        chains = [self.provider_chain, getattr(self, "vision_chain", None), *getattr(self, "tier_chains", {}).values()]
        for chain in chains:
            if not chain:
                continue
            for provider in getattr(chain, "providers", []):
                provider_id = id(provider)
                if provider_id in seen_provider_ids:
                    continue
                seen_provider_ids.add(provider_id)
                try:
                    await _close_provider(provider)
                except Exception:
                    pass

        if getattr(self, "web_search", None) is not None:
            try:
                await self.web_search.close()
            except Exception:
                pass

        if getattr(self, "voice_transcriber", None) is not None:
            try:
                await self.voice_transcriber.close()
            except Exception:
                pass

        await _close_studio_session()

    async def start_master_bot(self):
        """Start the master Telegram bot"""
        _ensure_telegram()  # lazy-load telegram on first bot start
        builder = (
            Application.builder()
            .token(self.bot_token)
            .concurrent_updates(True)
            .connection_pool_size(32)
            .pool_timeout(10.0)
        )
        if _RATE_LIMITER_AVAILABLE:
            builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
        self.master_bot = builder.build()

        # Add handlers
        self.master_bot.add_handler(CommandHandler("start", self._cmd_start))
        self.master_bot.add_handler(CommandHandler("status", self._cmd_status))
        self.master_bot.add_handler(CommandHandler("clients", self._cmd_clients))
        self.master_bot.add_handler(CommandHandler("audit", self._cmd_audit))
        self.master_bot.add_handler(
            CallbackQueryHandler(self._handle_rlhf_callback, pattern=r"^rlhf:")
        )
        self.master_bot.add_handler(CallbackQueryHandler(self._handle_approval_callback))
        self.master_bot.add_handler(MessageHandler(
            (filters.TEXT | filters.VOICE | filters.AUDIO | filters.PHOTO | filters.VIDEO | filters.VIDEO_NOTE | filters.Document.ALL) & ~filters.COMMAND,
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

        client_builder = (
            Application.builder()
            .token(client.telegram_bot_token)
            .concurrent_updates(True)
            .connection_pool_size(16)
            .pool_timeout(10.0)
        )
        if _RATE_LIMITER_AVAILABLE:
            client_builder = client_builder.rate_limiter(AIORateLimiter(max_retries=3))
        app = client_builder.build()

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await self._handle_client_message(client_id, update, context)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        self.client_bots[client_id] = app

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        providers_status = f"{len(self.provider_chain.providers)} provider(s) active" if self.provider_chain.providers else "⚠️ No providers"
        await update.message.reply_text(
            f"🤖 ABLE v2 Master Bot\n"
            f"AI: {providers_status}\n\n"
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
            f"📊 ABLE v2 Status\n\n"
            f"🤖 Active client bots: {len(self.client_bots)}\n"
            f"👥 Registered clients: {client_count}\n"
            f"📋 Queue lanes: {stats['lane_count']}\n"
            f"🧠 AI providers: {', '.join(provider_names) or 'none'}\n"
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

    async def run(self):
        """Main run loop"""
        # Start health server first (so Docker health checks pass immediately)
        await self.start_health_server()

        # Start queue
        await self.queue.start()

        if self.telegram_polling_enabled:
            # Start master bot (non-fatal — health server stays up even if token is invalid)
            try:
                await self.start_master_bot()
            except Exception as e:
                print(f"⚠ Telegram bot failed to start: {e}")
                print("  Health server still running. Fix TELEGRAM_BOT_TOKEN and restart.")

            # Start all registered client bots
            for client_id in self.client_registry.clients:
                try:
                    await self.start_client_bot(client_id)
                except Exception as e:
                    print(f"Failed to start bot for {client_id}: {e}")
        else:
            logger.info("Telegram polling disabled; gateway running without getUpdates")
            print("⏸️ Telegram polling disabled (set ABLE_TELEGRAM_ENABLED=1 on one bot leader only)")

        # Start event bus + SSE bridge
        await self.event_bus.start()
        self.sse_bridge.start()

        provider_count = len(self.provider_chain.providers)
        print(f"🚀 ABLE v2 Gateway running | {provider_count} AI provider(s) active")

        # ── Startup smoke test (non-blocking, 10s timeout per provider) ──
        if hasattr(self, 'provider_registry') and self.provider_registry:
            try:
                smoke_results = await asyncio.wait_for(
                    self.provider_registry.smoke_test_providers(),
                    timeout=10.0 * max(len(self.provider_registry.available_providers), 1),
                )
                self._last_smoke_results = smoke_results  # Prometheus endpoint reads this
                healthy = [n for n, s in smoke_results.items() if s.get("healthy")]
                unhealthy = [n for n, s in smoke_results.items() if not s.get("healthy")]
                if healthy:
                    print(f"✅ Smoke test passed: {', '.join(healthy)}")
                for name in unhealthy:
                    err = smoke_results[name].get("error", "unknown")
                    logger.warning("Smoke test FAILED for %s (T%d): %s", name, smoke_results[name].get("tier", 0), err)
                    print(f"⚠️  Smoke test failed: {name} — {err}")
            except asyncio.TimeoutError:
                logger.warning("Smoke test timed out — skipping, providers may be slow")
                print("⚠️  Smoke test timed out — continuing without verification")
            except Exception as e:
                logger.warning("Smoke test error: %s — startup continues", e)
                print(f"⚠️  Smoke test error: {e} — continuing anyway")

        if self.cron_enabled:
            # Start the Persistence Layer (Proactive AGI)
            self.initiative.register_jobs(self.scheduler)

            # Register default ABLE jobs (evolution, distillation, morning report)
            async def _send_telegram(text: str):
                """Send a message to the owner via Telegram (used by morning report)."""
                if self.master_bot and self.owner_telegram_id:
                    try:
                        await self.master_bot.bot.send_message(
                            chat_id=self.owner_telegram_id,
                            text=text,
                            parse_mode="Markdown",
                        )
                    except Exception:
                        await self.master_bot.bot.send_message(
                            chat_id=self.owner_telegram_id,
                            text=text,
                        )

            # Initialize audit log for cron and system event tracking
            from able.audit.logs.audit_log import AuditLog as _CronAuditLog
            _audit_log_instance = _CronAuditLog()

            register_default_jobs(
                self.scheduler,
                memory=self.memory,
                audit_log=_audit_log_instance,
                send_telegram=_send_telegram,
                provider_chain=self.tier_chains.get(1, self.provider_chain),  # T1 as cheap judge
                interaction_logger=self.interaction_logger,
            )
            print(f"🕰️ ABLE Persistent Scheduler started with {len(self.scheduler.jobs)} autonomous missions")
        else:
            logger.info("ABLE cron disabled; set ABLE_CRON_ENABLED=1 on the single cron leader")
            print("⏸️ ABLE Scheduler disabled (follower mode; set ABLE_CRON_ENABLED=1 on one server only)")

        # ── Startup Health Report ───────────────────────────────────
        # Run doctor checks and report degraded subsystems so operator
        # knows exactly what to fix. This prevents silent cron failures.
        try:
            from able.tools.doctor import Doctor
            _doc = Doctor()
            _report = _doc.run_all()
            if _report.error_count > 0:
                print(f"⚠️  ABLE Doctor: {_report.error_count} errors, {_report.warning_count} warnings")
                for r in _report.results:
                    if r.status == "error":
                        print(f"   ❌ {r.check_name}: {r.message}")
                        if r.suggestion:
                            print(f"      Fix: {r.suggestion}")
            elif _report.warning_count > 0:
                print(f"⚡ ABLE Doctor: {_report.ok_count} OK, {_report.warning_count} warnings")
            else:
                print(f"✅ ABLE Doctor: all {_report.ok_count} checks passed")
        except Exception as _doc_err:
            logger.warning("Doctor health check failed: %s", _doc_err)

        if self.cron_enabled:
            # Recovery runs once at startup. Keeping this outside the restart loop
            # prevents a scheduler crash from re-running recovery and re-firing jobs.
            async def _supervised_scheduler():
                await self.scheduler.recover_missed_jobs(max_lookback_hours=48)
                while True:
                    try:
                        await self.scheduler.run_forever(poll_interval=30.0)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error("Scheduler crashed, restarting in 30s: %s", e, exc_info=True)
                        await asyncio.sleep(30)

            asyncio.create_task(_supervised_scheduler())

        # ── Config hot-reload polling (60s interval) ──────────────────
        # Detects changes to routing_config.yaml, scorer_weights.yaml,
        # and tool_permissions.yaml — rebuilds in-memory state w/o restart.
        _routing_config_path = _PROJECT_ROOT / "config" / "routing_config.yaml"
        _scorer_weights_path = _PROJECT_ROOT / "config" / "scorer_weights.yaml"
        _tool_perms_path = _PROJECT_ROOT / "config" / "tool_permissions.yaml"

        async def _config_reload_poller():
            import hashlib
            _last_scorer_hash = ""
            _last_perms_hash = ""
            try:
                if _scorer_weights_path.exists():
                    _last_scorer_hash = hashlib.md5(_scorer_weights_path.read_bytes()).hexdigest()
                if _tool_perms_path.exists():
                    _last_perms_hash = hashlib.md5(_tool_perms_path.read_bytes()).hexdigest()
            except OSError:
                pass

            while True:
                await asyncio.sleep(60)
                try:
                    # Check routing_config.yaml for provider changes
                    if hasattr(self, 'provider_registry') and self.provider_registry:
                        if self.provider_registry.reload_from_yaml(_routing_config_path):
                            # Rebuild tier chains from updated registry
                            for tier in self.provider_registry.tiers:
                                self.tier_chains[tier] = self.provider_registry.build_chain_for_tier(tier)
                            self.provider_chain = self.provider_registry.build_provider_chain()
                            logger.info("Provider chains rebuilt from hot-reloaded config")

                    # Check scorer_weights.yaml for weight changes
                    if self.complexity_scorer and _scorer_weights_path.exists():
                        new_hash = hashlib.md5(_scorer_weights_path.read_bytes()).hexdigest()
                        if new_hash != _last_scorer_hash:
                            self.complexity_scorer.reload_weights()
                            _last_scorer_hash = new_hash
                            logger.info("Scorer weights hot-reloaded (v%d)", self.complexity_scorer.version)

                    # E6: Check tool_permissions.yaml for permission changes
                    if _tool_perms_path.exists():
                        new_hash = hashlib.md5(_tool_perms_path.read_bytes()).hexdigest()
                        if new_hash != _last_perms_hash:
                            _last_perms_hash = new_hash
                            # Reload CommandGuard permissions
                            if hasattr(self, 'command_guard') and self.command_guard:
                                self.command_guard._yaml_permissions = self.command_guard._load_yaml_permissions()
                                self.command_guard._policy_engine = self.command_guard._load_policy_engine()
                                logger.info("Tool permissions hot-reloaded from %s", _tool_perms_path)
                            # Reload ShellSource permissions if wired
                            try:
                                from able.tools.sources.shell_source import _load_yaml_permissions as _reload_shell_perms
                                _reload_shell_perms()
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug("Config reload check failed: %s", e)

        asyncio.create_task(_config_reload_poller())

        # Start the Evolution Daemon only on the cron leader. It is autonomous
        # background work and must not run from local follower gateways.
        if self.cron_enabled:
            try:
                from able.core.evolution.daemon import EvolutionDaemon, EvolutionConfig
                evo_config = EvolutionConfig(
                    weights_path=str(_PROJECT_ROOT / "config" / "scorer_weights.yaml"),
                    interaction_db=str(_PROJECT_ROOT / "data" / "interaction_log.db"),
                    cycle_log_dir=str(_PROJECT_ROOT / "data" / "evolution_cycles"),
                    cycle_interval_hours=6,
                    min_interactions_for_cycle=20,
                    auto_deploy=True,
                )
                # Wire M2.7 provider if available from registry
                m27_provider = None
                if hasattr(self, 'provider_registry') and self.provider_registry:
                    m27_config = self.provider_registry.get_provider_config("minimax-m2.7")
                    if m27_config and m27_config.is_available:
                        m27_provider = self.provider_registry._instantiate_provider(m27_config)
                        logger.info("Evolution daemon connected to MiniMax M2.7")

                # Wire split test policy for A/B testing weight changes
                split_policy = None
                try:
                    from able.core.evolution.split_test_integration import EvolutionSplitTestPolicy
                    split_policy = EvolutionSplitTestPolicy()
                except Exception as e:
                    logger.warning(f"Split test policy unavailable: {e}")

                self.evolution_daemon = EvolutionDaemon(
                    config=evo_config,
                    m27_provider=m27_provider,
                    split_policy=split_policy,
                    approval_workflow=self.approval_workflow,
                    memory=getattr(self, "memory", None),
                )
                asyncio.create_task(self.evolution_daemon.run_continuous())
                print(f"🧬 Evolution Daemon started (6h cycle, M2.7 {'connected' if m27_provider else 'rule-based fallback'}, split_test={'on' if split_policy else 'off'})")
            except Exception as e:
                logger.warning(f"Evolution daemon failed to start: {e}")
                print(f"⚠️ Evolution daemon not started: {e}")
        else:
            logger.info("Evolution daemon disabled in cron follower mode")

        # Report observability status
        if self.phoenix and self.phoenix.is_available:
            import os as _os
            _ph_ui = _os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces").replace("/v1/traces", "")
            print(f"🔭 Phoenix dashboard: {_ph_ui} (project: able)")
        elif self.tracer:
            print(f"🔭 Tracing: JSONL fallback (data/traces.jsonl)")
        if self.evaluator:
            print(f"📊 Quality evaluators: hallucination, correctness, skill_adherence, tone")

        # Report routing status
        if self.complexity_scorer:
            tier_info = ", ".join(f"T{t}: {len(c.providers)}p" for t, c in self.tier_chains.items())
            print(f"🎯 Complexity-scored routing active (scorer v{self.complexity_scorer.version}) [{tier_info}]")
        print(f"🛡️ Circuit breaker: 3-fail threshold, 5min cooldown (instant skip on dead providers)")
        print(f"🧠 Approval preference learning: auto-approve after {ApprovalWorkflow.AUTO_APPROVE_THRESHOLD} consecutive approvals")
        print(f"🔧 Tool self-healing: alert after {self._tool_failure_threshold} failures in {self._tool_failure_window}s")

        while True:
            await asyncio.sleep(1)


# Entry point
if __name__ == "__main__":
    gateway = ABLEGateway()
    asyncio.run(gateway.run())
