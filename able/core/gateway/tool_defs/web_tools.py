"""
Web research tool definitions and handlers.
Includes: web_search, web_fetch, deep_research.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from able.core.gateway.tool_registry import ToolRegistry, ToolContext

logger = logging.getLogger(__name__)


# ── Tool Definitions ──────────────────────────────────────────────────────────

WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information. Use for any question about recent events, prices, people, companies, technology, etc. Returns structured results from Brave/Perplexity/Gemini/Google.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query — be specific and descriptive"},
                "max_results": {"type": "integer", "description": "Max results to return (default 5, max 10)"},
            },
            "required": ["query"],
        },
    },
}

WEB_FETCH = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "Fetch and extract readable content from a specific URL. Returns the page content as clean text. Use when you have a URL and need to read its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch content from"},
            },
            "required": ["url"],
        },
    },
}

DEEP_RESEARCH = {
    "type": "function",
    "function": {
        "name": "deep_research",
        "description": "Perform deep research on a topic using Perplexity Sonar Pro or Gemini Grounding. Returns a comprehensive, cited analysis. Use for complex questions requiring thorough investigation with verified sources.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research question — be detailed about what you want to learn"},
            },
            "required": ["query"],
        },
    },
}


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_web_search(args: dict, ctx: "ToolContext") -> str:
    web_search = ctx.metadata["web_search"]
    query = args.get("query", "")
    max_results = min(args.get("max_results", 5), 10)
    response = await web_search.search(query, max_results=max_results)
    return web_search.format_for_llm(response)


_MAX_REDIRECTS = 5

async def handle_web_fetch(args: dict, ctx: "ToolContext") -> str:
    """Fetch URL with SSRF-safe redirect following.

    Validates each redirect hop against cloud metadata endpoints, CGNAT,
    and link-local ranges. Drops response body when redirected to an
    untrusted domain (prevents data exfiltration via redirect chain).
    """
    url = args.get("url", "")
    if not url:
        return "⚠️ No URL provided"
    try:
        import aiohttp
        from bs4 import BeautifulSoup
        from able.core.security.egress_inspector import EgressInspector

        # Initial URL validation
        if not EgressInspector.validate_redirect_target(url):
            return "⚠️ Blocked: URL targets a restricted address (cloud metadata / internal network)"

        async with aiohttp.ClientSession() as session:
            current_url = url
            for _hop in range(_MAX_REDIRECTS):
                async with session.get(
                    current_url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; ABLEBot/2.0)"},
                    allow_redirects=False,
                ) as resp:
                    # Not a redirect — process response
                    if resp.status not in (301, 302, 303, 307, 308):
                        if resp.status != 200:
                            return f"⚠️ Failed to fetch URL: HTTP {resp.status}"
                        html = await resp.text()
                        break

                    # Redirect — validate target before following
                    location = resp.headers.get("Location", "")
                    if not location:
                        return "⚠️ Redirect with no Location header"
                    # Resolve relative redirects
                    from urllib.parse import urljoin
                    location = urljoin(current_url, location)

                    if not EgressInspector.validate_redirect_target(location):
                        # Body dropped — redirect to restricted address
                        logger.warning("SSRF: redirect to restricted %s (from %s)", location, current_url)
                        return "⚠️ Blocked: redirect targets a restricted address"

                    current_url = location
            else:
                return "⚠️ Too many redirects"

        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        if len(text) > 8000:
            text = text[:8000] + "\n\n[... content truncated at 8000 chars ...]"
        return f"Content from {current_url}:\n\n{text}"
    except Exception as e:
        return f"⚠️ Failed to fetch URL: {e}"


async def handle_deep_research(args: dict, ctx: "ToolContext") -> str:
    web_search = ctx.metadata["web_search"]
    query = args.get("query", "")
    result = await web_search.deep_research(query)
    answer = result.get("answer", "No results")
    citations = result.get("citations", [])
    model = result.get("model", "unknown")
    output = f"## Deep Research (via {model})\n\n{answer}"
    if citations:
        output += "\n\n### Sources\n"
        for i, url in enumerate(citations):
            if isinstance(url, str):
                output += f"[{i+1}] {url}\n"
    return output


# ── Registration ──────────────────────────────────────────────────────────────

def register_tools(registry: "ToolRegistry"):
    """Register all web research tools with the registry."""
    registry.register(
        name="web_search",
        definition=WEB_SEARCH,
        handler=handle_web_search,
        display_name="Web / Search",
        requires_approval=False,
        category="search-fetch",
        read_only=True,
        concurrent_safe=True,
        surface="web",
        artifact_kind="markdown",
        tags=["web", "research"],
    )
    registry.register(
        name="web_fetch",
        definition=WEB_FETCH,
        handler=handle_web_fetch,
        display_name="Web / Fetch URL",
        requires_approval=False,
        category="search-fetch",
        read_only=True,
        concurrent_safe=True,
        surface="web",
        artifact_kind="markdown",
        tags=["web", "fetch"],
    )
    registry.register(
        name="deep_research",
        definition=DEEP_RESEARCH,
        handler=handle_deep_research,
        display_name="Web / Deep Research",
        requires_approval=False,
        category="search-fetch",
        read_only=True,
        concurrent_safe=False,
        surface="research",
        artifact_kind="markdown",
        tags=["web", "research", "analysis"],
    )
