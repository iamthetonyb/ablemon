"""
TriliumNext ETAPI Client
Provides programmatic access to TriliumNext knowledge base.

ETAPI docs: https://triliumnext.github.io/Docs/Wiki/etapi.html
OpenAPI spec: /etapi/app-info for version check

Follows MCPBridge pattern from able/tools/mcp/bridge.py — httpx-based,
async-first, with sync wrappers for cron/CLI use.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


# Load .env if Trilium vars not already in environment
if not os.environ.get("TRILIUM_KB_ROOT"):
    try:
        from dotenv import load_dotenv
        _env_path = Path(__file__).parent.parent.parent / ".env"
        if _env_path.exists():
            load_dotenv(_env_path)
    except ImportError:
        pass

# Well-known parent note IDs — set after initial setup, overridable via env
KNOWN_PARENTS = {
    "root": "root",
    "knowledge_base": os.environ.get("TRILIUM_KB_ROOT", ""),
    "weekly_research": os.environ.get("TRILIUM_WEEKLY_RESEARCH", ""),
    "document_summaries": os.environ.get("TRILIUM_DOC_SUMMARIES", ""),
    "architecture": os.environ.get("TRILIUM_ARCHITECTURE", ""),
    "security": os.environ.get("TRILIUM_SECURITY", ""),
}


@dataclass
class TriliumNote:
    """Parsed note from ETAPI response."""
    note_id: str
    title: str
    type: str
    mime: str
    content: Optional[str] = None
    parent_note_ids: Optional[List[str]] = None
    child_note_ids: Optional[List[str]] = None
    attributes: Optional[List[Dict]] = None
    date_created: Optional[str] = None
    date_modified: Optional[str] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any], content: Optional[str] = None) -> "TriliumNote":
        return cls(
            note_id=data["noteId"],
            title=data.get("title", ""),
            type=data.get("type", "text"),
            mime=data.get("mime", "text/html"),
            content=content,
            parent_note_ids=data.get("parentNoteIds"),
            child_note_ids=data.get("childNoteIds"),
            attributes=data.get("attributes"),
            date_created=data.get("dateCreated"),
            date_modified=data.get("dateModified"),
        )


class TriliumClient:
    """
    Async client for TriliumNext ETAPI.

    Usage:
        client = TriliumClient()
        async with client:
            note = await client.create_note("root", "My Note", "<p>Content</p>")
            results = await client.search_notes("keyword")
    """

    # When running outside Docker, the Docker hostname won't resolve.
    # Fall back to localhost:8081 (the mapped port).
    _LOCAL_FALLBACK = "http://localhost:8081"

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
    ):
        env_url = os.environ.get("TRILIUM_URL", "http://localhost:8081")
        self.base_url = (base_url or env_url).rstrip("/")
        # Auto-fallback: if URL contains Docker hostname, also try localhost
        if "trilium:" in self.base_url and not base_url:
            self._try_local_fallback = True
        else:
            self._try_local_fallback = False
        self.token = token or os.environ.get("TRILIUM_ETAPI_TOKEN", "")
        self._client: Optional[httpx.AsyncClient] = None

        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required: pip install httpx")

    @property
    def etapi_url(self) -> str:
        return f"{self.base_url}/etapi"

    @property
    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = self.token
        return h

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.etapi_url,
            headers=self._headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.etapi_url,
                headers=self._headers,
                timeout=30.0,
            )

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        self._ensure_client()
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            logger.error("ETAPI %s %s → %d: %s", method, path, resp.status_code, resp.text[:300])
            resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text

    # ── App Info ──────────────────────────────────────────────────────────

    async def app_info(self) -> Dict[str, Any]:
        """Get TriliumNext version and instance info."""
        return await self._request("GET", "/app-info")

    async def is_available(self) -> bool:
        """Check if Trilium is reachable. Falls back to localhost if Docker URL fails."""
        try:
            info = await self.app_info()
            return "appVersion" in info
        except Exception:
            if self._try_local_fallback:
                # Docker hostname failed — try localhost
                self.base_url = self._LOCAL_FALLBACK
                self._try_local_fallback = False
                try:
                    info = await self.app_info()
                    if "appVersion" in info:
                        logger.info("Trilium: fell back to %s", self._LOCAL_FALLBACK)
                        return True
                except Exception:
                    pass
            return False

    # ── Notes ─────────────────────────────────────────────────────────────

    async def create_note(
        self,
        parent_note_id: str,
        title: str,
        content: str,
        note_type: str = "text",
        mime: str = "text/html",
        prefix: Optional[str] = None,
    ) -> TriliumNote:
        """Create a new note under the given parent."""
        body: Dict[str, Any] = {
            "parentNoteId": parent_note_id,
            "title": title,
            "type": note_type,
            "content": content,
        }
        if mime != "text/html":
            body["mime"] = mime
        if prefix:
            body["prefix"] = prefix

        data = await self._request("POST", "/create-note", json=body)
        return TriliumNote.from_api(data["note"], content=content)

    async def get_note(self, note_id: str) -> TriliumNote:
        """Get note metadata (without content)."""
        data = await self._request("GET", f"/notes/{note_id}")
        return TriliumNote.from_api(data)

    async def get_note_content(self, note_id: str) -> str:
        """Get note content body."""
        self._ensure_client()
        resp = await self._client.get(f"/notes/{note_id}/content")
        resp.raise_for_status()
        return resp.text

    async def update_note(self, note_id: str, **fields) -> Dict[str, Any]:
        """Update note metadata. Fields: title, type, mime."""
        api_fields = {}
        if "title" in fields:
            api_fields["title"] = fields["title"]
        if "type" in fields:
            api_fields["type"] = fields["type"]
        if "mime" in fields:
            api_fields["mime"] = fields["mime"]
        return await self._request("PATCH", f"/notes/{note_id}", json=api_fields)

    async def update_note_content(self, note_id: str, content: str) -> None:
        """Replace note content body."""
        self._ensure_client()
        resp = await self._client.put(
            f"/notes/{note_id}/content",
            content=content,
            headers={"Content-Type": "text/html"},
        )
        resp.raise_for_status()

    async def delete_note(self, note_id: str) -> None:
        """Delete a note."""
        await self._request("DELETE", f"/notes/{note_id}")

    async def search_notes(
        self,
        query: str,
        fast_search: bool = False,
        include_archived: bool = False,
        ancestor_note_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[TriliumNote]:
        """Search notes. Supports fulltext and label queries."""
        params: Dict[str, Any] = {"search": query}
        if fast_search:
            params["fastSearch"] = "true"
        if include_archived:
            params["includeArchivedNotes"] = "true"
        if ancestor_note_id:
            params["ancestorNoteId"] = ancestor_note_id
        if limit:
            params["limit"] = str(limit)

        data = await self._request("GET", "/notes", params=params)
        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            return [TriliumNote.from_api(n) for n in results[:limit]]
        return []

    # ── Attributes (Labels & Relations) ──────────────────────────────────

    async def set_attribute(
        self,
        note_id: str,
        attr_type: str,
        name: str,
        value: str = "",
        is_inheritable: bool = False,
    ) -> Dict[str, Any]:
        """Create or update an attribute on a note.
        attr_type: 'label' or 'relation'
        For labels: value is the label value.
        For relations: value is the target noteId.
        """
        body = {
            "noteId": note_id,
            "type": attr_type,
            "name": name,
            "value": value,
            "isInheritable": is_inheritable,
        }
        return await self._request("POST", "/attributes", json=body)

    async def create_label(
        self, note_id: str, name: str, value: str = "", inheritable: bool = False
    ) -> Dict[str, Any]:
        """Shortcut: create a label attribute."""
        return await self.set_attribute(note_id, "label", name, value, inheritable)

    async def create_relation(
        self, note_id: str, name: str, target_note_id: str, inheritable: bool = False
    ) -> Dict[str, Any]:
        """Shortcut: create a relation attribute pointing to another note."""
        return await self.set_attribute(note_id, "relation", name, target_note_id, inheritable)

    # ── Branches ─────────────────────────────────────────────────────────

    async def clone_note(self, note_id: str, new_parent_id: str, prefix: Optional[str] = None) -> Dict[str, Any]:
        """Clone a note into a second parent (note appears in both trees)."""
        body: Dict[str, Any] = {
            "noteId": note_id,
            "parentNoteId": new_parent_id,
        }
        if prefix:
            body["prefix"] = prefix
        return await self._request("POST", "/branches", json=body)

    # ── Export / Import ──────────────────────────────────────────────────

    async def export_note(self, note_id: str, format: str = "html") -> bytes:
        """Export a note subtree. format: 'html' or 'markdown'."""
        self._ensure_client()
        resp = await self._client.get(
            f"/notes/{note_id}/export",
            params={"format": format},
        )
        resp.raise_for_status()
        return resp.content

    # ── Inbox / Calendar ─────────────────────────────────────────────────

    async def get_inbox(self, date: str) -> TriliumNote:
        """Get inbox note for a given date (YYYY-MM-DD)."""
        data = await self._request("GET", f"/inbox/{date}")
        return TriliumNote.from_api(data)

    async def get_day_note(self, date: str) -> TriliumNote:
        """Get day note for a given date (YYYY-MM-DD)."""
        data = await self._request("GET", f"/calendar/days/{date}")
        return TriliumNote.from_api(data)

    # ── Convenience ──────────────────────────────────────────────────────

    async def file_research_finding(
        self,
        title: str,
        html_content: str,
        source: str = "",
        tags: Optional[List[str]] = None,
        relevance: float = 0.5,
    ) -> TriliumNote:
        """File a research finding under Weekly Research parent.
        Creates the note, sets labels for source, relevance, and tags.
        """
        parent = KNOWN_PARENTS.get("weekly_research") or "root"
        note = await self.create_note(parent, title, html_content)

        if source:
            await self.create_label(note.note_id, "source", source)
        await self.create_label(note.note_id, "relevance", str(relevance))
        await self.create_label(note.note_id, "dateAdded", note.date_created or "")

        for tag in (tags or []):
            await self.create_label(note.note_id, "tag", tag)

        return note

    async def file_document_summary(
        self,
        title: str,
        html_summary: str,
        source_path: str = "",
        cross_refs: Optional[List[str]] = None,
    ) -> TriliumNote:
        """File a document summary under Document Summaries parent."""
        parent = KNOWN_PARENTS.get("document_summaries") or "root"
        note = await self.create_note(parent, title, html_summary)

        if source_path:
            await self.create_label(note.note_id, "sourcePath", source_path)

        for ref_id in (cross_refs or []):
            await self.create_relation(note.note_id, "references", ref_id)

        return note

    async def file_security_finding(
        self,
        title: str,
        html_content: str,
        severity: str = "info",
        tags: Optional[List[str]] = None,
    ) -> TriliumNote:
        """File a security finding under Security Findings parent."""
        parent = KNOWN_PARENTS.get("security") or "root"
        note = await self.create_note(parent, title, html_content)
        await self.create_label(note.note_id, "severity", severity)
        for tag in (tags or []):
            await self.create_label(note.note_id, "tag", tag)
        return note


# ── Sync wrapper for non-async contexts ──────────────────────────────────

def get_client(**kwargs) -> TriliumClient:
    """Factory — returns a TriliumClient with env-based defaults."""
    return TriliumClient(**kwargs)


async def quick_search(query: str, limit: int = 10) -> List[TriliumNote]:
    """One-shot search — opens and closes client automatically."""
    async with TriliumClient() as client:
        return await client.search_notes(query, limit=limit)


async def quick_create(parent: str, title: str, content: str, **labels) -> TriliumNote:
    """One-shot note creation with optional labels."""
    async with TriliumClient() as client:
        note = await client.create_note(parent, title, content)
        for k, v in labels.items():
            await client.create_label(note.note_id, k, str(v))
        return note
