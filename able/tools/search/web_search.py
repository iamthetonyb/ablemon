"""
Web Search Tool - Multi-provider web search with intelligent fallback.

Priority chain (mirrors OpenClaw pattern):
  Brave → Perplexity → Gemini → Google → DuckDuckGo

Supports:
- Brave Search API (free 2K/mo, structured API, LLM grounding context)
- Perplexity Sonar (deep research with cited sources, $1/M tokens)
- Google Gemini Grounding (free 5K grounded prompts/mo)
- Google Custom Search API
- Bing Search API
- DuckDuckGo (no API key, HTML scraping fallback)

Returns structured results with title, snippet, URL.
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlencode

logger = logging.getLogger(__name__)

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


class SearchProvider(Enum):
    BRAVE = "brave"
    PERPLEXITY = "perplexity"
    GEMINI = "gemini"
    GOOGLE = "google"
    BING = "bing"
    DUCKDUCKGO = "duckduckgo"
    SERPAPI = "serpapi"


@dataclass
class SearchResult:
    """A single search result"""
    title: str
    url: str
    snippet: str
    position: int
    provider: SearchProvider
    metadata: Dict = field(default_factory=dict)


@dataclass
class SearchResponse:
    """Response from a web search"""
    query: str
    results: List[SearchResult]
    total_results: int
    search_time_ms: float
    provider: SearchProvider
    citations: Optional[List[str]] = None  # For Perplexity cited sources


# ── Brave Search ──────────────────────────────────────────────────────────────

class BraveSearch:
    """
    Brave Search API — OpenClaw's recommended search provider.

    Free tier: 2,000 queries/month.
    Features: Structured API, no scraping, LLM Context endpoint for grounding.
    API docs: https://api.search.brave.com/
    """

    BASE_URL = "https://api.search.brave.com/res/v1/web/search"
    SUMMARIZER_URL = "https://api.search.brave.com/res/v1/summarizer/search"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("BRAVE_SEARCH_API_KEY", "")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self.api_key,
                }
            )
        return self._session

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """Search Brave — returns structured results with snippets."""
        if not self.api_key:
            logger.debug("Brave API key not configured, skipping")
            return []

        session = await self._get_session()

        params = {
            "q": query,
            "count": min(max_results, 20),
            "text_decorations": "false",
            "search_lang": "en",
        }

        try:
            async with session.get(self.BASE_URL, params=params) as response:
                if response.status == 429:
                    logger.warning("Brave Search rate limited (free tier: 2K/mo)")
                    return []
                if response.status != 200:
                    text = await response.text()
                    logger.warning(f"Brave search failed: {response.status} - {text[:200]}")
                    return []

                data = await response.json()

            results = []
            web_results = data.get("web", {}).get("results", [])
            for i, item in enumerate(web_results[:max_results]):
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    position=i + 1,
                    provider=SearchProvider.BRAVE,
                    metadata={
                        "age": item.get("age"),
                        "language": item.get("language"),
                        "extra_snippets": item.get("extra_snippets", []),
                    }
                ))

            return results

        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            return []

    async def get_llm_context(self, query: str) -> Optional[str]:
        """
        Brave's LLM Context / Summarizer endpoint.
        Returns pre-processed grounding snippets with source metadata.
        Ideal for RAG workflows.
        """
        if not self.api_key:
            return None

        session = await self._get_session()

        try:
            params = {"q": query, "summary": "1"}
            async with session.get(self.BASE_URL, params=params) as response:
                if response.status != 200:
                    return None
                data = await response.json()

            summarizer_key = data.get("summarizer", {}).get("key")
            if not summarizer_key:
                return None

            async with session.get(
                self.SUMMARIZER_URL,
                params={"key": summarizer_key, "entity_info": "1"}
            ) as response:
                if response.status != 200:
                    return None
                summary_data = await response.json()

            return summary_data.get("summary", [{}])[0].get("data", "")

        except Exception as e:
            logger.error(f"Brave LLM context failed: {e}")
            return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── Perplexity Sonar ──────────────────────────────────────────────────────────

class PerplexitySearch:
    """
    Perplexity Sonar API — deep research with cited sources.

    Best for: Deep research queries requiring cited, verified sources.
    Models: sonar (fast), sonar-pro (deep), sonar-reasoning-pro (complex).
    Pricing: $1/$1 per M tokens (sonar), $3/$15 (sonar-pro).
    API: OpenAI-compatible chat completions endpoint.
    """

    BASE_URL = "https://api.perplexity.ai/chat/completions"

    def __init__(self, api_key: str = None, model: str = "sonar"):
        self.api_key = api_key or os.environ.get("PERPLEXITY_API_KEY", "")
        self.model = model
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """
        Search via Perplexity Sonar — returns answer with inline citations.
        Perplexity doesn't return traditional search results; it returns a
        synthesized answer with citation URLs. We extract both.
        """
        if not self.api_key:
            logger.debug("Perplexity API key not configured, skipping")
            return []

        session = await self._get_session()

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a research assistant. Provide a concise, factual answer with specific details. Be direct."
                },
                {
                    "role": "user",
                    "content": query
                }
            ],
            "max_tokens": 1024,
            "return_citations": True,
            "search_recency_filter": "month",
        }

        try:
            async with session.post(self.BASE_URL, json=payload) as response:
                if response.status == 429:
                    logger.warning("Perplexity rate limited")
                    return []
                if response.status != 200:
                    text = await response.text()
                    logger.warning(f"Perplexity search failed: {response.status} - {text[:200]}")
                    return []

                data = await response.json()

            # Extract the synthesized answer
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])

            results = []

            # The main synthesized answer as the first result
            if answer:
                results.append(SearchResult(
                    title=f"Perplexity Research: {query[:60]}",
                    url="https://perplexity.ai",
                    snippet=answer[:500],
                    position=1,
                    provider=SearchProvider.PERPLEXITY,
                    metadata={
                        "full_answer": answer,
                        "citations": citations,
                        "model": self.model,
                    }
                ))

            # Citation URLs as individual results
            for i, citation_url in enumerate(citations[:max_results - 1]):
                if isinstance(citation_url, str):
                    results.append(SearchResult(
                        title=f"Source [{i+1}]",
                        url=citation_url,
                        snippet=f"Cited source for: {query[:100]}",
                        position=i + 2,
                        provider=SearchProvider.PERPLEXITY,
                        metadata={"citation_index": i + 1}
                    ))

            return results

        except Exception as e:
            logger.error(f"Perplexity search failed: {e}")
            return []

    async def deep_research(self, query: str) -> Dict[str, Any]:
        """
        Deep research mode — uses sonar-pro for comprehensive, cited analysis.
        Returns full answer + all citation URLs.
        Best for: complex questions, market research, technical deep dives.
        """
        if not self.api_key:
            return {"answer": "", "citations": []}

        session = await self._get_session()

        payload = {
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a thorough research analyst. Provide comprehensive, well-structured analysis with specific data points, statistics, and evidence. Cite all sources."
                },
                {
                    "role": "user",
                    "content": query
                }
            ],
            "max_tokens": 4096,
            "return_citations": True,
        }

        try:
            async with session.post(self.BASE_URL, json=payload) as response:
                if response.status != 200:
                    return {"answer": "", "citations": []}
                data = await response.json()

            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])

            return {
                "answer": answer,
                "citations": citations,
                "model": "sonar-pro",
                "usage": data.get("usage", {}),
            }

        except Exception as e:
            logger.error(f"Perplexity deep research failed: {e}")
            return {"answer": "", "citations": []}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── Gemini Grounding Search ───────────────────────────────────────────────────

class GeminiSearch:
    """
    Google Gemini Grounding with Google Search.

    Free tier: 5,000 grounded prompts/month.
    Features: Real-time web grounding, inline citations, Google's own index.
    Uses the google-generativeai SDK or direct REST API.
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """
        Search via Gemini with Google Search grounding.
        Gemini generates an answer grounded in real-time Google Search results.
        Returns the grounded answer + supporting search entries.
        """
        if not self.api_key:
            logger.debug("Gemini API key not configured, skipping")
            return []

        session = await self._get_session()

        url = f"{self.BASE_URL}/{self.model}:generateContent?key={self.api_key}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": query}
                    ]
                }
            ],
            "tools": [
                {
                    "google_search_retrieval": {
                        "dynamic_retrieval_config": {
                            "mode": "MODE_DYNAMIC",
                            "dynamic_threshold": 0.3,
                        }
                    }
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1024,
            }
        }

        try:
            async with session.post(url, json=payload) as response:
                if response.status == 429:
                    logger.warning("Gemini rate limited (free tier: 5K grounded/mo)")
                    return []
                if response.status != 200:
                    text = await response.text()
                    logger.warning(f"Gemini search failed: {response.status} - {text[:200]}")
                    return []

                data = await response.json()

            results = []

            # Extract grounded answer
            candidates = data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                answer_text = " ".join(p.get("text", "") for p in parts if "text" in p)

                if answer_text:
                    results.append(SearchResult(
                        title=f"Gemini Grounded: {query[:60]}",
                        url="https://ai.google.dev",
                        snippet=answer_text[:500],
                        position=1,
                        provider=SearchProvider.GEMINI,
                        metadata={
                            "full_answer": answer_text,
                            "model": self.model,
                        }
                    ))

                # Extract grounding metadata (search entries with URLs)
                grounding_metadata = candidates[0].get("groundingMetadata", {})
                grounding_chunks = grounding_metadata.get("groundingChunks", [])

                for i, chunk in enumerate(grounding_chunks[:max_results - 1]):
                    web_info = chunk.get("web", {})
                    if web_info:
                        results.append(SearchResult(
                            title=web_info.get("title", f"Source [{i+1}]"),
                            url=web_info.get("uri", ""),
                            snippet=web_info.get("title", ""),
                            position=i + 2,
                            provider=SearchProvider.GEMINI,
                            metadata={"grounding_source": True}
                        ))

                # Extract search entry point (rendered search link)
                search_entry = grounding_metadata.get("searchEntryPoint", {})
                if search_entry.get("renderedContent"):
                    results[-1].metadata["search_entry"] = search_entry["renderedContent"]

            return results

        except Exception as e:
            logger.error(f"Gemini search failed: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── DuckDuckGo (Free Fallback) ────────────────────────────────────────────────

class DuckDuckGoSearch:
    """
    DuckDuckGo search - no API key required.
    Uses the HTML search page and parses results.
    Rate limited to avoid blocks. Last-resort fallback.
    """

    BASE_URL = "https://html.duckduckgo.com/html/"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request = 0
        self._min_delay = 1.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36"
                }
            )
        return self._session

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """Search DuckDuckGo"""
        if not BS4_AVAILABLE:
            logger.warning("BeautifulSoup not installed, using fallback")
            return []

        elapsed = time.time() - self._last_request
        if elapsed < self._min_delay:
            await asyncio.sleep(self._min_delay - elapsed)

        session = await self._get_session()
        self._last_request = time.time()

        try:
            async with session.post(
                self.BASE_URL,
                data={"q": query, "b": ""},
            ) as response:
                if response.status != 200:
                    logger.warning(f"DuckDuckGo returned {response.status}")
                    return []
                html = await response.text()

            soup = BeautifulSoup(html, 'html.parser')
            results = []

            for i, result in enumerate(soup.select('.result')):
                if i >= max_results:
                    break

                title_elem = result.select_one('.result__title a')
                snippet_elem = result.select_one('.result__snippet')

                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)
                url = title_elem.get('href', '')
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

                if 'uddg=' in url:
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    url = parsed.get('uddg', [url])[0]

                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    position=i + 1,
                    provider=SearchProvider.DUCKDUCKGO,
                ))

            return results

        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── Google Custom Search ──────────────────────────────────────────────────────

