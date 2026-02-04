"""
ATLAS v1/v2 Bridge
Synchronizes state between v1 (~/.atlas) and v2 (atlas-v2/) systems.
"""

import os
import yaml
import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime


class ATLASBridge:
    """
    Bridge between ATLAS v1 and v2 systems.
    Provides shared access to:
    - Secrets
    - Memory/learnings
    - Billing configuration
    - Client data
    - Audit logs
    """

    def __init__(self):
        self.v1_home = Path.home() / ".atlas"
        self.v2_home = Path(__file__).parent.parent

        # Check if v1 exists
        self.v1_exists = self.v1_home.exists()

    # ─────────────────────────────────────────────────────────────────────────
    # SECRETS
    # ─────────────────────────────────────────────────────────────────────────

    def get_secret(self, name: str) -> Optional[str]:
        """Get a secret from v1 or v2 secrets directory"""
        # Try v2 first
        v2_secret = self.v2_home / ".secrets" / name
        if v2_secret.exists():
            return v2_secret.read_text().strip()

        # Fall back to v1
        if self.v1_exists:
            v1_secret = self.v1_home / ".secrets" / name
            if v1_secret.exists():
                return v1_secret.read_text().strip()

        # Try environment variable
        env_name = name.upper().replace("-", "_")
        return os.environ.get(env_name)

    def set_secret(self, name: str, value: str, prefer_v1: bool = True):
        """Store a secret"""
        if prefer_v1 and self.v1_exists:
            path = self.v1_home / ".secrets" / name
        else:
            path = self.v2_home / ".secrets" / name

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        os.chmod(path, 0o600)

    # ─────────────────────────────────────────────────────────────────────────
    # IDENTITY & CONFIG
    # ─────────────────────────────────────────────────────────────────────────

    def get_identity(self) -> Dict[str, Any]:
        """Get operator identity from v1"""
        if self.v1_exists:
            identity_file = self.v1_home / "memory" / "identity.yaml"
            if identity_file.exists():
                return yaml.safe_load(identity_file.read_text()) or {}
        return {}

    def get_owner_telegram_id(self) -> Optional[str]:
        """Get owner's Telegram ID from v1 config or v2 config"""
        # Try v2 config
        v2_config = self.v2_home / "config" / "gateway.json"
        if v2_config.exists():
            config = json.loads(v2_config.read_text())
            if config.get("owner_telegram_id"):
                return config["owner_telegram_id"]

        # Try v1 identity
        identity = self.get_identity()
        return identity.get("operator", {}).get("telegram_id")

    # ─────────────────────────────────────────────────────────────────────────
    # MEMORY
    # ─────────────────────────────────────────────────────────────────────────

    def get_objectives(self) -> Dict[str, Any]:
        """Get current objectives from v1"""
        if self.v1_exists:
            objectives_file = self.v1_home / "memory" / "current_objectives.yaml"
            if objectives_file.exists():
                return yaml.safe_load(objectives_file.read_text()) or {}
        return {}

    def add_learning(self, content: str, category: str = "Session Learnings"):
        """Add a learning to v1 learnings.md"""
        if not self.v1_exists:
            return

        learnings_file = self.v1_home / "memory" / "learnings.md"
        if not learnings_file.exists():
            return

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        new_entry = f"\n### {timestamp}\n{content}\n"

        current = learnings_file.read_text()

        # Find category section and append
        if f"## {category}" in current:
            idx = current.find(f"## {category}") + len(f"## {category}")
            next_section = current.find("\n## ", idx)
            if next_section == -1:
                current = current + new_entry
            else:
                current = current[:next_section] + new_entry + current[next_section:]
        else:
            current = current + f"\n## {category}\n{new_entry}"

        learnings_file.write_text(current)

    def log_to_daily(self, message: str):
        """Log to today's daily file in v1"""
        if not self.v1_exists:
            return

        today = datetime.utcnow().strftime("%Y-%m-%d")
        daily_file = self.v1_home / "memory" / "daily" / f"{today}.md"

        if daily_file.exists():
            now = datetime.utcnow().strftime("%H:%M")
            entry = f"\n**{now}**: {message}\n"
            with open(daily_file, "a") as f:
                f.write(entry)

    # ─────────────────────────────────────────────────────────────────────────
    # BILLING
    # ─────────────────────────────────────────────────────────────────────────

    def get_billing_rates(self) -> Dict[str, Any]:
        """Get billing rates from v1 or v2"""
        # Try v1 first
        if self.v1_exists:
            v1_rates = self.v1_home / "billing" / "rates.yaml"
            if v1_rates.exists():
                return yaml.safe_load(v1_rates.read_text()) or {}

        # Fall back to v2
        v2_rates = self.v2_home / "config" / "gateway.json"
        if v2_rates.exists():
            config = json.loads(v2_rates.read_text())
            return config.get("billing", {})

        # Default rates
        return {
            "client_rates": {
                "standard": {
                    "input_per_million": 6.25,
                    "output_per_million": 31.25
                }
            }
        }

    def sync_billing_session(self, session_data: Dict[str, Any]):
        """Sync a billing session to v1"""
        if not self.v1_exists:
            return

        session_id = session_data.get("session_id", "unknown")
        session_file = self.v1_home / "billing" / "sessions" / f"{session_id}.yaml"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(yaml.dump(session_data, default_flow_style=False))

    # ─────────────────────────────────────────────────────────────────────────
    # CLIENTS
    # ─────────────────────────────────────────────────────────────────────────

    def get_v1_clients(self) -> Dict[str, Any]:
        """Get clients from v1 clients directory"""
        clients = {}
        if self.v1_exists:
            clients_dir = self.v1_home / "clients"
            if clients_dir.exists():
                for client_file in clients_dir.glob("*/context.yaml"):
                    client_id = client_file.parent.name
                    clients[client_id] = yaml.safe_load(client_file.read_text())
        return clients

    def sync_client_to_v1(self, client_id: str, client_data: Dict[str, Any]):
        """Sync a client configuration to v1"""
        if not self.v1_exists:
            return

        client_dir = self.v1_home / "clients" / client_id
        client_dir.mkdir(parents=True, exist_ok=True)

        context_file = client_dir / "context.yaml"
        context_file.write_text(yaml.dump(client_data, default_flow_style=False))

    # ─────────────────────────────────────────────────────────────────────────
    # AUDIT
    # ─────────────────────────────────────────────────────────────────────────

    def log_audit(self, action: str, details: Dict[str, Any]):
        """Log to both v1 and v2 audit logs"""
        timestamp = datetime.utcnow().isoformat()

        # v2 log (JSONL)
        v2_log = self.v2_home / "audit" / "logs" / "bridge.jsonl"
        v2_log.parent.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": timestamp, "action": action, **details}
        with open(v2_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # v1 log (plain text)
        if self.v1_exists:
            v1_log = self.v1_home / "logs" / "audit" / "audit.log"
            if v1_log.parent.exists():
                v1_entry = f"[{timestamp}] ACTION={action}"
                for k, v in details.items():
                    v1_entry += f" {k.upper()}={v}"
                v1_entry += "\n"
                with open(v1_log, "a") as f:
                    f.write(v1_entry)

    # ─────────────────────────────────────────────────────────────────────────
    # SKILLS
    # ─────────────────────────────────────────────────────────────────────────

    def get_skills(self) -> Dict[str, Any]:
        """Get skills index from v1"""
        if self.v1_exists:
            skills_index = self.v1_home / "skills" / "SKILL_INDEX.yaml"
            if skills_index.exists():
                return yaml.safe_load(skills_index.read_text()) or {}
        return {}

    def register_skill(self, skill_name: str, skill_data: Dict[str, Any]):
        """Register a skill in v1"""
        if not self.v1_exists:
            return

        skills_index = self.v1_home / "skills" / "SKILL_INDEX.yaml"
        if not skills_index.exists():
            return

        index = yaml.safe_load(skills_index.read_text()) or {"skills": {}}
        index["skills"][skill_name] = skill_data
        skills_index.write_text(yaml.dump(index, default_flow_style=False))

    # ─────────────────────────────────────────────────────────────────────────
    # STATUS
    # ─────────────────────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get combined status from v1 and v2"""
        status = {
            "v1_exists": self.v1_exists,
            "v2_path": str(self.v2_home),
            "secrets_available": [],
            "v1_components": [],
            "v2_components": []
        }

        # Check secrets
        for secret in ["NVIDIA_API_KEY", "TELEGRAM_BOT_TOKEN", "OPENROUTER_API_KEY"]:
            if self.get_secret(secret):
                status["secrets_available"].append(secret)

        # Check v1 components
        if self.v1_exists:
            v1_components = [
                ("memory/identity.yaml", "identity"),
                ("memory/learnings.md", "learnings"),
                ("memory/current_objectives.yaml", "objectives"),
                ("billing/rates.yaml", "billing"),
                ("skills/SKILL_INDEX.yaml", "skills")
            ]
            for path, name in v1_components:
                if (self.v1_home / path).exists():
                    status["v1_components"].append(name)

        # Check v2 components
        v2_components = [
            ("core/security/trust_gate.py", "security"),
            ("core/agents/base.py", "agents"),
            ("core/queue/lane_queue.py", "queue"),
            ("core/gateway/gateway.py", "gateway"),
            ("memory/hybrid_memory.py", "memory"),
            ("tools/sandbox/executor.py", "sandbox"),
            ("audit/alerts/alert_manager.py", "alerts")
        ]
        for path, name in v2_components:
            if (self.v2_home / path).exists():
                status["v2_components"].append(name)

        return status


# Singleton instance
bridge = ATLASBridge()
