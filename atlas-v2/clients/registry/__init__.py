"""
ATLAS v2 Client Registry
Client configuration and management.
"""

from pathlib import Path
import json

REGISTRY_DIR = Path(__file__).parent

def get_client(client_id: str):
    """Get client configuration"""
    client_file = REGISTRY_DIR / f"{client_id}.json"
    if client_file.exists():
        return json.loads(client_file.read_text())
    return None

def list_clients():
    """List all registered clients"""
    clients = []
    for client_file in REGISTRY_DIR.glob("*.json"):
        if client_file.name != "__init__.py":
            clients.append(client_file.stem)
    return clients
