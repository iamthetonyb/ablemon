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

**Deployment:**
- `github_pages_deploy` — Deploy static HTML/CSS/JS to GitHub Pages (free, instant)
- `vercel_deploy` — Deploy React/Next.js/frontend to Vercel (free tier, CDN)

**Infrastructure:**
- `do_list_droplets` — List Digital Ocean droplets (read-only)
- `do_create_droplet` — Provision a new Digital Ocean VPS ($6+/month, billable)

## Hosting Decision Guide
- Static HTML/CSS/JS → GitHub Pages (free, simple)
- React/Next.js/frontend → Vercel (free tier, CDN, serverless)
- Backend/database/long-running → Digital Ocean VPS (billable)
- Need root access/custom env → Digital Ocean VPS

## Approval
All write operations require owner approval via Telegram inline buttons.
Read-only operations (list repos, list droplets) execute immediately.

## Rules
- Never say "I can't" — try tools first
- Be direct and concise
- If unsure which tool to use, ask one focused question
- Always show cost estimates before provisioning paid infrastructure
"""

# ── Tool definitions (OpenAI function-calling format) ─────────────────────────

ATLAS_TOOL_DEFS: List[Dict] = [
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": "List all GitHub repositories for the authenticated user. Read-only, no approval needed.",
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
                        "description": "Map of {filepath: file_content_string}",
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

        # Client bots
        self.client_bots: Dict[str, Application] = {}

        # Master bot
        self.master_bot: Optional[Application] = None

    def _init_providers(self) -> ProviderChain:
        """Build ProviderChain, skipping any provider whose env var is missing."""
        providers = []

        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                # Primary Provider: Qwen 3.5 397B (via OpenRouter FP8)
                providers.append(OpenRouterProvider(
                    api_key=openrouter_key,
                    model="qwen/qwen3.5-397b-a17b",
                    timeout=120.0
                ))
                logger.info("Provider added: OpenRouter (Qwen 397B)")
            except Exception as e:
                logger.warning(f"Failed to init OpenRouter provider: {e}")

        # (OpenRouter block is now earlier, so we can remove the old Claude logic or keep it as fallback)
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            try:
                providers.append(AnthropicProvider(ProviderConfig(
                    api_key=anthropic_key,
                    model="claude-opus-4-5",
                )))
                logger.info("Provider added: Anthropic")
            except Exception as e:
                logger.warning(f"Failed to init Anthropic provider: {e}")

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            providers.append(OllamaProvider(ProviderConfig(
                model="llama3.1",
                base_url=ollama_url,
            )))
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
                # Primary Vision Provider: Moonshot Kimi 2.5 (OpenRouter)
                providers.append(OpenRouterProvider(
                    api_key=openrouter_key,
                    model="moonshotai/moonshot-v1-auto", # Also known as kimi-k2.5 equivalent
                    timeout=120.0
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

        # Extract textual content from multimodal list for security scanning
        text_content = message
        if isinstance(message, list):
            text_content = next((item.get("text", "") for item in message if item.get("type") == "text"), "")

        # Step 1: Scanner (read-only analysis)
        scan_result = await self.scanner.process(text_content, metadata or {})

        if not scan_result["security_verdict"]["passed"]:
            return f"⚠️ Security check failed: {scan_result['blocked_reason']}"

        # Step 2: Auditor (validation)
        audit_result = await self.auditor.process(scan_result)

        if not audit_result["approved_for_executor"]:
            return f"⚠️ Audit failed: {'; '.join(audit_result['notes'])}"

        # Step 3: AI response via ProviderChain
        if not self.provider_chain.providers:
            return "⚠️ No AI providers configured. Set NVIDIA_API_KEY, OPENROUTER_API_KEY, or ANTHROPIC_API_KEY."

        try:
            msgs = [Message(role=Role.SYSTEM, content=ATLAS_SYSTEM_PROMPT)]
            
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

            msgs.append(Message(role=Role.USER, content=message))

            for loop_iteration in range(5):
                # Route to a vision-capable provider if message is multimodal (only needed for first pass)
                if isinstance(message, list) and loop_iteration == 0:
                    result = await self.vision_chain.complete(
                        msgs,
                        tools=ATLAS_TOOL_DEFS,
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
                            
                    result = await self.provider_chain.complete(
                        text_only_msgs,
                        tools=ATLAS_TOOL_DEFS,
                        max_tokens=16384, # Reduced to prevent OpenRouter from reserving massive account balances and triggering 429
                        temperature=0.60,
                        top_p=0.95,
                        top_k=20,
                        presence_penalty=0,
                        repetition_penalty=1,
                        # Target atlas-cloud specifically, enabling up to 1M YaRN context expansion
                        provider={
                            "order": ["OpenRouter"],
                            "allow_fallbacks": True,
                            "require_parameters": True,
                            "data_collection": "deny"
                        },
                        models=["qwen/qwen3.5-397b-a17b"],
                        route="fallback",
                        # Allow massive context scaling in OpenRouter
                        extra_body={
                            "chat_template_kwargs": {"enable_thinking": True}
                        }
                    )

                # Step 4: Tool dispatch if AI called a tool
                if result.tool_calls:
                    tool_call = result.tool_calls[0]
                    
                    # Log the assistant's action into the memory array
                    msgs.append(Message(
                        role=Role.ASSISTANT,
                        content=result.content or "",
                        tool_calls=[tool_call]
                    ))
                    
                    # Execute the tool
                    tool_output = await self._handle_tool_call(tool_call, update, user_id)
                    
                    # Notify the user on Telegram that a tool was executed so they aren't waiting in silence
                    if update and update.message:
                        try:
                            # Drop the Markdown parse mode for tool outputs to prevent unescaped char errors from code snippets
                            await update.message.reply_text(f"⚙️ `[{tool_call.name}]`\n{tool_output}")
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

                return result.content or "⚠️ Empty response from AI."
                
            return "⚠️ Agent exceeded maximum tool iterations (5)."

        except Exception as e:
            logger.error(f"AI completion failed: {e}", exc_info=True)
            return f"⚠️ AI error: {e}"

    async def _handle_tool_call(self, tool_call, update: Optional[Update], user_id: str) -> str:
        """Dispatch a tool call from the AI to the correct client, with approval for writes."""
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}

        logger.info(f"Tool call: {name}({args})")

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
            return f"⚠️ Tool error ({name}): {e}"

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
        while True:
            await asyncio.sleep(1)


# Entry point
if __name__ == "__main__":
    gateway = ATLASGateway()
    asyncio.run(gateway.run())
