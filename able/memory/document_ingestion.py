"""
Document Ingestion Pipeline — PDF, HTML, and plain-text ingestion into ABLE memory.

Supports:
  - PDF files (via pypdf — no heavy ML deps)
  - HTML pages / URLs (via beautifulsoup4 + urllib)
  - Plain text (direct)

Documents are chunked, embedded, and stored in the VectorStore so they
surface in semantic memory recall alongside conversation history.

Usage:
    ingester = DocumentIngester()

    # PDF — local file or Telegram download
    result = ingester.ingest_pdf("path/to/doc.pdf", metadata={"title": "Q1 Report"})

    # URL
    result = ingester.ingest_url("https://example.com/docs/api")

    # Raw text
    result = ingester.ingest_text("Your company context...", source="manual")

    # Summary
    print(f"Ingested {result.chunk_count} chunks from {result.source}")

Chunk parameters (tunable via env vars):
    ABLE_CHUNK_SIZE    — chars per chunk (default 800)
    ABLE_CHUNK_OVERLAP — overlap between chunks (default 100)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHUNK_SIZE = int(os.environ.get("ABLE_CHUNK_SIZE", 800))
_CHUNK_OVERLAP = int(os.environ.get("ABLE_CHUNK_OVERLAP", 100))

# Default vector store location (overridden in tests or alternate deployments)
_DEFAULT_VECTOR_STORE = Path(
    os.environ.get("ABLE_HOME", Path.home() / ".able")
) / "memory" / "vectors.bin"


@dataclass
class IngestionResult:
    """Outcome of a single document ingestion."""

    doc_id: str
    source: str
    title: str
    chunk_count: int
    total_chars: int
    vector_ids: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def ok(self) -> bool:
        return self.chunk_count > 0 and not self.errors


class DocumentIngester:
    """
    Ingest documents into ABLE's vector memory.

    The ingester is intentionally dependency-light:
      - pypdf: only imported when ingesting PDFs (in requirements-full.txt)
      - beautifulsoup4: only imported when ingesting HTML (in requirements-full.txt)
      - urllib: stdlib, always available

    VectorStore handles its own embedding provider selection (openai → ollama →
    simple hash fallback), so ingestion works even without API keys.
    """

    def __init__(
        self,
        vector_store_path: Optional[Path] = None,
        chunk_size: int = _CHUNK_SIZE,
        chunk_overlap: int = _CHUNK_OVERLAP,
    ) -> None:
        self._store_path = Path(vector_store_path or _DEFAULT_VECTOR_STORE)
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._store: Any = None  # Lazy-loaded

    # ── Lazy vector store init ──────────────────────────────────────────────

    def _get_store(self):
        if self._store is None:
            from able.memory.embeddings.vector_store import VectorStore

            self._store = VectorStore(
                storage_path=self._store_path,
                embedding_dim=384,
                embedding_provider="auto",
            )
            logger.debug(
                "DocumentIngester: vector store at %s (%d entries)",
                self._store_path,
                self._store.count(),
            )
        return self._store

    # ── Public API ─────────────────────────────────────────────────────────

    def ingest_pdf(
        self,
        file_path: str | Path,
        metadata: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
    ) -> IngestionResult:
        """
        Extract text from a PDF and store chunks in vector memory.

        Requires: pypdf (pip install pypdf) — in requirements-full.txt.
        Works page-by-page; each page's text is combined then chunked.
        """
        file_path = Path(file_path)
        title = title or file_path.stem
        doc_id = self._doc_id(str(file_path))

        try:
            import pypdf  # type: ignore[import-untyped]
        except ImportError:
            msg = "pypdf not installed — run: pip install pypdf"
            logger.error(msg)
            return IngestionResult(
                doc_id=doc_id,
                source=str(file_path),
                title=title,
                chunk_count=0,
                total_chars=0,
                errors=[msg],
            )

        try:
            reader = pypdf.PdfReader(str(file_path))
            pages_text: List[str] = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(f"[Page {i + 1}]\n{text.strip()}")

            full_text = "\n\n".join(pages_text)
            page_count = len(reader.pages)
            logger.info(
                "PDF %s: %d pages, %d chars extracted",
                file_path.name,
                page_count,
                len(full_text),
            )
        except Exception as exc:
            msg = f"PDF extraction failed: {exc}"
            logger.error(msg)
            return IngestionResult(
                doc_id=doc_id,
                source=str(file_path),
                title=title,
                chunk_count=0,
                total_chars=0,
                errors=[msg],
            )

        base_meta = {
            "source_type": "pdf",
            "source": str(file_path),
            "title": title,
            "doc_id": doc_id,
            **(metadata or {}),
        }
        return self._store_text(full_text, doc_id, str(file_path), title, base_meta)

    def ingest_url(
        self,
        url: str,
        metadata: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
    ) -> IngestionResult:
        """
        Fetch an HTML page and store its text content in vector memory.

        Requires: beautifulsoup4 (pip install beautifulsoup4) — in requirements-full.txt.
        Falls back to raw HTML stripping via regex when bs4 is unavailable.
        """
        doc_id = self._doc_id(url)

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ABLE/1.0 document-ingester"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw_bytes = resp.read(2_000_000)  # max 2MB
                charset = resp.headers.get_content_charset("utf-8")
                html = raw_bytes.decode(charset, errors="replace")
        except Exception as exc:
            msg = f"URL fetch failed: {exc}"
            logger.error(msg)
            return IngestionResult(
                doc_id=doc_id,
                source=url,
                title=title or url,
                chunk_count=0,
                total_chars=0,
                errors=[msg],
            )

        text, page_title = self._html_to_text(html)
        title = title or page_title or url

        base_meta = {
            "source_type": "url",
            "source": url,
            "title": title,
            "doc_id": doc_id,
            **(metadata or {}),
        }
        return self._store_text(text, doc_id, url, title, base_meta)

    def ingest_text(
        self,
        content: str,
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
    ) -> IngestionResult:
        """Store raw text directly in vector memory."""
        doc_id = self._doc_id(content[:200])
        title = title or source
        base_meta = {
            "source_type": "text",
            "source": source,
            "title": title,
            "doc_id": doc_id,
            **(metadata or {}),
        }
        return self._store_text(content, doc_id, source, title, base_meta)

    def list_documents(self) -> List[Dict[str, Any]]:
        """Return metadata for all ingested documents (unique doc_ids)."""
        store = self._get_store()
        docs: Dict[str, Dict[str, Any]] = {}
        for entry in store.vectors.values():
            doc_id = entry.metadata.get("doc_id")
            if doc_id and doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "title": entry.metadata.get("title", ""),
                    "source": entry.metadata.get("source", ""),
                    "source_type": entry.metadata.get("source_type", ""),
                }
        return list(docs.values())

    def delete_document(self, doc_id: str) -> int:
        """Remove all chunks for a given doc_id. Returns number deleted."""
        store = self._get_store()
        to_delete = [
            vid
            for vid, e in store.vectors.items()
            if e.metadata.get("doc_id") == doc_id
        ]
        for vid in to_delete:
            store.delete(vid)
        logger.info("Deleted %d chunks for doc_id=%s", len(to_delete), doc_id)
        return len(to_delete)

    # ── Internals ──────────────────────────────────────────────────────────

    def _store_text(
        self,
        text: str,
        doc_id: str,
        source: str,
        title: str,
        base_metadata: Dict[str, Any],
    ) -> IngestionResult:
        if not text.strip():
            return IngestionResult(
                doc_id=doc_id,
                source=source,
                title=title,
                chunk_count=0,
                total_chars=0,
                errors=["No text content extracted"],
            )

        chunks = self._chunk(text)
        store = self._get_store()
        vector_ids: List[str] = []
        errors: List[str] = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}::chunk_{i}"
            try:
                vec = store.compute_embedding(chunk)
                if vec:
                    store.add(
                        chunk_id,
                        vec,
                        metadata={
                            **base_metadata,
                            "chunk_index": i,
                            "chunk_total": len(chunks),
                            "text": chunk[:500],  # store preview for recall
                        },
                    )
                    vector_ids.append(chunk_id)
            except Exception as exc:
                errors.append(f"chunk {i}: {exc}")
                logger.warning("Embedding chunk %d failed: %s", i, exc)

        logger.info(
            "Ingested %d/%d chunks from %s (%d chars total)",
            len(vector_ids),
            len(chunks),
            source,
            len(text),
        )

        result = IngestionResult(
            doc_id=doc_id,
            source=source,
            title=title,
            chunk_count=len(vector_ids),
            total_chars=len(text),
            vector_ids=vector_ids,
            errors=errors,
        )

        # File summary to TriliumNext knowledge base
        self._file_to_trilium(title, text, source)

        return result

    def _chunk(self, text: str) -> List[str]:
        """Split text into overlapping chunks at sentence/paragraph boundaries."""
        # Normalize whitespace
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if len(text) <= self._chunk_size:
            return [text]

        # Try to split at paragraph boundaries first
        paragraphs = re.split(r"\n\n+", text)
        chunks: List[str] = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) + 2 <= self._chunk_size:
                current = f"{current}\n\n{para}".strip()
            else:
                if current:
                    chunks.append(current)
                # Para itself is too long — split at sentence boundary
                if len(para) > self._chunk_size:
                    chunks.extend(self._split_sentences(para))
                    current = ""
                else:
                    # Start fresh chunk with overlap from previous
                    if chunks:
                        overlap_text = chunks[-1][-self._chunk_overlap:]
                        current = f"{overlap_text}\n\n{para}".strip()
                    else:
                        current = para

        if current:
            chunks.append(current)

        return [c for c in chunks if c.strip()]

    def _split_sentences(self, text: str) -> List[str]:
        """Split long paragraphs at sentence boundaries."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks: List[str] = []
        current = ""
        for sent in sentences:
            if len(current) + len(sent) + 1 <= self._chunk_size:
                current = f"{current} {sent}".strip()
            else:
                if current:
                    chunks.append(current)
                current = sent
        if current:
            chunks.append(current)
        return chunks

    def _html_to_text(self, html: str) -> tuple[str, str]:
        """Extract readable text from HTML. Returns (text, page_title)."""
        title = ""
        try:
            from bs4 import BeautifulSoup  # type: ignore[import-untyped]

            soup = BeautifulSoup(html, "html.parser")
            # Extract title
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            # Remove script/style/nav/footer noise
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except ImportError:
            # Fallback: regex-based HTML stripping
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            if title_match:
                title = title_match.group(1).strip()
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"&[a-z]+;", " ", text)

        # Collapse whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
        return text, title

    def _file_to_trilium(self, title: str, text: str, source: str):
        """File a document summary to TriliumNext (best-effort, non-blocking)."""
        try:
            import asyncio
            from able.tools.trilium.client import TriliumClient

            # Build a summary: first 500 chars as HTML
            preview = text[:500].replace("&", "&amp;").replace("<", "&lt;")
            html = (
                f"<h3>{title}</h3>"
                f"<p><strong>Source:</strong> {source}</p>"
                f"<p><strong>Length:</strong> {len(text):,} chars</p>"
                f"<hr/><p>{preview}...</p>"
            )

            async def _file():
                async with TriliumClient() as client:
                    if not await client.is_available():
                        return
                    await client.file_document_summary(
                        title=title,
                        html_summary=html,
                        source_path=source,
                    )

            asyncio.run(_file())
            logger.debug("Filed document summary to Trilium: %s", title)
        except Exception as e:
            logger.debug("Trilium filing skipped: %s", e)

    @staticmethod
    def _doc_id(source: str) -> str:
        return hashlib.sha1(source.encode()).hexdigest()[:16]
