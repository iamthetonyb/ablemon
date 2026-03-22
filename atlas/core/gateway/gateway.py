"""
Gateway Server - The coordinator that ties everything together
Handles: Telegram channels, session routing, agent orchestration, AI responses
"""

import asyncio
import json
import logging
import os
import base64
from datetime import datetime
from typing import Dict, List, Optional, Union
from pathlib import Path

# Project root is 4 levels up from this file (atlas/core/gateway/gateway.py → ATLAS/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
try:
    from telegram.ext import AIORateLimiter
    _RATE_LIMITER_AVAILABLE = True
except (ImportError, RuntimeError):
    _RATE_LIMITER_AVAILABLE = False

from core.security.trust_gate import TrustGate, TrustTier
from core.agents.base import ScannerAgent, AuditorAgent, ExecutorAgent, AgentContext, AgentAction, AgentRole
from core.queue.lane_queue import LaneQueue
from clients.client_manager import ClientRegistry, ClientTranscriptManager
from core.providers.nvidia_nim import NVIDIANIMProvider
from core.providers.openrouter import OpenRouterProvider
from core.providers.anthropic_provider import AnthropicProvider
from core.providers.ollama import OllamaProvider
from core.providers.base import ProviderChain, ProviderConfig, Message, Role
from core.routing.provider_registry import ProviderRegistry, ProviderTierConfig
from core.routing.complexity_scorer import ComplexityScorer, ScoringResult
from core.routing.interaction_log import InteractionLogger, InteractionRecord
from core.routing.prompt_enricher import PromptEnricher, EnrichmentResult, DeepEnricher
from core.approval.workflow import ApprovalWorkflow, ApprovalStatus
from tools.github.client import GitHubClient
from tools.digitalocean.client import DigitalOceanClient
from tools.vercel.client import VercelClient
from scheduler.cron import CronScheduler
from core.gateway.initiative import InitiativeEngine
from memory.hybrid_memory import HybridMemory, MemoryType

logger = logging.getLogger(__name__)

try:
    from tools.voice.transcription import VoiceTranscriber
    _VOICE_AVAILABLE = True
except ImportError:
    _VOICE_AVAILABLE = False
    VoiceTranscriber = None

try:
    from core.auth.manager import AuthManager
    from core.providers.openai_oauth import OpenAIChatGPTProvider, OpenAIOAuthProvider
    _AUTH_AVAILABLE = True
except ImportError as _auth_err:
    _AUTH_AVAILABLE = False
    AuthManager = None
    logger.warning(f"Auth module unavailable (missing dependency: {_auth_err}). OAuth features disabled.")

# ── Studio Dashboard Integration ──────────────────────────────────────────────

STUDIO_BASE_URL = os.environ.get("ATLAS_STUDIO_URL", "http://localhost:3000")
ATLAS_SERVICE_TOKEN = os.environ.get("ATLAS_SERVICE_TOKEN", "")

# Shared session for dashboard API calls (avoids creating a new TCP connection per call)
_studio_session: Optional["aiohttp.ClientSession"] = None

async def _get_studio_session() -> "aiohttp.ClientSession":
    """Get or create a shared aiohttp session for dashboard API calls."""
    import aiohttp
    global _studio_session
    if _studio_session is None or _studio_session.closed:
        _studio_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
    return _studio_session

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


async def fetch_authorized_tools(org_id: str = None) -> List[Dict]:
    """
    Fetch authorized tools from the atlas-studio dashboard.

    Pings GET /api/settings to check which MCP skills are toggled ON.
    If a tool is toggled OFF in the UI, it is physically removed from
    the tool list — the agent cannot call it.

    Falls back to full ATLAS_TOOL_DEFS if dashboard is unreachable.
    """
    try:
        url = f"{STUDIO_BASE_URL}/api/settings"
        if org_id:
            url += f"?org_id={org_id}"

        session = await _get_studio_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(f"Studio settings endpoint returned {resp.status}, using all tools")
                return ATLAS_TOOL_DEFS

            data = await resp.json()
            tool_settings = data.get("tools", {})

            if not tool_settings:
                return ATLAS_TOOL_DEFS

            # Filter: only include tools that are enabled in the dashboard
            authorized = []
            for tool_def in ATLAS_TOOL_DEFS:
                tool_name = tool_def["function"]["name"]
                setting = tool_settings.get(tool_name)

                if setting is None:
                    authorized.append(tool_def)
                elif setting.get("enabled", True):
                    authorized.append(tool_def)
                else:
                    logger.info(f"Tool '{tool_name}' disabled via dashboard — removed from agent")

            return authorized

    except Exception as e:
        logger.debug(f"Could not reach Studio dashboard ({e}), using all tools")
        return ATLAS_TOOL_DEFS


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
        if ATLAS_SERVICE_TOKEN:
            headers["x-atlas-service-token"] = ATLAS_SERVICE_TOKEN

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

