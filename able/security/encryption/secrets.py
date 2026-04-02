"""
Encrypted Secrets Manager

AES-256-GCM encryption for secrets with secure key derivation.
Supports key rotation, TTL, and audit logging.
"""

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# Try cryptography library
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.warning("cryptography not installed - using fallback encryption")


@dataclass
class SecretEntry:
    """Encrypted secret entry"""
    key: str
    encrypted_value: bytes
    nonce: bytes
    salt: bytes
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        self.metadata = self.metadata or {}


class SecretsManager:
    """
    Secure secrets storage with AES-256-GCM encryption.

    Features:
    - AES-256-GCM authenticated encryption
    - PBKDF2 key derivation with random salts
    - TTL support for auto-expiring secrets
    - Key rotation support
    - Audit logging of access
    """

    def __init__(
        self,
        secrets_dir: Optional[Path] = None,
        master_key_env: str = "ABLE_MASTER_KEY",
    ):
        self.secrets_dir = secrets_dir or Path.home() / ".able" / ".secrets"
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secrets_dir, 0o700)

        self.master_key_env = master_key_env
        self._secrets_cache: Dict[str, SecretEntry] = {}
        self._master_key: Optional[bytes] = None

        # Secrets index file
        self.index_file = self.secrets_dir / "secrets.enc"

        # Audit log
        self.audit_file = self.secrets_dir / "access.log"

    def _get_master_key(self) -> bytes:
        """Get or generate master encryption key"""
        if self._master_key:
            return self._master_key

        # Try environment variable
        key_str = os.environ.get(self.master_key_env)

        if key_str:
            # Derive key from provided string
            self._master_key = hashlib.sha256(key_str.encode()).digest()
        else:
            # Generate and store key file
            key_file = self.secrets_dir / ".master_key"

            if key_file.exists():
                self._master_key = key_file.read_bytes()
            else:
                self._master_key = secrets.token_bytes(32)
                key_file.write_bytes(self._master_key)
                os.chmod(key_file, 0o600)
                logger.info("Generated new master key")

        return self._master_key

    def _derive_key(self, salt: bytes) -> bytes:
        """Derive encryption key using PBKDF2"""
        master = self._get_master_key()

        if HAS_CRYPTO:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100_000,
                backend=default_backend(),
            )
            return kdf.derive(master)
        else:
            # Fallback: simple HMAC-based derivation
            import hmac
            return hmac.new(master, salt, hashlib.sha256).digest()

    def _encrypt(self, plaintext: bytes, key: bytes) -> tuple[bytes, bytes]:
        """Encrypt data with AES-256-GCM"""
        if HAS_CRYPTO:
            nonce = secrets.token_bytes(12)
            aesgcm = AESGCM(key)
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            return ciphertext, nonce
        else:
            # Fallback: XOR with key (NOT secure - install cryptography!)
            nonce = secrets.token_bytes(12)
            key_stream = hashlib.sha256(key + nonce).digest()
            encrypted = bytes(p ^ k for p, k in zip(plaintext, key_stream * (len(plaintext) // 32 + 1)))
            return encrypted, nonce

    def _decrypt(self, ciphertext: bytes, key: bytes, nonce: bytes) -> bytes:
        """Decrypt data with AES-256-GCM"""
        if HAS_CRYPTO:
            aesgcm = AESGCM(key)
            return aesgcm.decrypt(nonce, ciphertext, None)
        else:
            # Fallback: XOR with key
            key_stream = hashlib.sha256(key + nonce).digest()
            decrypted = bytes(c ^ k for c, k in zip(ciphertext, key_stream * (len(ciphertext) // 32 + 1)))
            return decrypted

    async def store(
        self,
        key: str,
        value: str,
        ttl_hours: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store an encrypted secret"""
        try:
            # Generate salt and derive key
            salt = secrets.token_bytes(16)
            derived_key = self._derive_key(salt)

            # Encrypt
            plaintext = value.encode()
            ciphertext, nonce = self._encrypt(plaintext, derived_key)

            # Calculate expiry
            expires_at = None
            if ttl_hours:
                expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)

            # Create entry
            entry = SecretEntry(
                key=key,
                encrypted_value=ciphertext,
                nonce=nonce,
                salt=salt,
                created_at=datetime.utcnow(),
                expires_at=expires_at,
                metadata=metadata or {},
            )

            self._secrets_cache[key] = entry
            await self._save_index()
            await self._audit_log("STORE", key)

            logger.info(f"Stored secret: {key}")
            return True

        except Exception as e:
            logger.error(f"Failed to store secret {key}: {e}")
            return False

    async def retrieve(self, key: str) -> Optional[str]:
        """Retrieve and decrypt a secret"""
        await self._load_index()

        if key not in self._secrets_cache:
            logger.warning(f"Secret not found: {key}")
            return None

        entry = self._secrets_cache[key]

        # Check expiry
        if entry.expires_at and datetime.utcnow() > entry.expires_at:
            logger.warning(f"Secret expired: {key}")
            await self.delete(key)
            return None

        try:
            # Derive key and decrypt
            derived_key = self._derive_key(entry.salt)
            plaintext = self._decrypt(entry.encrypted_value, derived_key, entry.nonce)

            # Update access metadata
            entry.last_accessed = datetime.utcnow()
            entry.access_count += 1
            await self._save_index()
            await self._audit_log("RETRIEVE", key)

            return plaintext.decode()

        except Exception as e:
            logger.error(f"Failed to decrypt secret {key}: {e}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete a secret"""
        if key in self._secrets_cache:
            del self._secrets_cache[key]
            await self._save_index()
            await self._audit_log("DELETE", key)
            logger.info(f"Deleted secret: {key}")
            return True
        return False

    async def list_keys(self) -> List[Dict[str, Any]]:
        """List all secret keys (not values)"""
        await self._load_index()

        return [
            {
                "key": entry.key,
                "created_at": entry.created_at.isoformat(),
                "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                "access_count": entry.access_count,
                "metadata": entry.metadata,
            }
            for entry in self._secrets_cache.values()
        ]

    async def rotate_key(self, old_key: str, new_key: str) -> bool:
        """Rotate a secret key (re-encrypt with new salt)"""
        value = await self.retrieve(old_key)
        if value is None:
            return False

        # Get original metadata
        entry = self._secrets_cache[old_key]
        metadata = entry.metadata.copy()
        metadata["rotated_from"] = old_key
        metadata["rotated_at"] = datetime.utcnow().isoformat()

        # Store with new key
        success = await self.store(new_key, value, metadata=metadata)

        if success:
            await self.delete(old_key)
            await self._audit_log("ROTATE", f"{old_key} -> {new_key}")

        return success

    async def import_env_file(self, env_file: Path) -> int:
        """Import secrets from .env file"""
        if not env_file.exists():
            return 0

        count = 0
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")

                    await self.store(key, value, metadata={"source": str(env_file)})
                    count += 1

        return count

    async def export_to_env(self) -> Dict[str, str]:
        """Export all secrets as environment variables dict"""
        await self._load_index()

        result = {}
        for key in self._secrets_cache:
            value = await self.retrieve(key)
            if value:
                result[key] = value

        return result

    async def _save_index(self) -> None:
        """Save encrypted index to disk"""
        # Serialize index
        index_data = {}
        for key, entry in self._secrets_cache.items():
            index_data[key] = {
                "encrypted_value": base64.b64encode(entry.encrypted_value).decode(),
                "nonce": base64.b64encode(entry.nonce).decode(),
                "salt": base64.b64encode(entry.salt).decode(),
                "created_at": entry.created_at.isoformat(),
                "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                "last_accessed": entry.last_accessed.isoformat() if entry.last_accessed else None,
                "access_count": entry.access_count,
                "metadata": entry.metadata,
            }

        # Encrypt and save
        plaintext = json.dumps(index_data).encode()
        salt = secrets.token_bytes(16)
        key = self._derive_key(salt)
        ciphertext, nonce = self._encrypt(plaintext, key)

        # Write: salt + nonce + ciphertext
        with open(self.index_file, "wb") as f:
            f.write(salt + nonce + ciphertext)

        os.chmod(self.index_file, 0o600)

    async def _load_index(self) -> None:
        """Load encrypted index from disk"""
        if not self.index_file.exists():
            return

        if self._secrets_cache:
            return  # Already loaded

        try:
            with open(self.index_file, "rb") as f:
                data = f.read()

            # Extract: salt (16) + nonce (12) + ciphertext
            salt = data[:16]
            nonce = data[16:28]
            ciphertext = data[28:]

            # Decrypt
            key = self._derive_key(salt)
            plaintext = self._decrypt(ciphertext, key, nonce)
            index_data = json.loads(plaintext.decode())

            # Rebuild cache
            for key, entry_data in index_data.items():
                self._secrets_cache[key] = SecretEntry(
                    key=key,
                    encrypted_value=base64.b64decode(entry_data["encrypted_value"]),
                    nonce=base64.b64decode(entry_data["nonce"]),
                    salt=base64.b64decode(entry_data["salt"]),
                    created_at=datetime.fromisoformat(entry_data["created_at"]),
                    expires_at=datetime.fromisoformat(entry_data["expires_at"]) if entry_data.get("expires_at") else None,
                    last_accessed=datetime.fromisoformat(entry_data["last_accessed"]) if entry_data.get("last_accessed") else None,
                    access_count=entry_data.get("access_count", 0),
                    metadata=entry_data.get("metadata", {}),
                )

        except Exception as e:
            logger.error(f"Failed to load secrets index: {e}")

    async def _audit_log(self, action: str, target: str) -> None:
        """Log secret access"""
        timestamp = datetime.utcnow().isoformat()
        log_line = f"[{timestamp}] ACTION={action} TARGET={target}\n"

        with open(self.audit_file, "a") as f:
            f.write(log_line)


# Convenience functions
_manager: Optional[SecretsManager] = None


def get_secrets_manager() -> SecretsManager:
    """Get global secrets manager instance"""
    global _manager
    if _manager is None:
        _manager = SecretsManager()
    return _manager


async def get_secret(key: str) -> Optional[str]:
    """Convenience function to get a secret"""
    return await get_secrets_manager().retrieve(key)


async def set_secret(key: str, value: str, ttl_hours: Optional[int] = None) -> bool:
    """Convenience function to set a secret"""
    return await get_secrets_manager().store(key, value, ttl_hours=ttl_hours)