class GoogleSearch:
    """Google Custom Search API"""

    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str = None, cx: str = None):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.cx = cx or os.environ.get("GOOGLE_CX", "")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """Search Google"""
        if not self.api_key or not self.cx:
            logger.debug("Google API key or CX not configured")
            return []

        session = await self._get_session()

        params = {
            "key": self.api_key,
            "cx": self.cx,
            "q": query,
            "num": min(max_results, 10),
        }

        try:
            async with session.get(self.BASE_URL, params=params) as response:
                if response.status != 200:
                    text = await response.text()
                    logger.warning(f"Google search failed: {response.status} - {text[:200]}")
                    return []
                data = await response.json()

            results = []
            for i, item in enumerate(data.get("items", [])):
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    position=i + 1,
                    provider=SearchProvider.GOOGLE,
                    metadata={
                        "displayLink": item.get("displayLink"),
                        "pagemap": item.get("pagemap", {}),
                    }
                ))

            return results

        except Exception as e:
            logger.error(f"Google search failed: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── Bing Search ───────────────────────────────────────────────────────────────

class BingSearch:
    """Bing Search API"""

    BASE_URL = "https://api.bing.microsoft.com/v7.0/search"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("BING_API_KEY", "")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Ocp-Apim-Subscription-Key": self.api_key}
            )
        return self._session

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """Search Bing"""
        if not self.api_key:
            logger.debug("Bing API key not configured")
            return []

        session = await self._get_session()

        params = {
            "q": query,
            "count": max_results,
            "textDecorations": "false",
            "textFormat": "Raw",
        }

        try:
            async with session.get(self.BASE_URL, params=params) as response:
                if response.status != 200:
                    logger.warning(f"Bing search failed: {response.status}")
                    return []
                data = await response.json()

            results = []
            for i, item in enumerate(data.get("webPages", {}).get("value", [])):
                results.append(SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    snippet=item.get("snippet", ""),
                    position=i + 1,
                    provider=SearchProvider.BING,
                    metadata={
                        "dateLastCrawled": item.get("dateLastCrawled"),
                        "language": item.get("language"),
                    }
                ))

            return results

        except Exception as e:
            logger.error(f"Bing search failed: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── Unified WebSearch ─────────────────────────────────────────────────────────

class WebSearch:
    """
    Unified web search with intelligent multi-provider fallback.

    Priority chain (OpenClaw-inspired):
      Brave → Perplexity → Gemini → Google → DuckDuckGo

    Auto-detects available providers based on env vars.

    Usage:
        search = WebSearch()
        results = await search.search("ABLE AI agent")
        formatted = search.format_for_llm(results)

    For deep research:
        answer = await search.deep_research("market analysis of AI agents 2026")
    """

    def __init__(
        self,
        providers: List[SearchProvider] = None,
        brave_api_key: str = None,
        perplexity_api_key: str = None,
        gemini_api_key: str = None,
        google_api_key: str = None,
        google_cx: str = None,
        bing_api_key: str = None,
    ):
        # Initialize all provider instances
        self._brave = BraveSearch(api_key=brave_api_key)
        self._perplexity = PerplexitySearch(api_key=perplexity_api_key)
        self._gemini = GeminiSearch(api_key=gemini_api_key)
        self._google = GoogleSearch(api_key=google_api_key, cx=google_cx)
        self._bing = BingSearch(api_key=bing_api_key)
        self._ddg = DuckDuckGoSearch()

        # Auto-detect available providers if not specified
        if providers:
            self.providers = providers
        else:
            self.providers = self._auto_detect_providers()

        logger.info(f"WebSearch initialized with providers: {[p.value for p in self.providers]}")

    def _auto_detect_providers(self) -> List[SearchProvider]:
        """Auto-detect available search providers from environment variables."""
        available = []

        if os.environ.get("BRAVE_SEARCH_API_KEY"):
            available.append(SearchProvider.BRAVE)
        if os.environ.get("PERPLEXITY_API_KEY"):
            available.append(SearchProvider.PERPLEXITY)
        if os.environ.get("GEMINI_API_KEY"):
            available.append(SearchProvider.GEMINI)
        if os.environ.get("GOOGLE_API_KEY") and os.environ.get("GOOGLE_CX"):
            available.append(SearchProvider.GOOGLE)
        if os.environ.get("BING_API_KEY"):
            available.append(SearchProvider.BING)

        # DuckDuckGo is always available as fallback (no API key needed)
        available.append(SearchProvider.DUCKDUCKGO)

        return available

    def _get_provider_instance(self, provider: SearchProvider):
        """Map provider enum to instance."""
        return {
            SearchProvider.BRAVE: self._brave,
            SearchProvider.PERPLEXITY: self._perplexity,
            SearchProvider.GEMINI: self._gemini,
            SearchProvider.GOOGLE: self._google,
            SearchProvider.BING: self._bing,
            SearchProvider.DUCKDUCKGO: self._ddg,
        }.get(provider)

    async def search(
        self,
        query: str,
        max_results: int = 10,
        provider: SearchProvider = None,
    ) -> SearchResponse:
        """
        Search the web with automatic provider fallback.

        Args:
            query: Search query
            max_results: Maximum results to return
            provider: Force a specific provider (or cascade through all)

        Returns:
            SearchResponse with results and citations
        """
        start_time = time.time()
        providers_to_try = [provider] if provider else self.providers

        for prov in providers_to_try:
            try:
                instance = self._get_provider_instance(prov)
                if not instance:
                    continue

                results = await instance.search(query, max_results)

                if results:
                    elapsed_ms = (time.time() - start_time) * 1000

                    # Extract citations if from Perplexity
                    citations = None
                    if prov == SearchProvider.PERPLEXITY and results:
                        citations = results[0].metadata.get("citations", [])

                    return SearchResponse(
                        query=query,
                        results=results,
                        total_results=len(results),
                        search_time_ms=elapsed_ms,
                        provider=prov,
                        citations=citations,
                    )

            except Exception as e:
                logger.warning(f"Provider {prov.value} failed: {e}")
                continue

        # All providers failed
        elapsed_ms = (time.time() - start_time) * 1000
        return SearchResponse(
            query=query,
            results=[],
            total_results=0,
            search_time_ms=elapsed_ms,
            provider=providers_to_try[0] if providers_to_try else SearchProvider.DUCKDUCKGO,
        )

    async def deep_research(self, query: str) -> Dict[str, Any]:
        """
        Deep research mode — uses Perplexity sonar-pro for cited analysis.
        Falls back to Gemini grounding if Perplexity unavailable.

        Returns:
            {"answer": str, "citations": list, "model": str}
        """
        # Try Perplexity deep research first
        if self._perplexity.api_key:
            result = await self._perplexity.deep_research(query)
            if result.get("answer"):
                return result

        # Fall back to Gemini grounding
        if self._gemini.api_key:
            results = await self._gemini.search(query, max_results=5)
            if results:
                answer = results[0].metadata.get("full_answer", results[0].snippet)
                citations = [r.url for r in results[1:] if r.url]
                return {"answer": answer, "citations": citations, "model": "gemini-grounding"}

        # Fall back to Brave LLM context
        if self._brave.api_key:
            context = await self._brave.get_llm_context(query)
            if context:
                return {"answer": context, "citations": [], "model": "brave-llm-context"}

        # Last resort: regular search and concat snippets
        response = await self.search(query, max_results=5)
        if response.results:
            combined = "\n".join(f"- {r.title}: {r.snippet}" for r in response.results)
            citations = [r.url for r in response.results]
            return {"answer": combined, "citations": citations, "model": f"{response.provider.value}-fallback"}

        return {"answer": "", "citations": [], "model": "none"}

    def format_for_llm(self, response: SearchResponse) -> str:
        """Format search results for LLM consumption"""
        if not response.results:
            return f"No results found for: {response.query}"

        lines = [f"Search results for: {response.query} (via {response.provider.value})\n"]

        for r in response.results:
            # For Perplexity/Gemini, the first result may contain a full answer
            if r.position == 1 and r.metadata.get("full_answer"):
                lines.append(f"## Synthesized Answer\n{r.metadata['full_answer']}\n")
                if r.metadata.get("citations"):
                    lines.append("Sources:")
                    for i, url in enumerate(r.metadata["citations"]):
                        lines.append(f"  [{i+1}] {url}")
                    lines.append("")
            else:
                lines.append(f"{r.position}. {r.title}")
                lines.append(f"   URL: {r.url}")
                lines.append(f"   {r.snippet}\n")

        return "\n".join(lines)

    async def close(self):
        """Close all provider sessions"""
        await self._brave.close()
        await self._perplexity.close()
        await self._gemini.close()
        await self._google.close()
        await self._bing.close()
        await self._ddg.close()
