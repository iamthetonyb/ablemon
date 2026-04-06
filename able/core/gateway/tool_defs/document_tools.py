"""
Document ingestion tool definitions and handlers.

Provides the `ingest_document` tool — callable by Able to ingest PDFs,
URLs, or raw text into ABLE's vector memory.

Triggered by:
  - Onboarding: "upload a PDF", "add my company docs"
  - Mid-session: "read this page", "save this for later", "ingest this doc"
  - Telegram: user sends a PDF file → gateway downloads → calls this tool

No availability check needed — DocumentIngester works offline (hash embeddings
when no OpenAI/Ollama is available).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from able.core.gateway.tool_registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


# ── Tool Definitions ────────────────────────────────────────────────────────

INGEST_DOCUMENT = {
    "type": "function",
    "function": {
        "name": "ingest_document",
        "description": (
            "Ingest a document (PDF file path, URL, or raw text) into ABLE's vector memory. "
            "After ingestion, the content surfaces in semantic recall — use this when the user "
            "uploads a file or asks you to 'remember' an external resource. "
            "Supports: local PDF paths, http/https URLs, raw text strings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "What to ingest. One of:\n"
                        "  - Absolute path to a PDF file (e.g. /tmp/report.pdf)\n"
                        "  - HTTP/HTTPS URL (e.g. https://example.com/docs/api)\n"
                        "  - Raw text content to store directly"
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Optional human-readable title for this document (used in recall results).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for filtering (e.g. ['onboarding', 'reference', 'client:acme'])",
                },
            },
            "required": ["source"],
        },
    },
}

LIST_DOCUMENTS = {
    "type": "function",
    "function": {
        "name": "list_documents",
        "description": "List all documents ingested into ABLE's vector memory.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

DELETE_DOCUMENT = {
    "type": "function",
    "function": {
        "name": "delete_document",
        "description": "Remove an ingested document from ABLE's vector memory by its doc_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Document ID from list_documents output.",
                }
            },
            "required": ["doc_id"],
        },
    },
}


# ── Handlers ────────────────────────────────────────────────────────────────

async def handle_ingest_document(args: dict, ctx: "ToolContext") -> str:
    from able.memory.document_ingestion import DocumentIngester

    source: str = args.get("source", "").strip()
    title: str = args.get("title", "")
    tags: list = args.get("tags", [])

    if not source:
        return "Error: `source` is required."

    metadata = {"tags": tags} if tags else {}
    if ctx.user_id:
        metadata["user_id"] = ctx.user_id

    ingester = DocumentIngester()

    # Route by source type
    if source.startswith(("http://", "https://")):
        logger.info("Ingesting URL: %s", source)
        result = ingester.ingest_url(source, metadata=metadata, title=title or None)
    elif source.endswith(".pdf") or _looks_like_path(source):
        logger.info("Ingesting PDF: %s", source)
        result = ingester.ingest_pdf(source, metadata=metadata, title=title or None)
    else:
        # Treat as raw text
        if len(source) < 100:
            return f"Error: source looks like a path or URL but wasn't recognised. Got: {source!r}"
        logger.info("Ingesting raw text (%d chars)", len(source))
        result = ingester.ingest_text(
            source, source="manual", metadata=metadata, title=title or "Manual text"
        )

    if result.ok:
        tag_str = f" tags={tags}" if tags else ""
        return (
            f"Ingested **{result.title}**{tag_str}\n"
            f"- Chunks stored: {result.chunk_count}\n"
            f"- Total chars: {result.total_chars:,}\n"
            f"- Doc ID: `{result.doc_id}`\n\n"
            "Content is now searchable in memory recall."
        )
    else:
        errors = "\n".join(f"  - {e}" for e in result.errors)
        return f"Ingestion failed for `{result.source}`:\n{errors}"


async def handle_list_documents(args: dict, ctx: "ToolContext") -> str:
    from able.memory.document_ingestion import DocumentIngester

    ingester = DocumentIngester()
    docs = ingester.list_documents()

    if not docs:
        return "No documents ingested yet. Use `ingest_document` to add resources to memory."

    lines = [f"**Ingested documents ({len(docs)}):**\n"]
    for doc in docs:
        tag = f" `{doc['source_type']}`" if doc.get("source_type") else ""
        lines.append(f"- **{doc['title']}**{tag} — `{doc['doc_id']}`")
        if doc.get("source"):
            lines.append(f"  {doc['source']}")
    return "\n".join(lines)


async def handle_delete_document(args: dict, ctx: "ToolContext") -> str:
    from able.memory.document_ingestion import DocumentIngester

    doc_id = args.get("doc_id", "").strip()
    if not doc_id:
        return "Error: `doc_id` is required. Use `list_documents` to find it."

    ingester = DocumentIngester()
    deleted = ingester.delete_document(doc_id)

    if deleted:
        return f"Removed {deleted} chunks for document `{doc_id}`."
    return f"No chunks found for `{doc_id}`. Already deleted or wrong ID."


def _looks_like_path(s: str) -> bool:
    """Heuristic: does this string look like a file path?"""
    import os.path
    return s.startswith(("/", "~/", "./", "../")) or os.path.sep in s


# ── Registration ─────────────────────────────────────────────────────────────

def register(registry: "ToolRegistry") -> None:
    """Register all document tools with the tool registry."""
    registry.register(
        name="ingest_document",
        definition=INGEST_DOCUMENT,
        handler=handle_ingest_document,
        display_name="Ingest Document",
        description="Ingest PDF, URL, or text into vector memory",
        requires_approval=False,
        risk_level="low",
        category="memory",
        read_only=False,
        surface="both",
        artifact_kind="markdown",
        tags=["memory", "document", "pdf", "onboarding"],
    )
    registry.register(
        name="list_documents",
        definition=LIST_DOCUMENTS,
        handler=handle_list_documents,
        display_name="List Documents",
        description="List all ingested documents",
        requires_approval=False,
        risk_level="low",
        category="memory",
        read_only=True,
        surface="both",
        artifact_kind="markdown",
        tags=["memory", "document"],
    )
    registry.register(
        name="delete_document",
        definition=DELETE_DOCUMENT,
        handler=handle_delete_document,
        display_name="Delete Document",
        description="Remove a document from vector memory",
        requires_approval=False,
        risk_level="low",
        category="memory",
        read_only=False,
        surface="both",
        artifact_kind="markdown",
        tags=["memory", "document"],
    )
