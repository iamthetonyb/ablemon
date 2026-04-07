"""
XCrawl API Client — Structured web scraping for research pipeline.

Provides clean markdown extraction from URLs, replacing raw HTML scraping.
Used by the research pipeline for full-content extraction of high-priority findings.

API: https://docs.xcrawl.dev
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


@dataclass
class ScrapeResult:
    """Result from a single URL scrape."""
    url: str
    title: str = ""
    markdown: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    links: List[str] = field(default_factory=list)
    status_code: int = 0
    error: Optional[str] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "ScrapeResult":
        return cls(
            url=data.get("url", ""),
            title=data.get("title", ""),
            markdown=data.get("markdown", data.get("content", "")),
            metadata=data.get("metadata", {}),
            links=[l.get("url", "") for l in data.get("links", []) if l.get("url")],
            status_code=data.get("statusCode", 0),
        )


@dataclass
class SearchResult:
    """Result from an XCrawl search."""
    url: str
    title: str = ""
    snippet: str = ""
    source: str = ""

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "SearchResult":
        return cls(
            url=data.get("url", ""),
            title=data.get("title", ""),
            snippet=data.get("snippet", data.get("description", "")),
            source=data.get("source", ""),
        )


class XCrawlClient:
    """
    Async client for XCrawl API — structured web scraping.

    Usage:
        async with XCrawlClient() as client:
            result = await client.scrape("https://example.com")
            print(result.markdown)
    """

    BASE_URL = "https://api.xcrawl.dev/v1"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx required: pip install httpx")
        self.api_key = api_key or os.environ.get("XCRAWL_API_KEY", "")
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=60.0,
        )
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=60.0,
            )

    async def scrape(
        self,
        url: str,
        formats: Optional[List[str]] = None,
        wait_for: Optional[str] = None,
    ) -> ScrapeResult:
        """
        Scrape a single URL and return structured content.

        Args:
            url: URL to scrape
            formats: Output formats — ["markdown", "links", "metadata"]
            wait_for: CSS selector to wait for before extracting (for SPAs)
        """
        self._ensure_client()
        body: Dict[str, Any] = {
            "url": url,
            "formats": formats or ["markdown", "links", "metadata"],
        }
        if wait_for:
            body["waitFor"] = wait_for

        try:
            resp = await self._client.post(f"{self.base_url}/scrape", json=body)
            if resp.status_code >= 400:
                return ScrapeResult(url=url, error=f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json().get("data", resp.json())
            return ScrapeResult.from_api(data)
        except Exception as e:
            return ScrapeResult(url=url, error=str(e))

    async def search(
        self,
        query: str,
        limit: int = 10,
        lang: str = "en",
        country: str = "us",
    ) -> List[SearchResult]:
        """Search the web via XCrawl and return structured results."""
        self._ensure_client()
        body = {
            "query": query,
            "limit": limit,
            "lang": lang,
            "country": country,
        }
        try:
            resp = await self._client.post(f"{self.base_url}/search", json=body)
            if resp.status_code >= 400:
                logger.error("XCrawl search failed: %d", resp.status_code)
                return []
            results = resp.json().get("data", resp.json().get("results", []))
            return [SearchResult.from_api(r) for r in results]
        except Exception as e:
            logger.error("XCrawl search error: %s", e)
            return []

    async def map_site(self, url: str, limit: int = 100) -> List[str]:
        """Discover all URLs on a domain."""
        self._ensure_client()
        try:
            resp = await self._client.post(
                f"{self.base_url}/map",
                json={"url": url, "limit": limit},
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
            return data.get("links", data.get("urls", []))
        except Exception as e:
            logger.error("XCrawl map error: %s", e)
            return []

    async def scrape_batch(
        self, urls: List[str], formats: Optional[List[str]] = None
    ) -> List[ScrapeResult]:
        """Scrape multiple URLs. Falls back to sequential if batch endpoint unavailable."""
        results = []
        for url in urls[:20]:  # Cap at 20 to avoid abuse
            result = await self.scrape(url, formats=formats)
            results.append(result)
        return results