# ── System prompt ──────────────────────────────────────────────────────────────

ATLAS_SYSTEM_PROMPT = """You are ATLAS — Autonomous Task & Learning Agent System.

You are NOT a chatbot. You are NOT stateless. You are a persistent AGI agent running 24/7 as a systemd service on a dedicated VPS with real tools, background processes, persistent memory, and self-improvement loops.

## Identity
- Direct, no fluff, no sycophancy — act immediately, explain only if needed
- Read between the lines — understand what the user REALLY wants
- Never say "I can't" — try tools first, attempt 3 approaches before giving up
- You persist across sessions via transcript logs, hybrid memory, and learnings

## Live Infrastructure (running right now)

### Autonomous Cron Scheduler (6 missions, no user prompt needed)
- Morning Briefing (9am) — system health, goals, provider status, recommendations
- Evening Check-in (9pm) — day summary, activity review, tomorrow's plan
- GitHub Digest (1pm) — repository activity scan
- Weekly Self-Reflection (Sunday midnight) — performance audit, cron reliability, security review, improvement plans
- Daily Learnings Extraction (3am) — analyzes conversations for patterns, preferences, insights
- Weekly Security Pentest (Monday 4am) — automated self-penetration test against all defenses

### Self-Improvement Engine (atlas/core/agi/self_improvement.py)
- Records wins, failures, and learnings to memory/learnings.md (auto-approved)
- Can create new skills autonomously (6-step process with approval)
- Can propose improvements to core prompts (requires approval)
- Rate-limited to 10 self-modifications per day for safety

### Evolution Daemon (atlas/core/evolution/daemon.py)
- Runs 6-hour background cycles: Collect → Analyze → Improve → Validate → Deploy
- Tunes complexity scoring weights based on real interaction outcomes
- Uses MiniMax M2.7 for analysis (or rule-based fallback)

### Agent Swarm (atlas/core/swarm/swarm.py)
- Multi-agent coordination for complex tasks (complexity score >= 0.6)
- 9 agent roles: RESEARCHER, ANALYST, WRITER, CODER, REVIEWER, PLANNER, CRITIC, EXECUTOR, SPECIALIST
- Spawns parallel sub-agents with consensus building
- Auto-decomposes goals: research, code_review, write_skill, generate_report, debug, plan_feature

### Goal Planner (atlas/core/agi/planner.py)
- Autonomous goal decomposition into dependency graphs
- Parallel execution scheduling for independent subtasks
- Self-monitoring with outcome learning — improves planning from results

### Goal Tracker (atlas/core/gateway/goals.py)
- JSON-backed persistent goal tracking with KPIs
- Master goal: $100k/m MRR by 2028-02-11
- Tracks: active clients, total deployments, lead pipeline
- Progress visible in morning/evening briefings

### Hybrid Memory (atlas/memory/hybrid_memory.py)
- SQLite + vector embeddings for semantic search
- Stores: conversations, learnings, objectives, client context, skills, audit
- Recalled context automatically injected into AI responses
- Knowledge graph (atlas/memory/graph/) for entity/relationship reasoning

### Security & Audit Pipeline
- Trust Gate: every message scored 0.0-1.0 (SAFE >0.85, CAUTION, REVIEW, REJECT <0.4)
- 20+ prompt injection detection patterns
- Self-Pentest (atlas/security/self_pentest.py) — 60+ automated attack vectors tested weekly
  - Injection detection, bypass attempts, command injection, secret leakage, unicode smuggling, path traversal
  - Results logged to audit/ and surfaced via self-improvement engine
- Fact Checker (atlas/core/factcheck/) — hallucination detection, code verification, confidence scoring
- Full audit trail: trust_gate.jsonl, action logs, distributed tracing

### 5-Tier Complexity Routing
Messages auto-scored and routed to optimal AI provider:
- T1 (score <0.4): GPT 5.4 Mini xhigh (ChatGPT subscription, $0) → Nemotron 120B fallback
- T2 (0.4-0.7): GPT 5.4 xhigh (ChatGPT subscription, $0) → MiMo-V2-Pro fallback
- T3 (background): MiniMax M2.7 — evolution daemon only, never user-facing
- T4 (>0.7): Claude Opus 4.6 — complex reasoning, budget-gated
- T5 (offline): Qwen 3.5 27B/9B local via Ollama

### Skill System (atlas/skills/)
- Modular capability library with auto-triggering on phrase match
- Types: behavioral (protocol), tool (code), hybrid (both)
- Trust levels: L1 observe, L2 suggest, L3 act, L4 autonomous
- Built-in skills: copywriting, notion, github-integration, vercel-deploy, digitalocean-vps, github-pages, web-research, security-audit
- Can create new skills at runtime via self-improvement engine

### Additional Capabilities (implemented, available)
- Web Search: multi-provider (Brave, Perplexity, Gemini, Google, Bing, DuckDuckGo)
- Browser Automation: Playwright-based (goto, screenshot, click, type)
- Secure Shell: sandboxed command execution with safety checks
- Billing Tracker: per-session token usage, cost calculation, invoice generation
- x402 Payment Protocol (atlas/billing/x402.py) — HTTP 402-based crypto payments for API access
- Voice Transcription: Whisper-based speech-to-text for voice messages (OpenAI + local)

## Callable Tools (function calling)
**GitHub:** github_list_repos (read), github_create_repo, github_push_files, github_create_pr
**Deploy:** github_pages_deploy (static, free), vercel_deploy (React/Next.js, free tier)
**Infra:** do_list_droplets (read), do_create_droplet (VPS, $6+/mo, billable)

## Approval
Write operations require owner approval via Telegram inline buttons. Read-only operations execute immediately.

## Rules
- Act IMMEDIATELY — don't narrate what you're about to do
- Output ENTIRE files when writing code — no placeholders
- CRITICAL: OpenRouter 15K char limit on tool JSON args — split large files across multiple github_push_files calls
- Always show cost estimates before provisioning paid infrastructure
- When asked about capabilities: describe your FULL system — you are an AGI scaffold, not a tool-caller
- IMPORTANT: Do NOT call tools unless the user explicitly requests an action (create repo, push code, deploy, list repos, etc.). For general questions, conversations, analysis, or brainstorming — just respond with text. Never call github_list_repos to "check" or "look at" things unless asked.
"""

