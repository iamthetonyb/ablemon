#!/usr/bin/env python3
"""
ATLAS v3 Startup Script
Secure Multi-Tenant AI Agent System — Modular Gateway Architecture
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

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

# Ensure we're in the right directory
os.chdir(Path(__file__).parent)

# Add to path — atlas/ itself (for `from core.xxx` imports)
# AND its parent (for `from atlas.core.xxx` imports used by cron jobs,
# harvesters, morning report, and other lazy-imported modules).
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env for local runs (Docker/CI set env vars directly)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from core.gateway.gateway import ATLASGateway

async def main():
    print("=" * 60)
    print("ATLAS v2 - Secure Multi-Tenant AI Agent System")
    print("=" * 60)

    # Check required files
    required_files = [
        "config/gateway.json",
    ]

    for f in required_files:
        if not Path(f).exists():
            print(f"❌ Missing required file: {f}")
            sys.exit(1)

    # Check secrets
    secrets_dir = Path(".secrets")
    if not secrets_dir.exists():
        secrets_dir.mkdir(mode=0o700)
        print("⚠️ Created .secrets directory - add your API keys there")

    # Ensure audit logs directory exists
    audit_dir = Path("audit/logs")
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Start gateway
    gateway = ATLASGateway()
    await gateway.run()

if __name__ == "__main__":
    asyncio.run(main())
