"""Ed25519 keypair management and contribution signing for federation.

Provides cryptographic signing of contribution packages so receiving
instances can verify authenticity before ingestion.

Key storage: ~/.able/.secrets/federation_ed25519.key (private)
             ~/.able/.secrets/federation_ed25519.pub (public)

Graceful degradation: if neither `cryptography` nor `nacl` is installed,
all operations return safe defaults and log warnings.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ── Library detection ────────────────────────────────────────────────

_CRYPTO_BACKEND: Optional[str] = None

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    _CRYPTO_BACKEND = "cryptography"
except ImportError:
    pass

if _CRYPTO_BACKEND is None:
    try:
        import nacl.signing  # noqa: F401

        _CRYPTO_BACKEND = "nacl"
    except ImportError:
        pass

if _CRYPTO_BACKEND is None:
    logger.warning(
        "Federation crypto: neither 'cryptography' nor 'PyNaCl' installed. "
        "Contributions will NOT be signed or verified. "
        "Install with: pip install cryptography"
    )

# ── Key paths ────────────────────────────────────────────────────────

_DEFAULT_SECRETS_DIR = Path.home() / ".able" / ".secrets"
_PRIVATE_KEY_FILE = "federation_ed25519.key"
_PUBLIC_KEY_FILE = "federation_ed25519.pub"


# ── Public API ───────────────────────────────────────────────────────


def generate_keypair() -> Tuple[bytes, bytes]:
    """Generate a new Ed25519 keypair.

    Returns:
        (private_key_bytes, public_key_bytes) — raw key material.

    Raises:
        RuntimeError: if no crypto backend is available.
    """
    if _CRYPTO_BACKEND == "cryptography":
        private_key = Ed25519PrivateKey.generate()
        priv_bytes = private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        pub_bytes = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        return priv_bytes, pub_bytes

    if _CRYPTO_BACKEND == "nacl":
        import nacl.signing

        signing_key = nacl.signing.SigningKey.generate()
        return bytes(signing_key), bytes(signing_key.verify_key)

    raise RuntimeError(
        "No cryptography library available. Install 'cryptography' or 'PyNaCl'."
    )


def load_or_create_keypair(
    key_dir: Optional[Path] = None,
) -> Tuple[object, bytes]:
    """Load an existing Ed25519 keypair or create one if missing.

    Args:
        key_dir: Directory for key files. Default: ~/.able/.secrets/

    Returns:
        (private_key_object, public_key_bytes).
        The private key type depends on backend:
          - cryptography: Ed25519PrivateKey
          - nacl: nacl.signing.SigningKey
        public_key_bytes is always raw 32-byte public key.

    Raises:
        RuntimeError: if no crypto backend is available.
    """
    if _CRYPTO_BACKEND is None:
        raise RuntimeError(
            "No cryptography library available. Install 'cryptography' or 'PyNaCl'."
        )

    secrets = key_dir or _DEFAULT_SECRETS_DIR
    secrets.mkdir(parents=True, exist_ok=True)

    priv_path = secrets / _PRIVATE_KEY_FILE
    pub_path = secrets / _PUBLIC_KEY_FILE

    if priv_path.exists() and pub_path.exists():
        return _load_keypair(priv_path, pub_path)

    # Generate new keypair
    logger.info("Federation crypto: generating new Ed25519 keypair in %s", secrets)
    priv_bytes, pub_bytes = generate_keypair()

    # Write private key with restrictive permissions
    priv_path.write_bytes(priv_bytes)
    os.chmod(priv_path, 0o600)

    # Public key is safe to share
    pub_path.write_bytes(pub_bytes)
    os.chmod(pub_path, 0o644)

    fp = fingerprint(pub_bytes)
    logger.info("Federation crypto: keypair created — fingerprint %s", fp)

    return _load_private_key(priv_bytes), pub_bytes


def sign_contribution(private_key: object, payload_bytes: bytes) -> bytes:
    """Sign a JSONL payload with the instance's private key.

    Args:
        private_key: Private key object from load_or_create_keypair().
        payload_bytes: Raw bytes of the contribution JSONL content.

    Returns:
        Raw signature bytes (64 bytes for Ed25519).
    """
    if _CRYPTO_BACKEND == "cryptography":
        return private_key.sign(payload_bytes)  # type: ignore[union-attr]

    if _CRYPTO_BACKEND == "nacl":
        import nacl.signing

        signed = private_key.sign(payload_bytes)  # type: ignore[union-attr]
        return signed.signature

    logger.warning("Federation crypto: no backend — returning empty signature")
    return b""


def verify_contribution(
    public_key_bytes: bytes,
    signature: bytes,
    payload_bytes: bytes,
) -> bool:
    """Verify an Ed25519 signature on a contribution payload.

    Args:
        public_key_bytes: Raw 32-byte public key of the signer.
        signature: Raw 64-byte signature.
        payload_bytes: The signed content.

    Returns:
        True if signature is valid, False otherwise.
    """
    if not signature or not public_key_bytes:
        return False

    if _CRYPTO_BACKEND == "cryptography":
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey as PubKey,
        )

        try:
            pub = PubKey.from_public_bytes(public_key_bytes)
            pub.verify(signature, payload_bytes)
            return True
        except (InvalidSignature, ValueError, Exception) as e:
            logger.debug("Federation crypto: verification failed — %s", e)
            return False

    if _CRYPTO_BACKEND == "nacl":
        import nacl.exceptions
        import nacl.signing

        try:
            verify_key = nacl.signing.VerifyKey(public_key_bytes)
            verify_key.verify(payload_bytes, signature)
            return True
        except nacl.exceptions.BadSignatureError:
            logger.debug("Federation crypto: verification failed (nacl)")
            return False
        except Exception as e:
            logger.debug("Federation crypto: verification error — %s", e)
            return False

    logger.warning("Federation crypto: no backend — cannot verify")
    return False


def fingerprint(public_key_bytes: bytes) -> str:
    """Compute a SHA-256 fingerprint of a public key for display.

    Args:
        public_key_bytes: Raw 32-byte Ed25519 public key.

    Returns:
        String like "sha256:abcdef123456..." (first 32 hex chars).
    """
    digest = hashlib.sha256(public_key_bytes).hexdigest()
    return f"sha256:{digest[:32]}"


def is_available() -> bool:
    """Check whether any crypto backend is available."""
    return _CRYPTO_BACKEND is not None


def get_backend() -> Optional[str]:
    """Return the name of the active crypto backend, or None."""
    return _CRYPTO_BACKEND


# ── Internal helpers ─────────────────────────────────────────────────


def _load_keypair(
    priv_path: Path, pub_path: Path
) -> Tuple[object, bytes]:
    """Load keypair from disk."""
    priv_bytes = priv_path.read_bytes()
    pub_bytes = pub_path.read_bytes()

    logger.debug(
        "Federation crypto: loaded keypair — fingerprint %s",
        fingerprint(pub_bytes),
    )
    return _load_private_key(priv_bytes), pub_bytes


def _load_private_key(priv_bytes: bytes) -> object:
    """Reconstruct a private key object from raw bytes."""
    if _CRYPTO_BACKEND == "cryptography":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey as PrivKey,
        )

        return PrivKey.from_private_bytes(priv_bytes)

    if _CRYPTO_BACKEND == "nacl":
        import nacl.signing

        return nacl.signing.SigningKey(priv_bytes)

    raise RuntimeError("No crypto backend available")


def encode_b64(data: bytes) -> str:
    """Base64-encode bytes to a string (for JSONL embedding)."""
    return base64.b64encode(data).decode("ascii")


def decode_b64(data: str) -> bytes:
    """Decode a base64 string back to bytes."""
    return base64.b64decode(data)