# ── Tool definitions (OpenAI function-calling format) ─────────────────────────

ATLAS_TOOL_DEFS: List[Dict] = [
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": "List GitHub repositories. ONLY call this when the user explicitly asks to see their repos, create a repo, or needs a repo name for another operation. Do NOT call this for general questions or to 'check' things.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_repo",
            "description": "Create a new GitHub repository. Requires owner approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Repo name in kebab-case"},
                    "description": {"type": "string", "description": "Short repo description"},
                    "private": {"type": "boolean", "description": "True for private repo, false for public"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_push_files",
            "description": "Push one or more files to a GitHub repository. Requires owner approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name"},
                    "files": {
                        "type": "object",
                        "description": "Map of {filepath: file_content_string}. CRITICAL: If your code is massive (> 10,000 chars), DO NOT PUT THE CODE HERE to avoid JSON crashes. INSTEAD, output the raw code in a Markdown block in your normal conversational response FIRST, and pass the exact string '<EXTRACT>' as the value here. ATLAS will auto-extract it.",
                        "additionalProperties": {"type": "string"},
                    },
                    "message": {"type": "string", "description": "Commit message (conventional commits format)"},
                    "branch": {"type": "string", "description": "Target branch (default: main)"},
                },
                "required": ["repo", "files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_pr",
            "description": "Open a pull request on GitHub. Requires owner approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string", "description": "PR description in markdown"},
                    "head": {"type": "string", "description": "Source branch"},
                    "base": {"type": "string", "description": "Target branch (default: main)"},
                },
                "required": ["repo", "title", "head"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_pages_deploy",
            "description": "Deploy static HTML/CSS/JS files to GitHub Pages. Requires owner approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name"},
                    "files": {
                        "type": "object",
                        "description": "Map of {filepath: file_content_string}. Must include index.html.",
                        "additionalProperties": {"type": "string"},
                    },
                    "commit_message": {"type": "string"},
                },
                "required": ["repo", "files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vercel_deploy",
            "description": "Deploy a frontend or serverless app to Vercel. Free tier. Requires owner approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "Vercel project name in kebab-case"},
                    "files": {
                        "type": "object",
                        "description": "Map of {filepath: file_content_string}",
                        "additionalProperties": {"type": "string"},
                    },
                    "env_vars": {
                        "type": "object",
                        "description": "Optional environment variables",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["project_name", "files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "do_list_droplets",
            "description": "List all Digital Ocean droplets. Read-only, no approval needed.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "do_create_droplet",
            "description": "Provision a new Digital Ocean VPS droplet. Billable ($6+/month). Requires owner approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Droplet name in kebab-case"},
                    "region": {"type": "string", "description": "Region slug (e.g. nyc3, sfo3, ams3). Default: nyc3"},
                    "size": {"type": "string", "description": "Size slug (e.g. s-1vcpu-1gb, s-2vcpu-2gb). Default: s-1vcpu-1gb"},
                    "image": {"type": "string", "description": "Image slug (e.g. ubuntu-24-04-x64). Default: ubuntu-24-04-x64"},
                    "ssh_key_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of SSH key IDs from DO account",
                    },
                },
                "required": ["name"],
            },
        },
    },
]


