"""
ATLAS Auto-Learner - Automated knowledge acquisition from external sources.

Periodically scrapes and learns from:
- ClawHub.ai/skills (agent skills marketplace)
- Twitter/X (AI/agent trends)
- GitHub trending repos
- HackerNews, Reddit r/LocalLLaMA
- Research papers (arXiv)

Schedule: Every other night (configurable)

Workflow:
1. Scrape sources for relevant content
2. Filter and rank by relevance to ATLAS capabilities
3. Extract actionable insights
4. Route high-value insights through self-improvement
5. Optionally propose new skills or prompt improvements
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Awaitable
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# Optional imports
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


class ContentSource(Enum):
    """Known content sources"""
    CLAWHUB = "clawhub"
    GITHUB_TRENDING = "github_trending"
    HACKERNEWS = "hackernews"
    REDDIT = "reddit"
    TWITTER = "twitter"
    ARXIV = "arxiv"
    RSS = "rss"
    CUSTOM = "custom"


@dataclass
class ScrapedContent:
    """Content scraped from an external source"""
    id: str
    source: ContentSource
    url: str
    title: str
    content: str
    timestamp: float
    metadata: Dict = field(default_factory=dict)
    relevance_score: float = 0.0
    processed: bool = False


@dataclass
class LearningInsight:
    """An actionable insight extracted from scraped content"""
    id: str
    source_id: str
    source_url: str
    insight_type: str          # skill_idea, optimization, pattern, trend, etc.
    title: str
    description: str
    actionable: bool
    confidence: float
    suggested_actions: List[str]
    tags: List[str]
    created_at: float = field(default_factory=time.time)


@dataclass
class SourceConfig:
    """Configuration for a content source"""
    source: ContentSource
    enabled: bool = True
    url: str = ""
    scrape_interval_hours: int = 48  # Every other day
    last_scraped: Optional[float] = None
    filters: Dict = field(default_factory=dict)
    max_items: int = 50


class ContentScraper:
    """
    Base scraper with common functionality.

    Each source has specific parsing logic but shares:
    - Rate limiting
    - Caching
    - Error handling
    - Content normalization
    """

    USER_AGENT = "ATLAS-Agent/2.0 (Autonomous Learning System)"
    REQUEST_TIMEOUT = 30

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, ScrapedContent] = {}
        self._rate_limits: Dict[str, float] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if not AIOHTTP_AVAILABLE:
            raise RuntimeError("aiohttp not installed")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": self.USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)
            )
        return self._session

    async def _fetch_url(self, url: str, delay: float = 1.0) -> str:
        """Fetch URL with rate limiting"""
        domain = urlparse(url).netloc

        # Rate limit per domain
        last_request = self._rate_limits.get(domain, 0)
        elapsed = time.time() - last_request
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)

        session = await self._get_session()
        async with session.get(url) as response:
            self._rate_limits[domain] = time.time()
            if response.status == 200:
                return await response.text()
            else:
                logger.warning(f"Failed to fetch {url}: {response.status}")
                return ""

    def _generate_id(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:12]

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class ClawHubScraper(ContentScraper):
    """
    Scraper for ClawHub.ai skills marketplace.

    ClawHub is a community hub for AI agent skills - perfect for
    discovering new capabilities ATLAS could adopt.
    """

    BASE_URL = "https://clawhub.ai"
    SKILLS_URL = "https://clawhub.ai/skills"

    async def scrape(self, max_items: int = 50) -> List[ScrapedContent]:
        """Scrape skills from ClawHub"""
        results = []

        try:
            html = await self._fetch_url(self.SKILLS_URL)
            if not html or not BS4_AVAILABLE:
                return results

            soup = BeautifulSoup(html, 'html.parser')

            # Look for skill cards/items
            skill_items = soup.select('.skill-card, .skill-item, [data-skill], article')

            for item in skill_items[:max_items]:
                try:
                    # Extract title
                    title_elem = item.select_one('h2, h3, .skill-title, .title')
                    title = title_elem.get_text(strip=True) if title_elem else "Unknown Skill"

                    # Extract description
                    desc_elem = item.select_one('p, .description, .skill-description')
                    description = desc_elem.get_text(strip=True) if desc_elem else ""

                    # Extract link
                    link_elem = item.select_one('a[href]')
                    url = urljoin(self.BASE_URL, link_elem['href']) if link_elem else self.SKILLS_URL

                    # Extract tags
                    tags = [t.get_text(strip=True) for t in item.select('.tag, .label, .category')]

                    content = ScrapedContent(
                        id=self._generate_id(url),
                        source=ContentSource.CLAWHUB,
                        url=url,
                        title=title,
                        content=description,
                        timestamp=time.time(),
                        metadata={"tags": tags}
                    )
                    results.append(content)

                except Exception as e:
                    logger.warning(f"Error parsing ClawHub skill: {e}")
                    continue

        except Exception as e:
            logger.error(f"ClawHub scrape failed: {e}")

        return results


class GitHubTrendingScraper(ContentScraper):
    """Scraper for GitHub trending repositories"""

    TRENDING_URL = "https://github.com/trending"

    async def scrape(
        self,
        language: str = None,
        since: str = "weekly",
        max_items: int = 30
    ) -> List[ScrapedContent]:
        """Scrape trending repos"""
        results = []

        url = self.TRENDING_URL
        if language:
            url = f"{url}/{language}"
        url = f"{url}?since={since}"

        try:
            html = await self._fetch_url(url)
            if not html or not BS4_AVAILABLE:
                return results

            soup = BeautifulSoup(html, 'html.parser')

            for article in soup.select('article.Box-row')[:max_items]:
                try:
                    # Repo name
                    name_elem = article.select_one('h2 a')
                    if not name_elem:
                        continue
                    repo_path = name_elem.get('href', '').strip('/')
                    repo_url = f"https://github.com/{repo_path}"

                    # Description
                    desc_elem = article.select_one('p')
                    description = desc_elem.get_text(strip=True) if desc_elem else ""

                    # Stars
                    stars_elem = article.select_one('a[href*="stargazers"]')
                    stars = stars_elem.get_text(strip=True) if stars_elem else "0"

                    # Language
                    lang_elem = article.select_one('[itemprop="programmingLanguage"]')
                    lang = lang_elem.get_text(strip=True) if lang_elem else "Unknown"

                    content = ScrapedContent(
                        id=self._generate_id(repo_url),
                        source=ContentSource.GITHUB_TRENDING,
                        url=repo_url,
                        title=repo_path,
                        content=description,
                        timestamp=time.time(),
                        metadata={"stars": stars, "language": lang}
                    )
                    results.append(content)

                except Exception as e:
                    logger.warning(f"Error parsing GitHub repo: {e}")

        except Exception as e:
            logger.error(f"GitHub trending scrape failed: {e}")

        return results


class HackerNewsScraper(ContentScraper):
    """Scraper for Hacker News"""

    API_URL = "https://hacker-news.firebaseio.com/v0"

    async def scrape(
        self,
        story_type: str = "topstories",
        max_items: int = 30,
        filter_keywords: List[str] = None
    ) -> List[ScrapedContent]:
        """Scrape HN stories"""
        results = []
        filter_keywords = filter_keywords or [
            "ai", "llm", "agent", "gpt", "claude", "anthropic",
            "automation", "bot", "machine learning"
        ]

        try:
            # Get story IDs
            session = await self._get_session()
            async with session.get(f"{self.API_URL}/{story_type}.json") as resp:
                if resp.status != 200:
                    return results
                story_ids = await resp.json()

            # Fetch individual stories
            for story_id in story_ids[:max_items * 2]:  # Over-fetch for filtering
                try:
                    async with session.get(f"{self.API_URL}/item/{story_id}.json") as resp:
                        if resp.status != 200:
                            continue
                        story = await resp.json()

                    if not story or story.get('type') != 'story':
                        continue

                    title = story.get('title', '')
                    url = story.get('url', f"https://news.ycombinator.com/item?id={story_id}")

                    # Filter by keywords
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in filter_keywords):
                        continue

                    content = ScrapedContent(
                        id=str(story_id),
                        source=ContentSource.HACKERNEWS,
                        url=url,
                        title=title,
                        content=story.get('text', ''),
                        timestamp=story.get('time', time.time()),
                        metadata={
                            "score": story.get('score', 0),
                            "comments": story.get('descendants', 0),
                            "by": story.get('by', '')
                        }
                    )
                    results.append(content)

                    if len(results) >= max_items:
                        break

                    await asyncio.sleep(0.1)  # Rate limit

                except Exception as e:
                    logger.warning(f"Error fetching HN story {story_id}: {e}")

        except Exception as e:
            logger.error(f"HackerNews scrape failed: {e}")

        return results


class AutoLearner:
    """
    Automated learning engine that scrapes sources and extracts insights.

    Schedule:
    - ClawHub: Every other night (skills are updated less frequently)
    - GitHub: Weekly (trending changes weekly)
    - HackerNews: Daily (high velocity)

    Insights are routed to:
    - SelfImprovementEngine for document updates
    - Memory for later retrieval
    - Optionally: Skill creation workflow
    """

    DEFAULT_SOURCES = [
        SourceConfig(
            source=ContentSource.CLAWHUB,
            url="https://clawhub.ai/skills",
            scrape_interval_hours=48,
            max_items=50,
        ),
        SourceConfig(
            source=ContentSource.GITHUB_TRENDING,
            url="https://github.com/trending",
            scrape_interval_hours=168,  # Weekly
            max_items=30,
            filters={"keywords": ["agent", "llm", "ai", "automation"]}
        ),
        SourceConfig(
            source=ContentSource.HACKERNEWS,
            url="https://news.ycombinator.com",
            scrape_interval_hours=24,
            max_items=20,
        ),
    ]

    # Keywords that indicate high relevance to ATLAS
    RELEVANCE_KEYWORDS = {
        "high": ["agent", "autonomous", "claude", "anthropic", "skill", "tool", "automation", "self-improving"],
        "medium": ["llm", "gpt", "ai", "ml", "bot", "assistant", "pipeline"],
        "low": ["machine learning", "neural", "model", "api", "integration"]
    }

    def __init__(
        self,
        sources: List[SourceConfig] = None,
        self_improvement: 'SelfImprovementEngine' = None,
        memory=None,
        fact_checker=None,
    ):
        self.sources = sources or self.DEFAULT_SOURCES
        self.self_improvement = self_improvement
        self.memory = memory
        self.fact_checker = fact_checker

        self.scrapers = {
            ContentSource.CLAWHUB: ClawHubScraper(),
            ContentSource.GITHUB_TRENDING: GitHubTrendingScraper(),
            ContentSource.HACKERNEWS: HackerNewsScraper(),
        }

        self._scraped_content: List[ScrapedContent] = []
        self._insights: List[LearningInsight] = []
        self._running = False

    def _calculate_relevance(self, content: ScrapedContent) -> float:
        """Calculate relevance score based on keywords"""
        text = f"{content.title} {content.content}".lower()
        score = 0.0

        for word in self.RELEVANCE_KEYWORDS["high"]:
            if word in text:
                score += 0.3

        for word in self.RELEVANCE_KEYWORDS["medium"]:
            if word in text:
                score += 0.15

        for word in self.RELEVANCE_KEYWORDS["low"]:
            if word in text:
                score += 0.05

        return min(1.0, score)

    def _extract_insight(self, content: ScrapedContent) -> Optional[LearningInsight]:
        """Extract actionable insight from scraped content"""
        if content.relevance_score < 0.3:
            return None

        # Determine insight type based on source and content
        if content.source == ContentSource.CLAWHUB:
            insight_type = "skill_idea"
            actions = [
                f"Consider implementing skill: {content.title}",
                f"Review skill pattern from: {content.url}",
            ]
        elif content.source == ContentSource.GITHUB_TRENDING:
            insight_type = "tool_discovery"
            actions = [
                f"Evaluate repo for integration: {content.title}",
                f"Check for relevant patterns: {content.url}",
            ]
        else:
            insight_type = "trend"
            actions = [
                f"Research topic: {content.title}",
            ]

        # Extract tags from title
        tags = re.findall(r'#(\w+)', content.content)
        tags.extend(content.metadata.get("tags", []))

        return LearningInsight(
            id=f"insight_{content.id}",
            source_id=content.id,
            source_url=content.url,
            insight_type=insight_type,
            title=content.title,
            description=content.content[:500],
            actionable=content.relevance_score > 0.5,
            confidence=content.relevance_score,
            suggested_actions=actions,
            tags=list(set(tags)),
        )

    async def scrape_source(self, config: SourceConfig) -> List[ScrapedContent]:
        """Scrape a single source"""
        scraper = self.scrapers.get(config.source)
        if not scraper:
            logger.warning(f"No scraper for source: {config.source}")
            return []

        logger.info(f"Scraping {config.source.value}...")
        content = await scraper.scrape(max_items=config.max_items)

        # Calculate relevance scores
        for item in content:
            item.relevance_score = self._calculate_relevance(item)

        # Fact-check if available
        if self.fact_checker:
            for item in content:
                try:
                    report = await self.fact_checker.verify_scraped_content(
                        item.url, item.content
                    )
                    if not report.passed:
                        item.relevance_score *= 0.5  # Reduce score for unverified content
                        item.metadata["fact_check_warning"] = report.hallucination_risk
                except Exception:
                    pass

        config.last_scraped = time.time()
        return content

    async def scrape_all(self, force: bool = False) -> List[ScrapedContent]:
        """Scrape all enabled sources that are due"""
        all_content = []
        now = time.time()

        for config in self.sources:
            if not config.enabled:
                continue

            # Check if due for scraping
            if not force and config.last_scraped:
                hours_since = (now - config.last_scraped) / 3600
                if hours_since < config.scrape_interval_hours:
                    continue

            try:
                content = await self.scrape_source(config)
                all_content.extend(content)
            except Exception as e:
                logger.error(f"Failed to scrape {config.source.value}: {e}")

        self._scraped_content.extend(all_content)
        return all_content

    async def process_insights(self) -> List[LearningInsight]:
        """Extract and process insights from scraped content"""
        insights = []

        for content in self._scraped_content:
            if content.processed:
                continue

            insight = self._extract_insight(content)
            if insight:
                insights.append(insight)

                # Store in memory if available
                if self.memory:
                    try:
                        await self.memory.store(
                            content=f"{insight.title}\n\n{insight.description}",
                            memory_type="LEARNING",
                            metadata={
                                "source": insight.source_url,
                                "insight_type": insight.insight_type,
                                "tags": insight.tags,
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to store insight: {e}")

                # Route high-value insights to self-improvement
                if insight.actionable and self.self_improvement:
                    try:
                        await self.self_improvement.add_learning(
                            content=f"**{insight.title}**\n\n{insight.description}\n\n"
                                    f"Source: {insight.source_url}\n"
                                    f"Suggested actions:\n" +
                                    "\n".join(f"- {a}" for a in insight.suggested_actions),
                            category=insight.insight_type,
                            source=f"auto_learner:{insight.source_url[:50]}",
                        )
                    except Exception as e:
                        logger.warning(f"Failed to record learning: {e}")

            content.processed = True

        self._insights.extend(insights)
        return insights

    async def run_learning_cycle(self) -> Dict[str, Any]:
        """Run a complete learning cycle"""
        start = time.time()

        # Scrape
        content = await self.scrape_all()

        # Process
        insights = await self.process_insights()

        elapsed = time.time() - start

        result = {
            "scraped_items": len(content),
            "insights_extracted": len(insights),
            "high_value_insights": sum(1 for i in insights if i.actionable),
            "duration_s": elapsed,
            "sources_scraped": [s.source.value for s in self.sources if s.last_scraped],
        }

        logger.info(
            f"🎓 Learning cycle complete: {result['scraped_items']} items → "
            f"{result['insights_extracted']} insights ({result['high_value_insights']} high-value)"
        )

        return result

    async def start_background_learning(self, check_interval_hours: float = 6):
        """Start background learning loop"""
        self._running = True
        logger.info("🎓 Auto-learner started")

        while self._running:
            try:
                await self.run_learning_cycle()
            except Exception as e:
                logger.error(f"Learning cycle failed: {e}")

            # Sleep until next check
            await asyncio.sleep(check_interval_hours * 3600)

    async def stop(self):
        """Stop background learning"""
        self._running = False
        for scraper in self.scrapers.values():
            await scraper.close()

    def get_recent_insights(self, limit: int = 20) -> List[Dict]:
        """Get recent insights"""
        recent = sorted(self._insights, key=lambda i: i.created_at, reverse=True)
        return [
            {
                "id": i.id,
                "type": i.insight_type,
                "title": i.title,
                "confidence": i.confidence,
                "actionable": i.actionable,
                "source": i.source_url,
            }
            for i in recent[:limit]
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get auto-learner statistics"""
        return {
            "total_scraped": len(self._scraped_content),
            "total_insights": len(self._insights),
            "actionable_insights": sum(1 for i in self._insights if i.actionable),
            "sources": {
                s.source.value: {
                    "enabled": s.enabled,
                    "last_scraped": s.last_scraped,
                    "interval_hours": s.scrape_interval_hours,
                }
                for s in self.sources
            }
        }
