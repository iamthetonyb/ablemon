"""
CLI for tenant management.

Usage:
  python -m atlas.core.tenants --onboard --tenant-id "acme" --name "ACME" --domain "legal" --personality "..."
  python -m atlas.core.tenants --list
  python -m atlas.core.tenants --list --status active
  python -m atlas.core.tenants --status acme
  python -m atlas.core.tenants --pause acme
  python -m atlas.core.tenants --archive acme
"""

from __future__ import annotations

import argparse
import json
import sys

from atlas.core.tenants.tenant_manager import TenantManager


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="atlas.core.tenants",
        description="ATLAS multi-tenant management CLI",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--onboard", action="store_true", help="Onboard a new tenant")
    group.add_argument("--list", action="store_true", help="List all tenants")
    group.add_argument("--status", metavar="TENANT_ID", help="Show tenant status")
    group.add_argument("--pause", metavar="TENANT_ID", help="Pause a tenant")
    group.add_argument("--archive", metavar="TENANT_ID", help="Archive a tenant")

    # Onboard arguments
    parser.add_argument("--tenant-id", help="Tenant ID (for --onboard)")
    parser.add_argument("--name", help="Tenant name (for --onboard)")
    parser.add_argument("--domain", help="Tenant domain (for --onboard)")
    parser.add_argument("--personality", default="", help="Personality brief (for --onboard)")
    parser.add_argument("--config-dir", default="config/tenants", help="Config directory")
    parser.add_argument("--data-dir", default=None, help="Data directory")

    # Filter for --list
    parser.add_argument(
        "--filter-status",
        dest="filter_status",
        help="Filter tenants by status (for --list)",
    )

    args = parser.parse_args(argv)

    manager = TenantManager(config_dir=args.config_dir, data_dir=args.data_dir)

    if args.onboard:
        if not all([args.tenant_id, args.name, args.domain]):
            parser.error("--onboard requires --tenant-id, --name, and --domain")
        config = manager.onboard(
            tenant_id=args.tenant_id,
            name=args.name,
            domain=args.domain,
            personality=args.personality,
        )
        print(json.dumps(config.to_dict(), indent=2))

    elif args.list:
        tenants = manager.list_tenants(status=args.filter_status)
        for t in tenants:
            print(f"  {t.tenant_id:20s}  {t.status:10s}  {t.name} ({t.domain})")
        if not tenants:
            print("  No tenants found.")

    elif args.status:
        config = manager.get_tenant(args.status)
        if config is None:
            print(f"Tenant not found: {args.status}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(config.to_dict(), indent=2))

    elif args.pause:
        manager.pause_tenant(args.pause)
        print(f"Tenant paused: {args.pause}")

    elif args.archive:
        manager.archive_tenant(args.archive)
        print(f"Tenant archived: {args.archive}")


if __name__ == "__main__":
    main()
