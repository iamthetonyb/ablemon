"""Federation sync orchestrator — contribute local pairs, ingest remote ones.

Runs as a cron job at 3:30am daily, after harvest (2am) and evolution (3am).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


async def federation_sync(
    able_home=None,
    network_repo: Optional[str] = None,
) -> dict:
    """Full federation sync cycle.

    1. Check instance.yaml — bail if network_enabled=False
    2. Load sync cursor (last_sync_cursor)
    3. Export local contribution (pairs since cursor, quality >= 0.85)
    4. Publish to GitHub Releases (or queue to outbox)
    5. Drain outbox of any previously queued contributions
    6. Fetch new contributions from other instances
    7. Ingest all inbox files
    8. Update sync cursor + domains_contributed
    9. Return stats dict
    """
    from able.core.federation.identity import (
        get_instance_config,
        update_sync_cursor,
    )

    stats: dict = {
        "success": False,
        "skipped": False,
        "contributed": 0,
        "ingested_accepted": 0,
        "ingested_rejected": 0,
        "ingested_duplicates": 0,
        "outbox_drained": 0,
        "fetched_files": 0,
    }

    # Step 1: Check enrollment
    config = get_instance_config(able_home)
    if not config.get("instance_id"):
        logger.debug("Federation: not enrolled — skipping sync")
        stats["skipped"] = True
        return stats

    if not config.get("network_enabled", True):
        logger.debug("Federation: network disabled — skipping sync")
        stats["skipped"] = True
        return stats

    instance_id = config["instance_id"]

    # Step 2: Load sync cursor
    cursor_str = config.get("last_sync_cursor")
    since = None
    if cursor_str:
        try:
            since = datetime.fromisoformat(cursor_str)
        except (ValueError, TypeError):
            since = None

    # Step 3: Export local contribution
    try:
        from able.core.distillation.store import DistillationStore
        from able.core.federation.contributor import export_contribution

        store = DistillationStore()
        package = export_contribution(
            store=store,
            instance_id=instance_id,
            since=since,
        )

        contributed_domains: list[str] = []

        if package:
            stats["contributed"] = package.pair_count
            contributed_domains = list(package.domains.keys())

            # Step 4: Publish
            repo = network_repo or config.get("network_repo") or None
            from able.core.federation.distributor import (
                GitHubReleasesBackend,
                drain_outbox,
            )

            backend = GitHubReleasesBackend(repo=repo) if repo else GitHubReleasesBackend()
            url = await backend.publish(package)

            if not url:
                # Contribution stays in outbox for next sync
                logger.info("Federation: contribution queued in outbox")

            # Step 5: Drain outbox
            drained = await drain_outbox(backend)
            stats["outbox_drained"] = drained

            # Step 6: Fetch new contributions
            since_tag = config.get("last_sync_tag")
            downloaded = await backend.fetch_contributions(since=since_tag)
            stats["fetched_files"] = len(downloaded)

        else:
            # No new local pairs — still fetch and ingest
            repo = network_repo or config.get("network_repo") or None
            from able.core.federation.distributor import (
                GitHubReleasesBackend,
                drain_outbox,
            )

            backend = GitHubReleasesBackend(repo=repo) if repo else GitHubReleasesBackend()
            drained = await drain_outbox(backend)
            stats["outbox_drained"] = drained

            since_tag = config.get("last_sync_tag")
            downloaded = await backend.fetch_contributions(since=since_tag)
            stats["fetched_files"] = len(downloaded)

        # Step 7: Ingest inbox
        from able.core.federation.ingester import ingest_all_inbox

        local_domains = config.get("domains_contributed", [])
        ingest_result = ingest_all_inbox(
            store=store,
            local_domains=local_domains,
        )
        stats["ingested_accepted"] = ingest_result.accepted
        stats["ingested_rejected"] = ingest_result.rejected
        stats["ingested_duplicates"] = ingest_result.duplicates

        # Step 8: Update sync cursor
        new_cursor = datetime.now(timezone.utc).isoformat()
        update_sync_cursor(
            cursor=new_cursor,
            domains=contributed_domains if package else None,
            able_home=able_home,
        )

        stats["success"] = True
        logger.info(
            "Federation sync: contributed=%d, ingested=%d, dupes=%d, fetched=%d",
            stats["contributed"],
            stats["ingested_accepted"],
            stats["ingested_duplicates"],
            stats["fetched_files"],
        )

    except Exception as e:
        logger.error("Federation sync failed: %s", e, exc_info=True)
        stats["error"] = str(e)

    return stats
