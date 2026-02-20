"""
Gateway Server - The coordinator that ties everything together
Handles: Telegram channels, session routing, agent orchestration
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path

from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from core.security.trust_gate import TrustGate, TrustTier
from core.agents.base import ScannerAgent, AuditorAgent, ExecutorAgent, AgentContext, AgentAction, AgentRole
from core.queue.lane_queue import LaneQueue
from clients.client_manager import ClientRegistry, ClientTranscriptManager

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

        # Initialize agents
        self._init_agents()

        # Client bots
        self.client_bots: Dict[str, Application] = {}

        # Master bot
        self.master_bot: Optional[Application] = None

    def _init_agents(self):
        """Initialize the agent pipeline"""
        # Master scanner (read-only)
        self.scanner = ScannerAgent(AgentContext(
            agent_id="master_scanner",
            role=AgentRole.SCANNER,
            trust_tier=TrustTier.L4_AUTONOMOUS
        ), audit_dir=str(self.audit_dir))

        # Master auditor
        self.auditor = AuditorAgent(AgentContext(
            agent_id="master_auditor",
            role=AgentRole.AUDITOR,
            trust_tier=TrustTier.L4_AUTONOMOUS
        ), audit_dir=str(self.audit_dir))

        # Master executor
        self.executor = ExecutorAgent(AgentContext(
            agent_id="master_executor",
            role=AgentRole.EXECUTOR,
            trust_tier=TrustTier.L4_AUTONOMOUS
        ), audit_dir=str(self.audit_dir))

    async def process_message(
        self,
        message: str,
        user_id: str,
        client_id: Optional[str] = None,
        metadata: Dict = None
    ) -> str:
        """
        Main message processing pipeline:
        Input → Scanner → Auditor → Trust Gate → Executor
        """

        # Step 1: Scanner (read-only analysis)
        scan_result = await self.scanner.process(message, metadata or {})

        if not scan_result["security_verdict"]["passed"]:
            return f"⚠️ Security check failed: {scan_result['blocked_reason']}"

        # Step 2: Auditor (validation)
        audit_result = await self.auditor.process(scan_result)

        if not audit_result["approved_for_executor"]:
            return f"⚠️ Audit failed: {'; '.join(audit_result['notes'])}"

        # Step 3: Executor (if approved)
        # Here you would call your AI backend (Kimi K2.5, Claude, etc.)
        # For now, return confirmation

        return f"✅ Message processed (trust: {scan_result['security_verdict']['trust_score']:.2f})"

    async def _handle_master_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages to master bot"""
        user_id = str(update.effective_user.id)
        message = update.message.text

        # Check if owner
        if user_id != self.owner_telegram_id:
            await update.message.reply_text("⚠️ Unauthorized")
            return

        # Process through pipeline
        response = await self.process_message(
            message=message,
            user_id=user_id,
            metadata={"source": "master_telegram", "is_owner": True}
        )

        await update.message.reply_text(response)

    async def _handle_client_message(self, client_id: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages to client bots"""
        user_id = str(update.effective_user.id)
        message = update.message.text

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

        # Process through pipeline with client's trust tier
        response = await self.process_message(
            message=message,
            user_id=user_id,
            client_id=client_id,
            metadata={
                "source": f"client_telegram:{client_id}",
                "trust_tier": client.trust_tier
            }
        )

        # Log response
        self.transcript_manager.log_message(client_id, {
            "user_id": "bot",
            "message": response,
            "direction": "outbound"
        })

        await update.message.reply_text(response)

    async def _health_handler(self, request: web.Request) -> web.Response:
        """HTTP health check endpoint for Docker/load balancers"""
        return web.json_response({
            "status": "ok",
            "version": "2.0",
            "bots_active": len(self.client_bots) + (1 if self.master_bot else 0)
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
        self.master_bot = Application.builder().token(self.bot_token).build()

        # Add handlers
        self.master_bot.add_handler(CommandHandler("start", self._cmd_start))
        self.master_bot.add_handler(CommandHandler("status", self._cmd_status))
        self.master_bot.add_handler(CommandHandler("clients", self._cmd_clients))
        self.master_bot.add_handler(CommandHandler("audit", self._cmd_audit))
        self.master_bot.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_master_message
        ))

        await self.master_bot.initialize()
        await self.master_bot.start()
        await self.master_bot.updater.start_polling()

    async def start_client_bot(self, client_id: str):
        """Start a client's Telegram bot"""
        client = self.client_registry.get_client(client_id)
        if not client:
            raise ValueError(f"Client {client_id} not found")

        app = Application.builder().token(client.telegram_bot_token).build()

        # Wrap handler to include client_id
        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await self._handle_client_message(client_id, update, context)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        self.client_bots[client_id] = app

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 ATLAS v2 Master Bot\n\n"
            "Commands:\n"
            "/status - System status\n"
            "/clients - List clients\n"
            "/audit - View audit log\n"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.queue.get_stats()
        client_count = len(self.client_registry.clients)

        status = (
            f"📊 ATLAS v2 Status\n\n"
            f"🤖 Active client bots: {len(self.client_bots)}\n"
            f"👥 Registered clients: {client_count}\n"
            f"📋 Queue lanes: {stats['lane_count']}\n"
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
        # Show recent audit entries
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

        # Keep running
        print("🚀 ATLAS v2 Gateway running")
        while True:
            await asyncio.sleep(1)


# Entry point
if __name__ == "__main__":
    gateway = ATLASGateway()
    asyncio.run(gateway.run())
