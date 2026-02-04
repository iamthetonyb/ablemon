#!/usr/bin/env python3
"""
ATLAS v2 Startup Script
Secure Multi-Tenant AI Agent System
"""

import asyncio
import os
import sys
from pathlib import Path

# Ensure we're in the right directory
os.chdir(Path(__file__).parent)

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

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
