"""
Notion Tool - ClawHub Pattern Implementation

MCP-compatible tool for creating and managing Notion pages, databases, and blocks.
Based on the ClawHub steipete/notion tool pattern.

Usage:
    notion = NotionTool(api_key="secret_xxx")
    await notion.create_page(parent_id="...", title="...", content="...")
    await notion.query_database(database_id="...", filter={...})
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
import aiohttp

logger = logging.getLogger(__name__)


class NotionBlockType(str, Enum):
    """Notion block types"""
    PARAGRAPH = "paragraph"
    HEADING_1 = "heading_1"
    HEADING_2 = "heading_2"
    HEADING_3 = "heading_3"
    BULLETED_LIST = "bulleted_list_item"
    NUMBERED_LIST = "numbered_list_item"
    TO_DO = "to_do"
    TOGGLE = "toggle"
    CODE = "code"
    QUOTE = "quote"
    CALLOUT = "callout"
    DIVIDER = "divider"
    TABLE = "table"
    IMAGE = "image"
    BOOKMARK = "bookmark"


class NotionPropertyType(str, Enum):
    """Notion database property types"""
    TITLE = "title"
    RICH_TEXT = "rich_text"
    NUMBER = "number"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    DATE = "date"
    CHECKBOX = "checkbox"
    URL = "url"
    EMAIL = "email"
    PHONE = "phone_number"
    STATUS = "status"
    RELATION = "relation"


@dataclass
class NotionPage:
    """Represents a Notion page"""
    id: str
    url: str
    title: str
    created_time: datetime
    last_edited_time: datetime
    parent_type: str
    parent_id: str
    properties: Dict[str, Any] = field(default_factory=dict)
    content: List[Dict] = field(default_factory=list)


@dataclass
class NotionDatabase:
    """Represents a Notion database"""
    id: str
    url: str
    title: str
    properties: Dict[str, Dict]
    created_time: datetime
    last_edited_time: datetime


@dataclass
class ToolResult:
    """Standard tool result format"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class NotionTool:
    """
    Notion API integration tool.

    Capabilities:
    - Create/update/delete pages
    - Query/create databases
    - Manage blocks (content)
    - Search across workspace

    MCP-compatible interface for agent integration.
    """

    BASE_URL = "https://api.notion.com/v1"
    API_VERSION = "2022-06-28"

    def __init__(
        self,
        api_key: str = None,
        api_key_env: str = "NOTION_API_KEY",
    ):
        import os
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self._session: Optional[aiohttp.ClientSession] = None

        if not self.api_key:
            logger.warning("Notion API key not configured")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Notion-Version": self.API_VERSION,
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

    # =========================================================================
    # MCP Tool Interface
    # =========================================================================

    def get_tools(self) -> List[Dict]:
        """Return MCP-compatible tool definitions"""
        return [
            {
                "name": "notion_create_page",
                "description": "Create a new Notion page",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "parent_id": {"type": "string", "description": "Parent page or database ID"},
                        "title": {"type": "string", "description": "Page title"},
                        "content": {"type": "string", "description": "Page content (markdown)"},
                        "properties": {"type": "object", "description": "Database properties (if parent is database)"},
                    },
                    "required": ["parent_id", "title"],
                },
            },
            {
                "name": "notion_get_page",
                "description": "Get a Notion page by ID",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"},
                    },
                    "required": ["page_id"],
                },
            },
            {
                "name": "notion_update_page",
                "description": "Update a Notion page",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"},
                        "properties": {"type": "object", "description": "Properties to update"},
                    },
                    "required": ["page_id"],
                },
            },
            {
                "name": "notion_query_database",
                "description": "Query a Notion database",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "string", "description": "Database ID"},
                        "filter": {"type": "object", "description": "Filter conditions"},
                        "sorts": {"type": "array", "description": "Sort conditions"},
                    },
                    "required": ["database_id"],
                },
            },
            {
                "name": "notion_search",
                "description": "Search across Notion workspace",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "filter": {"type": "object", "description": "Filter by object type"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "notion_append_blocks",
                "description": "Append blocks to a page",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"},
                        "blocks": {"type": "array", "description": "Blocks to append"},
                    },
                    "required": ["page_id", "blocks"],
                },
            },
        ]

    async def call_tool(self, name: str, arguments: Dict) -> ToolResult:
        """MCP-compatible tool invocation"""
        tool_map = {
            "notion_create_page": self.create_page,
            "notion_get_page": self.get_page,
            "notion_update_page": self.update_page,
            "notion_query_database": self.query_database,
            "notion_search": self.search,
            "notion_append_blocks": self.append_blocks,
        }

        handler = tool_map.get(name)
        if not handler:
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        try:
            result = await handler(**arguments)
            return ToolResult(success=True, data=result)
        except Exception as e:
            logger.error(f"Notion tool error: {e}")
            return ToolResult(success=False, error=str(e))

    # =========================================================================
    # Page Operations
    # =========================================================================

    async def create_page(
        self,
        parent_id: str,
        title: str,
        content: str = None,
        properties: Dict = None,
        icon: str = None,
        cover: str = None,
    ) -> Dict:
        """Create a new Notion page"""
        session = await self._get_session()

        # Determine parent type
        parent = {"page_id": parent_id} if "-" in parent_id else {"database_id": parent_id}

        # Build page data
        data = {
            "parent": parent,
            "properties": properties or {
                "title": {
                    "title": [{"text": {"content": title}}]
                }
            },
        }

        if icon:
            data["icon"] = {"emoji": icon}
        if cover:
            data["cover"] = {"external": {"url": cover}}

        # Create page
        async with session.post(f"{self.BASE_URL}/pages", json=data) as resp:
            result = await resp.json()
            if resp.status != 200:
                raise Exception(f"Failed to create page: {result.get('message', 'Unknown error')}")

        page_id = result["id"]

        # Add content if provided
        if content:
            blocks = self._markdown_to_blocks(content)
            await self.append_blocks(page_id, blocks)

        return result

    async def get_page(self, page_id: str) -> Dict:
        """Get a Notion page by ID"""
        session = await self._get_session()

        async with session.get(f"{self.BASE_URL}/pages/{page_id}") as resp:
            result = await resp.json()
            if resp.status != 200:
                raise Exception(f"Failed to get page: {result.get('message', 'Unknown error')}")
            return result

    async def update_page(
        self,
        page_id: str,
        properties: Dict = None,
        archived: bool = None,
        icon: str = None,
        cover: str = None,
    ) -> Dict:
        """Update a Notion page"""
        session = await self._get_session()

        data = {}
        if properties:
            data["properties"] = properties
        if archived is not None:
            data["archived"] = archived
        if icon:
            data["icon"] = {"emoji": icon}
        if cover:
            data["cover"] = {"external": {"url": cover}}

        async with session.patch(f"{self.BASE_URL}/pages/{page_id}", json=data) as resp:
            result = await resp.json()
            if resp.status != 200:
                raise Exception(f"Failed to update page: {result.get('message', 'Unknown error')}")
            return result

    async def delete_page(self, page_id: str) -> Dict:
        """Archive (soft delete) a Notion page"""
        return await self.update_page(page_id, archived=True)

    # =========================================================================
    # Database Operations
    # =========================================================================

    async def query_database(
        self,
        database_id: str,
        filter: Dict = None,
        sorts: List[Dict] = None,
        start_cursor: str = None,
        page_size: int = 100,
    ) -> Dict:
        """Query a Notion database"""
        session = await self._get_session()

        data = {"page_size": min(page_size, 100)}
        if filter:
            data["filter"] = filter
        if sorts:
            data["sorts"] = sorts
        if start_cursor:
            data["start_cursor"] = start_cursor

        async with session.post(
            f"{self.BASE_URL}/databases/{database_id}/query",
            json=data
        ) as resp:
            result = await resp.json()
            if resp.status != 200:
                raise Exception(f"Failed to query database: {result.get('message', 'Unknown error')}")
            return result

    async def get_database(self, database_id: str) -> Dict:
        """Get a Notion database by ID"""
        session = await self._get_session()

        async with session.get(f"{self.BASE_URL}/databases/{database_id}") as resp:
            result = await resp.json()
            if resp.status != 200:
                raise Exception(f"Failed to get database: {result.get('message', 'Unknown error')}")
            return result

    # =========================================================================
    # Block Operations
    # =========================================================================

    async def get_blocks(self, page_id: str) -> List[Dict]:
        """Get all blocks from a page"""
        session = await self._get_session()
        blocks = []
        start_cursor = None

        while True:
            url = f"{self.BASE_URL}/blocks/{page_id}/children"
            if start_cursor:
                url += f"?start_cursor={start_cursor}"

            async with session.get(url) as resp:
                result = await resp.json()
                if resp.status != 200:
                    raise Exception(f"Failed to get blocks: {result.get('message', 'Unknown error')}")

                blocks.extend(result.get("results", []))

                if not result.get("has_more"):
                    break
                start_cursor = result.get("next_cursor")

        return blocks

    async def append_blocks(
        self,
        page_id: str,
        blocks: List[Dict],
    ) -> Dict:
        """Append blocks to a page"""
        session = await self._get_session()

        async with session.patch(
            f"{self.BASE_URL}/blocks/{page_id}/children",
            json={"children": blocks}
        ) as resp:
            result = await resp.json()
            if resp.status != 200:
                raise Exception(f"Failed to append blocks: {result.get('message', 'Unknown error')}")
            return result

    # =========================================================================
    # Search
    # =========================================================================

    async def search(
        self,
        query: str,
        filter: Dict = None,
        sort: Dict = None,
        start_cursor: str = None,
        page_size: int = 100,
    ) -> Dict:
        """Search across Notion workspace"""
        session = await self._get_session()

        data = {"query": query, "page_size": min(page_size, 100)}
        if filter:
            data["filter"] = filter
        if sort:
            data["sort"] = sort
        if start_cursor:
            data["start_cursor"] = start_cursor

        async with session.post(f"{self.BASE_URL}/search", json=data) as resp:
            result = await resp.json()
            if resp.status != 200:
                raise Exception(f"Search failed: {result.get('message', 'Unknown error')}")
            return result

    # =========================================================================
    # Helpers
    # =========================================================================

    def _markdown_to_blocks(self, markdown: str) -> List[Dict]:
        """Convert markdown to Notion blocks"""
        blocks = []
        lines = markdown.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Headers
            if line.startswith("### "):
                blocks.append({
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"text": {"content": line[4:]}}]
                    }
                })
            elif line.startswith("## "):
                blocks.append({
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"text": {"content": line[3:]}}]
                    }
                })
            elif line.startswith("# "):
                blocks.append({
                    "type": "heading_1",
                    "heading_1": {
                        "rich_text": [{"text": {"content": line[2:]}}]
                    }
                })
            # Bullet lists
            elif line.startswith("- ") or line.startswith("* "):
                blocks.append({
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"text": {"content": line[2:]}}]
                    }
                })
            # Numbered lists
            elif line[0].isdigit() and ". " in line[:4]:
                content = line.split(". ", 1)[1] if ". " in line else line
                blocks.append({
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": [{"text": {"content": content}}]
                    }
                })
            # Code blocks
            elif line.startswith("```"):
                # Skip code block markers (simplified)
                continue
            # Quotes
            elif line.startswith("> "):
                blocks.append({
                    "type": "quote",
                    "quote": {
                        "rich_text": [{"text": {"content": line[2:]}}]
                    }
                })
            # To-do items
            elif line.startswith("- [ ] "):
                blocks.append({
                    "type": "to_do",
                    "to_do": {
                        "rich_text": [{"text": {"content": line[6:]}}],
                        "checked": False
                    }
                })
            elif line.startswith("- [x] "):
                blocks.append({
                    "type": "to_do",
                    "to_do": {
                        "rich_text": [{"text": {"content": line[6:]}}],
                        "checked": True
                    }
                })
            # Dividers
            elif line in ["---", "***", "___"]:
                blocks.append({"type": "divider", "divider": {}})
            # Default to paragraph
            else:
                blocks.append({
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": line}}]
                    }
                })

        return blocks

    def _rich_text(self, content: str) -> List[Dict]:
        """Create rich text array"""
        return [{"text": {"content": content}}]


# =============================================================================
# Skill Registration
# =============================================================================

def get_notion_tool(api_key: str = None) -> NotionTool:
    """Factory function for NotionTool"""
    return NotionTool(api_key=api_key)


def should_trigger(user_input: str, context: Dict = None) -> bool:
    """Auto-trigger detection for notion operations"""
    triggers = [
        "notion", "create page", "add to notion", "notion database",
        "save to notion", "notion doc", "notion note",
    ]
    input_lower = user_input.lower()
    return any(t in input_lower for t in triggers)


# MCP registration helper
SKILL_MANIFEST = {
    "name": "notion",
    "version": "1.0.0",
    "description": "Notion API for creating and managing pages, databases, and blocks",
    "author": "ABLE",
    "triggers": ["notion", "create page", "add to notion"],
    "capabilities": [
        "notion_create_page",
        "notion_get_page",
        "notion_update_page",
        "notion_query_database",
        "notion_search",
        "notion_append_blocks",
    ],
}
