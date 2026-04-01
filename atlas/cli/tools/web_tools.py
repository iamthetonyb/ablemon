"""
Web tools — WebSearchTool, WebFetchTool.

Thin wrappers around the existing atlas.tools.search.web_search module.
Falls back gracefully when dependencies (aiohttp, etc.) are missing.
"""

from pathlib import Path
from typing import Optional

from .base import CLITool, ToolContext


class WebSearchTool(CLITool):
    """Search the web using the ATLAS multi-provider search chain."""

    def __init__(self, **_kw):
        super().__init__(
            name="web_search",
            description=(
                "Search the web. Uses the ATLAS multi-provider chain: "
                "Brave -> Perplexity -> Gemini -> Google -> DuckDuckGo."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string.",
                    },
                },
                "required": ["query"],
            },
            is_read_only=True,
            is_concurrent_safe=True,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        if not args.get("query", "").strip():
            return "query cannot be empty"
        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        query = args["query"]
        try:
            import sys
            atlas_root = str(Path(__file__).resolve().parent.parent.parent)
            if atlas_root not in sys.path:
                sys.path.insert(0, atlas_root)
            from tools.search.web_search import WebSearch

            ws = WebSearch()
            response = await ws.search(query, max_results=10)
            result = ws.format_for_llm(response)
            await ws.close()
            return result

        except ImportError as e:
            return f"Web search unavailable (missing dependency: {e})"
        except Exception as e:
            return f"Web search failed: {e}"


class WebFetchTool(CLITool):
    """Fetch and extract content from a URL."""

    def __init__(self, **_kw):
        super().__init__(
            name="web_fetch",
            description="Fetch a URL and return its text content.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Optional prompt to focus extraction.",
                    },
                },
                "required": ["url"],
            },
            is_read_only=True,
            is_concurrent_safe=True,
        )

    def validate_input(self, args: dict, ctx: Optional[ToolContext] = None) -> Optional[str]:
        url = args.get("url", "").strip()
        if not url:
            return "url cannot be empty"
        if not url.startswith(("http://", "https://")):
            return "url must start with http:// or https://"
        return None

    async def execute(self, args: dict, ctx: Optional[ToolContext] = None) -> str:
        url = args["url"]
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        return f"HTTP {resp.status} fetching {url}"
                    text = await resp.text()
                    return text[:50000]

        except ImportError:
            return "Web fetch unavailable (aiohttp not installed)"
        except Exception as e:
            return f"Fetch failed: {e}"
