"""
Encryption and Secrets Management

AES-256-GCM encryption for secrets with secure key derivation.
"""

from .secrets import (
    SecretsManager,
    SecretEntry,
    get_secrets_manager,
    get_secret,
    set_secret,
)

__all__ = [
    "SecretsManager",
    "SecretEntry",
    "get_secrets_manager",
    "get_secret",
    "set_secret",
]
