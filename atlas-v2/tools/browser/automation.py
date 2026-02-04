"""
Browser Automation
Playwright-based web automation for research and visual verification.
Integrates with v1 (~/.atlas/logs/screenshots/).
"""

import asyncio
import random
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

@dataclass
class BrowseResult:
    """Result of a browse operation"""
    url: str
    title: str
    content: str
    screenshot_path: Optional[Path] = None
    links: List[str] = None
    error: Optional[str] = None
    load_time: float = 0

@dataclass
class SearchResult:
    """A search result"""
    title: str
    url: str
    snippet: str

class BrowserAutomation:
    """
    Browser automation for web research and verification.
    Uses Playwright for headless browsing.
    """

    # Domains that should never be visited
    BLOCKED_DOMAINS = {
        'localhost', '127.0.0.1', '0.0.0.0',
        'internal', 'intranet', 'corp',
    }

    # User agents for rotation
    USER_AGENTS = [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]

    def __init__(
        self,
        headless: bool = True,
        screenshot_dir: Optional[Path] = None,
        timeout: int = 30000
    ):
        self.headless = headless
        self.timeout = timeout

        # Use v1 screenshots dir if available
        v1_screenshots = Path.home() / ".atlas" / "logs" / "screenshots"
        self.screenshot_dir = screenshot_dir or v1_screenshots
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self._browser = None
        self._context = None

    def _is_safe_url(self, url: str) -> bool:
        """Check if URL is safe to visit"""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""

            # Check blocked domains
            for blocked in self.BLOCKED_DOMAINS:
                if blocked in hostname.lower():
                    return False

            # Only allow http/https
            if parsed.scheme not in ('http', 'https'):
                return False

            return True

        except Exception:
            return False

    async def _ensure_browser(self):
        """Ensure browser is launched"""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless
                )
                self._context = await self._browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=random.choice(self.USER_AGENTS)
                )
            except ImportError:
                raise RuntimeError(
                    "Playwright not installed. Run: pip install playwright && playwright install chromium"
                )

    async def browse(
        self,
        url: str,
        take_screenshot: bool = False,
        wait_for_selector: Optional[str] = None
    ) -> BrowseResult:
        """Browse a URL and extract content"""
        if not self._is_safe_url(url):
            return BrowseResult(
                url=url,
                title="",
                content="",
                error=f"URL blocked for safety: {url}"
            )

        try:
            await self._ensure_browser()
            page = await self._context.new_page()

            start_time = time.time()

            # Navigate
            response = await page.goto(url, timeout=self.timeout)
            load_time = time.time() - start_time

            # Wait for selector if specified
            if wait_for_selector:
                await page.wait_for_selector(wait_for_selector, timeout=5000)

            # Human-like delay
            await asyncio.sleep(random.uniform(1, 2))

            # Extract content
            title = await page.title()
            content = await page.inner_text('body')

            # Get links
            links = await page.eval_on_selector_all(
                'a[href]',
                'elements => elements.map(e => e.href).slice(0, 50)'
            )

            # Screenshot
            screenshot_path = None
            if take_screenshot:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_url = url.replace('/', '_').replace(':', '')[:50]
                screenshot_path = self.screenshot_dir / f"{timestamp}_{safe_url}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)

            await page.close()

            return BrowseResult(
                url=url,
                title=title,
                content=content[:50000],  # Limit content size
                screenshot_path=screenshot_path,
                links=links,
                load_time=load_time
            )

        except Exception as e:
            return BrowseResult(
                url=url,
                title="",
                content="",
                error=str(e)
            )

    async def search(
        self,
        query: str,
        num_results: int = 5
    ) -> List[SearchResult]:
        """Search the web and return results"""
        results = []

        try:
            await self._ensure_browser()
            page = await self._context.new_page()

            # Use DuckDuckGo (no CAPTCHA issues)
            search_url = f"https://duckduckgo.com/?q={query.replace(' ', '+')}"
            await page.goto(search_url, timeout=self.timeout)

            # Wait for results
            await asyncio.sleep(random.uniform(2, 3))

            # Extract results
            result_elements = await page.query_selector_all('.result__body')

            for element in result_elements[:num_results]:
                try:
                    title_el = await element.query_selector('.result__a')
                    snippet_el = await element.query_selector('.result__snippet')

                    if title_el:
                        title = await title_el.inner_text()
                        url = await title_el.get_attribute('href')
                        snippet = await snippet_el.inner_text() if snippet_el else ""

                        results.append(SearchResult(
                            title=title,
                            url=url,
                            snippet=snippet
                        ))
                except Exception:
                    continue

            await page.close()

        except Exception as e:
            print(f"Search error: {e}")

        return results

    async def verify_visual(
        self,
        url: str,
        expected_elements: List[str] = None
    ) -> Dict[str, Any]:
        """Verify a page visually for quality assurance"""
        result = {
            "url": url,
            "verified": False,
            "screenshot": None,
            "errors": [],
            "warnings": []
        }

        browse_result = await self.browse(url, take_screenshot=True)

        if browse_result.error:
            result["errors"].append(browse_result.error)
            return result

        result["screenshot"] = str(browse_result.screenshot_path)

        # Check for expected elements
        if expected_elements:
            for element in expected_elements:
                if element.lower() not in browse_result.content.lower():
                    result["warnings"].append(f"Expected element not found: {element}")

        # Basic quality checks
        if len(browse_result.content) < 100:
            result["warnings"].append("Page content seems very short")

        if "error" in browse_result.title.lower() or "404" in browse_result.title:
            result["errors"].append("Page appears to be an error page")

        result["verified"] = len(result["errors"]) == 0
        return result

    async def close(self):
        """Close the browser"""
        if self._browser:
            await self._browser.close()
            await self._playwright.stop()
            self._browser = None
            self._context = None

    def __del__(self):
        """Cleanup on deletion"""
        if self._browser:
            asyncio.get_event_loop().run_until_complete(self.close())
