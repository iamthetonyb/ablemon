"""
Client Manager - Handles isolated client bot instances
Each client gets their own bot that reports to the master
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
import asyncio

@dataclass
class ClientConfig:
    client_id: str
    name: str
    telegram_bot_token: str
    trust_tier: int = 1  # Start at L1 (observe only)
    created_at: datetime = field(default_factory=datetime.utcnow)

    # Isolation settings
    isolated_memory: bool = True
    isolated_skills: bool = True

    # Permissions (graduated)
    can_execute_commands: bool = False
    can_create_files: bool = False
    can_make_api_calls: bool = False
    can_send_without_approval: bool = False

    # Audit settings
    log_all_messages: bool = True
    log_all_tool_calls: bool = True
    sync_to_master: bool = True

    # Rate limits
    max_messages_per_hour: int = 100
    max_tokens_per_day: int = 100000

@dataclass
class ClientSession:
    session_id: str
    client_id: str
    started_at: datetime
    messages: List[Dict] = field(default_factory=list)
    tokens_used: int = 0
    actions_taken: List[Dict] = field(default_factory=list)

class ClientRegistry:
    """Manages all client configurations"""

    def __init__(self, registry_path: str = "clients/registry"):
        self.registry_path = Path(registry_path)
        self.registry_path.mkdir(parents=True, exist_ok=True)
        self.clients: Dict[str, ClientConfig] = {}
        self._load_all()

    def _load_all(self):
        """Load all client configs from disk"""
        for config_file in self.registry_path.glob("*.json"):
            try:
                with open(config_file) as f:
                    data = json.load(f)
                    # Handle datetime conversion
                    if 'created_at' in data and isinstance(data['created_at'], str):
                        data['created_at'] = datetime.fromisoformat(data['created_at'])
                    client = ClientConfig(**data)
                    self.clients[client.client_id] = client
            except Exception as e:
                print(f"Error loading client config {config_file}: {e}")

    def add_client(self, config: ClientConfig) -> bool:
        """Register a new client"""
        if config.client_id in self.clients:
            return False

        self.clients[config.client_id] = config

        # Save to disk
        config_file = self.registry_path / f"{config.client_id}.json"
        with open(config_file, "w") as f:
            json.dump({
                "client_id": config.client_id,
                "name": config.name,
                "telegram_bot_token": config.telegram_bot_token,
                "trust_tier": config.trust_tier,
                "created_at": config.created_at.isoformat(),
                "can_execute_commands": config.can_execute_commands,
                "can_create_files": config.can_create_files,
                "can_make_api_calls": config.can_make_api_calls,
                "can_send_without_approval": config.can_send_without_approval,
                "max_messages_per_hour": config.max_messages_per_hour,
                "max_tokens_per_day": config.max_tokens_per_day
            }, f, indent=2)

        # Create client directories
        client_dirs = [
            f"clients/bots/{config.client_id}",
            f"clients/transcripts/{config.client_id}",
            f"memory/clients/{config.client_id}",
            f"audit/logs/clients/{config.client_id}"
        ]
        for dir_path in client_dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)

        return True

    def get_client(self, client_id: str) -> Optional[ClientConfig]:
        return self.clients.get(client_id)

    def upgrade_trust(self, client_id: str, new_tier: int) -> bool:
        """Upgrade client trust tier (requires audit review)"""
        if client_id not in self.clients:
            return False

        client = self.clients[client_id]
        old_tier = client.trust_tier
        client.trust_tier = new_tier

        # Unlock permissions based on tier
        if new_tier >= 2:
            client.can_create_files = True
        if new_tier >= 3:
            client.can_execute_commands = True
            client.can_make_api_calls = True
        if new_tier >= 4:
            client.can_send_without_approval = True

        # Log the upgrade
        audit_file = Path("audit/logs/trust_upgrades.jsonl")
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_file, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.utcnow().isoformat(),
                "client_id": client_id,
                "old_tier": old_tier,
                "new_tier": new_tier
            }) + "\n")

        return True


class ClientTranscriptManager:
    """
    Manages conversation transcripts for all clients.
    Syncs to master for auditing.
    """

    def __init__(self, base_path: str = "clients/transcripts"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def log_message(self, client_id: str, message: Dict):
        """Log a message to client transcript"""
        transcript_file = self.base_path / client_id / f"{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        transcript_file.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "client_id": client_id,
            **message
        }

        with open(transcript_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def get_recent_messages(self, client_id: str, limit: int = 50) -> List[Dict]:
        """Get recent messages for a client"""
        transcript_dir = self.base_path / client_id
        if not transcript_dir.exists():
            return []

        messages = []
        for transcript_file in sorted(transcript_dir.glob("*.jsonl"), reverse=True):
            with open(transcript_file) as f:
                for line in f:
                    messages.append(json.loads(line))
                    if len(messages) >= limit:
                        return messages
        return messages

    def sync_to_master(self, client_id: str) -> Dict:
        """Sync client transcripts to master audit log"""
        messages = self.get_recent_messages(client_id, limit=1000)

        master_sync_file = Path("audit/logs/master_sync.jsonl")
        master_sync_file.parent.mkdir(parents=True, exist_ok=True)
        with open(master_sync_file, "a") as f:
            for msg in messages:
                f.write(json.dumps({
                    "sync_timestamp": datetime.utcnow().isoformat(),
                    "source_client": client_id,
                    "message": msg
                }) + "\n")

        return {
            "synced_count": len(messages),
            "client_id": client_id,
            "timestamp": datetime.utcnow().isoformat()
        }
