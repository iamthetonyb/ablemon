"""
CLI entry point for tenant management.

Usage:
    python -m atlas.core.tenants --onboard --tenant-id "acme-legal" --domain legal --personality "..."
    python -m atlas.core.tenants --list
    python -m atlas.core.tenants --status acme-legal
    python -m atlas.core.tenants --train acme-legal
"""

import argparse
import asyncio
import json
import sys

from .tenant_manager import TenantManager
from .training_scheduler import TenantTrainingScheduler


def main():
    parser = argparse.ArgumentParser(
        prog="atlas.core.tenants",
        description="ATLAS multi-tenant management CLI",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--onboard", action="store_true", help="Onboard a new tenant")
    group.add_argument("--list", action="store_true", help="List all tenants")
    group.add_argument("--status", metavar="TENANT_ID", help="Get tenant status")
    group.add_argument("--train", metavar="TENANT_ID", help="Trigger training for a tenant")

    # Onboard args
    parser.add_argument("--tenant-id", help="Tenant ID (for --onboard)")
    parser.add_argument("--domain", help="Tenant domain (for --onboard)")
    parser.add_argument("--personality", help="Personality brief (for --onboard)")

    args = parser.parse_args()

    if args.onboard:
        if not all([args.tenant_id, args.domain, args.personality]):
            parser.error("--onboard requires --tenant-id, --domain, and --personality")
        asyncio.run(_onboard(args.tenant_id, args.domain, args.personality))
    elif args.list:
        asyncio.run(_list_tenants())
    elif args.status:
        asyncio.run(_status(args.status))
    elif args.train:
        asyncio.run(_train(args.train))


async def _onboard(tenant_id: str, domain: str, personality: str):
    manager = TenantManager()
    try:
        result = await manager.onboard(tenant_id, domain, personality)
        print(f"Tenant '{tenant_id}' onboarded.")
        print(json.dumps(result, indent=2))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


async def _list_tenants():
    manager = TenantManager()
    tenants = await manager.list_tenants()
    if not tenants:
        print("No tenants registered.")
        return
    print(f"{'TENANT ID':<25} {'DOMAIN':<12} {'STATUS':<10} {'CREATED'}")
    print("-" * 70)
    for t in tenants:
        created = t["created_at"][:10] if t.get("created_at") else "?"
        print(f"{t['tenant_id']:<25} {t['domain']:<12} {t['status']:<10} {created}")


async def _status(tenant_id: str):
    manager = TenantManager()
    try:
        status = await manager.get_status(tenant_id)
        print(f"Tenant: {status['tenant_id']}")
        print(f"Domain: {status['domain']}")
        print(f"Status: {status['status']}")
        print(f"Personality: {status['personality']}")
        print(f"Corpus files: {status['corpus_files']}")
        print(f"Has adapter: {status['has_adapter']}")
        print(f"Tier 0: {status['tier_0_enabled']}")
        print(f"Training threshold: {status['training_threshold']}")
        print(f"Billing plan: {status['billing_plan']}")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


async def _train(tenant_id: str):
    scheduler = TenantTrainingScheduler()
    job = await scheduler.trigger_training(tenant_id)
    print(f"Training job for '{tenant_id}':")
    print(json.dumps(job.to_dict(), indent=2))
    if job.status == "blocked":
        sys.exit(1)


if __name__ == "__main__":
    main()