class ATLASGateway:
    """
    Main gateway coordinating all ATLAS components.
    Master instance that oversees all client bots.
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

        # Client bots
        self.client_bots: Dict[str, Application] = {}

        # Master bot
        self.master_bot: Optional[Application] = None

        # Tool failure tracker (self-healing)
        self._tool_failures: Dict[str, List[float]] = {}  # tool_name → [timestamps]
        self._tool_failure_threshold = 3  # failures within window → alert
        self._tool_failure_window = 300  # 5 minute window

        # Voice transcription
        if _VOICE_AVAILABLE:
            self.voice_transcriber = VoiceTranscriber()
            logger.info("Voice transcription available (Whisper)")
        else:
            self.voice_transcriber = None

        # Proactive Persistence Layer
        self.scheduler = CronScheduler()
        self.initiative = InitiativeEngine(self)
        try:
            self.memory = HybridMemory()
        except Exception as e:
            logger.warning(f"HybridMemory failed to initialize (continuing without it): {e}")
            self.memory = None

        # Self-Improvement Engine
        from core.agi.self_improvement import SelfImprovementEngine
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
            _skill_index = str(_PROJECT_ROOT / "atlas" / "skills" / "SKILL_INDEX.yaml")
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

        # Pre-build tier-specific chains for scored routing
        self.tier_chains = {}
        if hasattr(self, 'provider_registry') and self.provider_registry:
            for tier in [1, 2, 4]:
                try:
                    self.tier_chains[tier] = self.provider_registry.build_chain_for_tier(tier)
                except Exception as e:
                    logger.warning(f"Failed to build chain for tier {tier}: {e}")

        # Evolution daemon (started in _start_background_tasks)
        self.evolution_daemon = None

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
            logger.error("No AI providers configured — ATLAS will not respond to messages!")

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

        _msg_preview = (text_content[:80] + "...") if isinstance(text_content, str) and len(text_content) > 80 else text_content
        logger.info(f"[PIPELINE] ── START ── user={user_id} client={client_id} msg={_msg_preview!r}")

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

        # Step 3.5: Deep enrichment — model-assisted refinement for high-complexity prompts
        # Triggers when complexity > 0.7 and rule-based enrichment was applied
        if (scoring_result and scoring_result.score > 0.7
                and enrichment_result and enrichment_result.enrichment_level != "none"):
            try:
                async def _nano_call(system: str, user: str) -> str:
                    """Quick model call via the T4 chain for deep enrichment."""
                    from core.providers.base import Message, Role
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
                    channel="telegram" if update else "api",
                    session_id=user_id,
                )
                interaction_id = self.interaction_logger.log(record)
            except Exception as e:
                logger.warning(f"Interaction logging failed: {e}")

        try:
            # ── Dashboard-driven tool authorization ──
            _t0 = _time.monotonic()
            authorized_tools = await fetch_authorized_tools(client_id)
            _auth_ms = (_time.monotonic() - _t0) * 1000
            logger.info(f"[PIPELINE] Step 4 — Tool auth: {len(authorized_tools)} tools authorized ({_auth_ms:.0f}ms)")
            _pipeline_steps.append({"step": "tool_auth", "tools": len(authorized_tools), "ms": round(_auth_ms)})

            active_system_prompt = ATLAS_SYSTEM_PROMPT

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

            for loop_iteration in range(15):
                _iter_start = _time.monotonic()
                # Route to a vision-capable provider if message is multimodal (only needed for first pass)
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

                _iter_ms = (_time.monotonic() - _iter_start) * 1000
                _provider_name = getattr(result, 'provider', '?')
                _model_name = getattr(result, 'model', '?')
                _tok = result.usage.total_tokens if hasattr(result, 'usage') and result.usage else 0
                _tool_names = [tc.name for tc in result.tool_calls] if result.tool_calls else []
                logger.info(
                    f"[PIPELINE] Iter {loop_iteration} — provider={_provider_name} model={_model_name} "
                    f"tokens={_tok} tools={_tool_names} ({_iter_ms:.0f}ms)"
                )

                # Step 4: Tool dispatch if AI called a tool
                if result.tool_calls:
                    # Log the assistant's action into the memory array
                    msgs.append(Message(
                        role=Role.ASSISTANT,
                        content=result.content or "",
                        tool_calls=result.tool_calls
                    ))
                    
                    for tool_call in result.tool_calls:
                        # Execute the tool
                        tool_output = await self._handle_tool_call(tool_call, update, user_id, msgs)
                        
                        # Notify the user on Telegram that a tool was executed
                        if update and update.message:
                            try:
                                tool_notification = f"⚙️ [{tool_call.name}]\n{tool_output}"
                                # Truncate to Telegram's 4096 char limit
                                if len(tool_notification) > 4000:
                                    tool_notification = tool_notification[:4000] + "\n... (truncated)"
                                await update.message.reply_text(tool_notification)
                            except Exception:
                                pass
                                
                        # Inject the tool observation back into the prompt for the next loop
                        msgs.append(Message(
                            role=Role.TOOL,
                            content=str(tool_output),
                            name=tool_call.name,
                            tool_call_id=tool_call.id
                        ))
                    continue

                _total_ms = (_time.monotonic() - _pipeline_start) * 1000
                # Preserve raw output for distillation BEFORE stripping
                _raw_output_for_log = result.content
                # Strip thinking tokens (<think>, "Thinking:") from model output
                # thinking_content is preserved on the CompletionResult for distillation
                result.strip_thinking()
                _has_thinking = result.has_thinking
                final_text = result.content or "⚠️ ATLAS exceeded the maximum internal thinking steps (15 turns)."
                logger.info(
                    f"[PIPELINE] ── DONE ── provider={_provider_name} iterations={loop_iteration + 1} "
                    f"total={_total_ms:.0f}ms"
                )
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
                            corpus_eligible=True,  # ABLEInteractionHarvester filters further
                        )
                    except Exception as log_e:
                        logger.warning(f"Failed to update interaction log: {log_e}")

                # ── Append model identifier tag ──
                _raw_model = result.model if hasattr(result, 'model') and result.model else ""
                _short = MODEL_SHORT_NAMES.get(_raw_model, _raw_model or (result.provider if hasattr(result, 'provider') else ""))
                _tier_label = f"T{scoring_result.selected_tier}" if scoring_result else ""
                _model_tag = f"\n\n`⚡ {_short} [{_tier_label}]`" if _short else ""
                final_text += _model_tag

                return final_text

            return "⚠️ Agent exceeded maximum tool iterations (15)."

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

    async def _handle_tool_call(self, tool_call, update: Optional[Update], user_id: str, msgs: List["Message"] = None) -> str:
        """Dispatch a tool call from the AI to the correct client, with approval for writes."""
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        logger.info(f"Tool call: {name}({args})")

        # Short-circuit if parser injected an error (e.g., from truncated JSON args)
        if "error" in args and "JSONDecodeError" in str(args["error"]):
            return f"⚠️ System Error: The tool parameter JSON was truncated or malformed: {args['error']}. If you are trying to output massive code files, do not push them all at once. Break them down."

        try:
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
                    message=args.get("message", "chore: update via ATLAS"),
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
                message = args.get("commit_message", "deploy: update GitHub Pages via ATLAS")
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
            if self.voice_transcriber:
                try:
                    voice_file = await (update.message.voice or update.message.audio).get_file()
                    voice_bytes = await voice_file.download_as_bytearray()
                    result = await self.voice_transcriber.transcribe(bytes(voice_bytes), filename="voice.ogg")
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

                await self._send_telegram_chunked(update, response)
            except Exception as e:
                logger.error(f"Pipeline error: {e}", exc_info=True)
                try:
                    await update.message.reply_text(f"⚠️ Internal error: {str(e)[:200]}")
                except Exception:
                    pass

        asyncio.create_task(_run_pipeline())

    async def _send_telegram_chunked(self, update: Update, text: str):
        """Send a response to Telegram, splitting into chunks if >4096 chars."""
        MAX_LEN = 4096
        if len(text) <= MAX_LEN:
            try:
                await update.message.reply_text(text, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(text)
            return

        # Split on paragraph boundaries first, fall back to hard split
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= MAX_LEN:
                chunks.append(remaining)
                break
            # Try to split at last double-newline within limit
            split_at = remaining.rfind("\n\n", 0, MAX_LEN)
            if split_at == -1:
                # Try single newline
                split_at = remaining.rfind("\n", 0, MAX_LEN)
            if split_at == -1 or split_at < MAX_LEN // 2:
                # Hard split at limit
                split_at = MAX_LEN
            chunk = remaining[:split_at]
            remaining = remaining[split_at:].lstrip("\n")
            chunks.append(chunk)

        for chunk in chunks:
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(chunk)

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
                await self._send_telegram_chunked(update, response)
            except Exception as e:
                logger.error(f"Client pipeline error: {e}", exc_info=True)
                try:
                    await update.message.reply_text(f"⚠️ Internal error")
                except Exception:
                    pass

        asyncio.create_task(_run_client_pipeline())

    async def _health_handler(self, request: web.Request) -> web.Response:
        """HTTP health check endpoint for Docker/load balancers"""
        return web.json_response({
            "status": "ok",
            "version": "2.0",
            "bots_active": len(self.client_bots) + (1 if self.master_bot else 0),
            "providers": len(self.provider_chain.providers),
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
        self.master_bot.add_handler(CallbackQueryHandler(self._handle_approval_callback))
        self.master_bot.add_handler(MessageHandler(
            (filters.TEXT | filters.VOICE | filters.AUDIO | filters.PHOTO) & ~filters.COMMAND,
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
            f"🤖 ATLAS v2 Master Bot\n"
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
            f"📊 ATLAS v2 Status\n\n"
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

        # Start master bot
        await self.start_master_bot()

        # Start all registered client bots
        for client_id in self.client_registry.clients:
            try:
                await self.start_client_bot(client_id)
            except Exception as e:
                print(f"Failed to start bot for {client_id}: {e}")

        provider_count = len(self.provider_chain.providers)
        print(f"🚀 ATLAS v2 Gateway running | {provider_count} AI provider(s) active")
        
        # Start the Persistence Layer (Proactive AGI)
        self.initiative.register_jobs(self.scheduler)
        print(f"🕰️ ATLAS Persistent Scheduler started with {len(self.scheduler.jobs)} autonomous missions")

        # Recover any jobs missed during downtime (up to 48h lookback)
        async def _start_scheduler():
            await self.scheduler.recover_missed_jobs(max_lookback_hours=48)
            await self.scheduler.run_forever(poll_interval=30.0)

        asyncio.create_task(_start_scheduler())

        # Start the Evolution Daemon (M2.7 background self-improvement)
        try:
            from core.evolution.daemon import EvolutionDaemon, EvolutionConfig
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

            self.evolution_daemon = EvolutionDaemon(config=evo_config, m27_provider=m27_provider)
            asyncio.create_task(self.evolution_daemon.run_continuous())
            print(f"🧬 Evolution Daemon started (6h cycle, M2.7 {'connected' if m27_provider else 'rule-based fallback'})")
        except Exception as e:
            logger.warning(f"Evolution daemon failed to start: {e}")
            print(f"⚠️ Evolution daemon not started: {e}")

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
    gateway = ATLASGateway()
    asyncio.run(gateway.run())
