"""
Web Search Tools

Multi-provider web search with intelligent fallback.
Priority: Brave → Perplexity → Gemini → Google → DuckDuckGo
"""

from .web_search import (
    WebSearch,
    SearchResult,
    SearchResponse,
    SearchProvider,
    BraveSearch,
    PerplexitySearch,
    GeminiSearch,
    GoogleSearch,
    BingSearch,
    DuckDuckGoSearch,
)

__all__ = [
    "WebSearch",
    "SearchResult",
    "SearchResponse",
    "SearchProvider",
    "BraveSearch",
    "PerplexitySearch",
    "GeminiSearch",
    "GoogleSearch",
    "BingSearch",
    "DuckDuckGoSearch",
]
