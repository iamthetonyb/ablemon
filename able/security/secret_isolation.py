"""
Process-Level Secret Isolation — ThePopeBot Pattern.

"The AI literally cannot access your secrets, even if it tries."

Secrets are filtered at the process level before the agent shell starts.
Only explicitly allowed secrets are injected at the moment of use.

Usage:
    from able.security.secret_isolation import SecretIsolation

    # Create isolated environment for subprocess
    env = SecretIsolation.create_isolated_env()

    # Run command with only specific secrets allowed
    result = await SecretIsolation.run_isolated(
        "curl -H 'Authorization: Bearer $GITHUB_TOKEN' https://api.github.com/user",
        allowed_secrets=["GITHUB_TOKEN"]
    )
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class SecretIsolation:
    """
    Process-level secret isolation for secure subprocess execution.

    Principles:
    1. Default deny: No secrets in subprocess environment unless explicitly allowed
    2. Lazy injection: Secrets loaded only at moment of use
    3. Audit trail: All secret access is logged
    4. Rotation support: Suspected compromise triggers rotation
    """

    # Patterns that indicate a secret/credential
    DANGEROUS_PATTERNS: Set[str] = {
        "API_KEY",
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "CREDENTIAL",
        "PRIVATE_KEY",
        "PASSPHRASE",
        "AUTH",
        # Provider-specific
        "AWS_",
        "AZURE_",
        "GCP_",
        "GITHUB_TOKEN",
        "ANTHROPIC_",
        "OPENAI_",
        "NVIDIA_",
        "OPENROUTER_",
        "TELEGRAM_BOT",
        "DISCORD_BOT",
        "SLACK_BOT",
        "NOTION_",
        # Database
        "DATABASE_URL",
        "REDIS_URL",
        "MONGO_URI",
        "POSTGRES_",
        "MYSQL_",
    }

    # Safe environment variables (always allowed)
    SAFE_VARS: Set[str] = {
        "HOME",
        "USER",
        "PATH",
        "SHELL",
        "TERM",
        "LANG",
        "LC_ALL",
        "TZ",
        "PWD",
        "OLDPWD",
        "HOSTNAME",
        "EDITOR",
        "VISUAL",
        # Python
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        # Node
        "NODE_PATH",
        "NPM_CONFIG_PREFIX",
        # Common tools
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    }

    # Audit log path
    _audit_log: Optional[Path] = None

    @classmethod
    def set_audit_log(cls, path: Path):
        """Set the audit log path for secret access logging"""
        cls._audit_log = Path(path).expanduser().resolve()
        cls._audit_log.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _log_access(cls, action: str, secrets: List[str], command: Optional[str] = None):
        """Log secret access for audit trail"""
        if not cls._audit_log:
            return

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": action,
            "secrets": secrets,
        }
        if command:
            # Redact the actual command to avoid logging secrets
            entry["command_pattern"] = re.sub(
                r"(Bearer |token=|key=|password=)[^\s&]+",
                r"\1[REDACTED]",
                command,
                flags=re.IGNORECASE,
            )

        try:
            with open(cls._audit_log, "a") as f:
                import json
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write secret audit log: {e}")

    @classmethod
    def is_dangerous_var(cls, var_name: str) -> bool:
        """Check if a variable name looks like a secret"""
        var_upper = var_name.upper()
        return any(pattern in var_upper for pattern in cls.DANGEROUS_PATTERNS)

    @classmethod
    def create_isolated_env(
        cls,
        allowed_vars: Optional[List[str]] = None,
        include_safe: bool = True,
    ) -> Dict[str, str]:
        """
        Create an environment dict with secrets filtered out.

        Args:
            allowed_vars: Specific variables to include (even if dangerous)
            include_safe: Include SAFE_VARS (default True)

        Returns:
            Filtered environment dict safe for subprocess
        """
        allowed_set = set(allowed_vars or [])
        env = {}

        for key, value in os.environ.items():
            # Always include if explicitly allowed
            if key in allowed_set:
                env[key] = value
                continue

            # Include safe vars
            if include_safe and key in cls.SAFE_VARS:
                env[key] = value
                continue

            # Exclude if dangerous
            if cls.is_dangerous_var(key):
                logger.debug(f"Filtered dangerous var from env: {key}")
                continue

            # Include everything else
            env[key] = value

        return env

    @classmethod
    async def get_secret(cls, name: str) -> Optional[str]:
        """
        Get a secret value with audit logging.

        Checks in order:
        1. Environment variable
        2. ~/.able/.secrets/{name}
        3. Encrypted secrets store (if available)
        """
        cls._log_access("get_secret", [name])

        # 1. Check environment
        if value := os.environ.get(name):
            return value

        # 2. Check secrets directory
        secrets_dir = Path("~/.able/.secrets").expanduser()
        secret_file = secrets_dir / name
        if secret_file.exists():
            return secret_file.read_text().strip()

        # 3. Check encrypted store
        try:
            from able.security.encryption.secrets import get_secret as get_encrypted_secret
            return await get_encrypted_secret(name)
        except ImportError:
            pass

        return None

    @classmethod
    async def run_isolated(
        cls,
        cmd: str,
        allowed_secrets: Optional[List[str]] = None,
        cwd: Optional[Path] = None,
        timeout: float = 60.0,
    ) -> str:
        """
        Run a command in an isolated subprocess with filtered environment.

        Only explicitly allowed secrets are available to the subprocess.

        Args:
            cmd: Shell command to run
            allowed_secrets: List of secret names to inject (e.g., ["GITHUB_TOKEN"])
            cwd: Working directory
            timeout: Command timeout in seconds

        Returns:
            Command stdout

        Raises:
            asyncio.TimeoutError: If command times out
            subprocess.CalledProcessError: If command fails
        """
        # Create isolated environment
        env = cls.create_isolated_env()

        # Inject only explicitly allowed secrets
        if allowed_secrets:
            cls._log_access("inject_secrets", allowed_secrets, command=cmd)
            for secret_name in allowed_secrets:
                if secret_value := await cls.get_secret(secret_name):
                    env[secret_name] = secret_value

        # Run in subprocess
        proc = await asyncio.create_subprocess_shell(
            cmd,
            env=env,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RuntimeError(f"Command failed (exit {proc.returncode}): {error_msg}")

        return stdout.decode()

    @classmethod
    def scan_for_leaked_secrets(cls, text: str) -> List[str]:
        """
        Scan text for potentially leaked secrets.

        Returns list of detected secret patterns.
        """
        leaked = []

        patterns = [
            # API keys
            (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Anthropic API key"),
            (r"nvapi-[a-zA-Z0-9-]+", "NVIDIA API key"),
            (r"ghp_[a-zA-Z0-9]{36}", "GitHub personal access token"),
            (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth token"),
            (r"github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}", "GitHub fine-grained PAT"),
            # AWS
            (r"AKIA[0-9A-Z]{16}", "AWS access key ID"),
            (r"[a-zA-Z0-9/+=]{40}", "Potential AWS secret key"),
            # Generic
            (r"['\"][a-zA-Z0-9]{32,}['\"]", "Potential API key in quotes"),
            (r"Bearer\s+[a-zA-Z0-9._-]+", "Bearer token"),
        ]

        for pattern, description in patterns:
            if re.search(pattern, text):
                leaked.append(description)

        return list(set(leaked))

    @classmethod
    async def rotate_secret(cls, name: str, new_value: str):
        """
        Rotate a secret (for use after suspected compromise).

        Stores in encrypted secrets and logs the rotation.
        """
        cls._log_access("rotate_secret", [name])

        try:
            from able.security.encryption.secrets import set_secret
            await set_secret(name, new_value)
            logger.info(f"Secret rotated: {name}")
        except ImportError:
            # Fallback to file-based storage
            secrets_dir = Path("~/.able/.secrets").expanduser()
            secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            secret_file = secrets_dir / name
            secret_file.write_text(new_value)
            secret_file.chmod(0o600)
            logger.info(f"Secret rotated (file-based): {name}")
