#!/usr/bin/env python3
"""
ABLE — Gateway startup script.
Modular Gateway Architecture for the Autonomous Business & Learning Engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent

if __package__ in (None, ""):
    sys.path.insert(0, str(_PACKAGE_DIR.parent))

# Configure logging FIRST — without this, all logger.info/error calls are lost
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
# Quiet noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Load .env for local runs (Docker/CI set env vars directly)
try:
    from dotenv import load_dotenv
    load_dotenv(_PACKAGE_DIR / ".env")
except ImportError:
    pass

async def main():
    # Root the service working directory in the package directory so
    # relative config paths resolve correctly.  This is intentionally
    # deferred to main() so that importing this module (e.g. from
    # ``able chat``) does NOT change the caller's working directory.
    os.chdir(_PACKAGE_DIR)

    from able.core.gateway.gateway import ABLEGateway

    print("=" * 60)
    print("ABLE - Autonomous Business & Learning Engine")
    print("=" * 60)

    # Check required files
    required_files = [
        "config/gateway.json",
    ]

    for f in required_files:
        if not Path(f).exists():
            print(f"Missing required file: {f}")
            sys.exit(1)

    # Check secrets
    secrets_dir = Path(".secrets")
    if not secrets_dir.exists():
        secrets_dir.mkdir(mode=0o700)
        print("Created .secrets directory - add your API keys there")

    # Ensure audit logs directory exists
    audit_dir = Path("audit/logs")
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Start gateway
    gateway = ABLEGateway()
    await gateway.run()

if __name__ == "__main__":
    asyncio.run(main())
