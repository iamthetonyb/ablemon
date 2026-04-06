"""
Wiki Skill — Search and manage ABLE's TriliumNext knowledge base.

Triggers: /wiki, "wiki", "knowledge base", "look up in notes"
Returns markdown-formatted results from Trilium, or creates new entries.

Usage:
    /wiki <topic>           — search the knowledge base
    /wiki add <title>       — create a new note (prompts for content)
    /wiki recent            — show recently modified notes
"""

import asyncio
import logging
from typing import List, Optional

from able.tools.trilium.client import TriliumClient, TriliumNote, KNOWN_PARENTS

logger = logging.getLogger(__name__)


async def wiki_search(query: str, limit: int = 10) -> str:
    """Search Trilium and return formatted markdown results."""
    try:
        async with TriliumClient() as client:
            if not await client.is_available():
                return "_TriliumNext is not running. Start with:_ `docker compose --profile observability up -d`"

            results = await client.search_notes(query, limit=limit)
            if not results:
                return f"No results found for **{query}**."

            lines = [f"**Wiki Results** — {len(results)} matches for _{query}_\n"]
            for note in results:
                content_preview = ""
                try:
                    raw = await client.get_note_content(note.note_id)
                    # Strip HTML tags for preview
                    import re
                    text = re.sub(r"<[^>]+>", "", raw).strip()
                    content_preview = text[:150] + "..." if len(text) > 150 else text
                except Exception:
                    pass

                lines.append(f"- **{note.title}** (`{note.note_id}`)")
                if content_preview:
                    lines.append(f"  _{content_preview}_")
                if note.date_modified:
                    lines.append(f"  Modified: {note.date_modified[:10]}")
                lines.append("")

            lines.append(f"_Browse full KB: http://localhost:8081_")
            return "\n".join(lines)
    except Exception as e:
        logger.error("Wiki search failed: %s", e)
        return f"Wiki search error: {e}"


async def wiki_add(
    title: str,
    content: str,
    parent: str = "knowledge_base",
    tags: Optional[List[str]] = None,
) -> str:
    """Create a new note in the knowledge base."""
    try:
        async with TriliumClient() as client:
            parent_id = KNOWN_PARENTS.get(parent, KNOWN_PARENTS.get("knowledge_base", "root"))
            if not parent_id:
                parent_id = "root"

            # Wrap plain text in HTML if needed
            if not content.strip().startswith("<"):
                content = f"<p>{content}</p>"

            note = await client.create_note(parent_id, title, content)
            for tag in (tags or []):
                await client.create_label(note.note_id, "tag", tag)

            return f"Created note **{title}** (`{note.note_id}`) under {parent}."
    except Exception as e:
        logger.error("Wiki add failed: %s", e)
        return f"Wiki add error: {e}"


async def wiki_recent(limit: int = 10) -> str:
    """Show recently modified notes."""
    try:
        async with TriliumClient() as client:
            # Search notes ordered by modification date
            results = await client.search_notes(
                "#!template orderBy:dateModified desc", limit=limit
            )
            if not results:
                return "No notes found in the knowledge base."

            lines = [f"**Recent Notes** — last {len(results)} modified\n"]
            for note in results:
                modified = note.date_modified[:10] if note.date_modified else "?"
                lines.append(f"- [{note.title}] (`{note.note_id}`) — {modified}")

            return "\n".join(lines)
    except Exception as e:
        logger.error("Wiki recent failed: %s", e)
        return f"Wiki recent error: {e}"


async def wiki_ingest_research(report_data: dict, date_str: str) -> str:
    """
    Ingest a research report into Trilium with cross-references.

    Creates notes for each finding, links related findings via relations,
    and updates the research frontier map. Works alongside the web clipper —
    clipped articles get linked to research findings via topic matching.
    """
    try:
        async with TriliumClient() as client:
            if not await client.is_available():
                return "Trilium not available"

            parent_id = KNOWN_PARENTS.get("weekly_research") or "root"
            findings = report_data.get("findings", [])
            if not findings:
                return "No findings to ingest"

            # Create a summary note for this report
            summary_html = (
                f"<h2>Research Report — {date_str}</h2>"
                f"<p>Findings: {len(findings)} | "
                f"High priority: {report_data.get('high_priority_count', 0)} | "
                f"Queries: {report_data.get('search_queries_run', 0)}</p>"
            )
            summary = await client.create_note(parent_id, f"Report {date_str}", summary_html)
            await client.create_label(summary.note_id, "reportDate", date_str)

            # Create individual finding notes and link them
            created_ids = []
            for f in findings[:20]:
                html = (
                    f"<h3>{f.get('title', '?')}</h3>"
                    f"<p>{f.get('summary', '')}</p>"
                )
                if f.get("url"):
                    html += f'<p><a href="{f["url"]}">{f["url"]}</a></p>'
                if f.get("action"):
                    html += f"<p><b>Action:</b> {f['action']}</p>"

                note = await client.create_note(
                    summary.note_id,
                    f.get("title", "Finding")[:80],
                    html,
                )
                for tag in f.get("tags", []):
                    await client.create_label(note.note_id, "tag", tag)
                await client.create_label(note.note_id, "relevance", f.get("relevance", "medium"))
                await client.create_label(note.note_id, "source", f.get("source", "web"))
                created_ids.append((note.note_id, f.get("tags", [])))

            # Cross-link findings that share tags (relation map building)
            for i, (nid_a, tags_a) in enumerate(created_ids):
                for j, (nid_b, tags_b) in enumerate(created_ids):
                    if j <= i:
                        continue
                    shared = set(tags_a) & set(tags_b)
                    if shared:
                        try:
                            await client.create_relation(nid_a, "relatedTo", nid_b)
                        except Exception:
                            pass

            # Search for web clipper notes that match our research topics
            # and create relations to connect external clips with findings
            for nid, tags in created_ids[:5]:
                for tag in tags[:1]:
                    try:
                        clips = await client.search_notes(
                            f"#clipType note.title *= {tag}", limit=3
                        )
                        for clip in clips:
                            clip_id = clip.note_id if hasattr(clip, "note_id") else clip.get("noteId", "")
                            if clip_id:
                                await client.create_relation(nid, "evidencedBy", clip_id)
                    except Exception:
                        pass

            return f"Ingested {len(created_ids)} findings with cross-references"

    except Exception as e:
        logger.error("Research ingestion failed: %s", e)
        return f"Ingestion error: {e}"


async def handle_wiki_command(args: str) -> str:
    """Route /wiki subcommands."""
    args = args.strip()

    if not args:
        return (
            "**Wiki Commands:**\n"
            "- `/wiki <query>` — search knowledge base\n"
            "- `/wiki add <title>` — create a new note\n"
            "- `/wiki recent` — recently modified notes\n"
            "- `/wiki clips` — list web clipper imports\n"
            "- Browse: http://localhost:8081"
        )

    if args.lower() == "recent":
        return await wiki_recent()

    if args.lower() == "clips":
        return await wiki_search("#clipType")

    if args.lower().startswith("add "):
        title = args[4:].strip()
        return await wiki_add(title, "<p>New note — edit in Trilium.</p>")

    # Default: treat as search query
    return await wiki_search(args)
