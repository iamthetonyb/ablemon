"""
Markdown Memory — PicoClaw Pattern.

Human-readable, git-friendly memory storage using markdown files.
Complements hybrid_memory.py (SQLite + vector) with human-inspectable files.

Usage:
    from able.memory.markdown_memory import MarkdownMemory

    memory = MarkdownMemory(Path("~/.able/memory"))
    await memory.add_learning("User prefers weekly invoices", category="preferences")
    results = await memory.search_learnings("invoice")
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except ImportError:
    AIOFILES_AVAILABLE = False


class MarkdownMemory:
    """
    Human-readable, git-friendly memory storage.

    Files:
    - LEARNINGS.md: Accumulated insights, patterns, mistakes
    - PREFERENCES.md: User/operator preferences
    - IDENTITY.md: System identity and configuration
    - SKILLS.md: Skill usage and optimization notes

    Benefits:
    - Human-readable (no database required to inspect)
    - Git-friendly (diffs show exactly what changed)
    - Portable (just copy the markdown files)
    - Searchable with grep
    """

    def __init__(self, base_path: Path):
        self.base_path = Path(base_path).expanduser().resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.files = {
            "learnings": self.base_path / "learnings.md",
            "preferences": self.base_path / "preferences.md",
            "identity": self.base_path / "identity.md",
            "skills": self.base_path / "skills.md",
        }

        # Ensure files exist with headers
        for name, path in self.files.items():
            if not path.exists():
                self._init_file(name, path)

    def _init_file(self, name: str, path: Path):
        """Initialize a markdown file with header"""
        headers = {
            "learnings": "# ABLE Learnings\n\nAccumulated insights, patterns, and lessons learned.\n\n---\n",
            "preferences": "# Operator Preferences\n\nPreferences and configurations learned over time.\n\n---\n",
            "identity": "# System Identity\n\nCore identity and behavioral configuration.\n\n---\n",
            "skills": "# Skill Notes\n\nSkill usage statistics and optimization notes.\n\n---\n",
        }
        with open(path, "w") as f:
            f.write(headers.get(name, f"# {name.title()}\n\n---\n"))

    async def _read_file(self, file_key: str) -> str:
        """Read a markdown file"""
        path = self.files.get(file_key)
        if not path or not path.exists():
            return ""

        if AIOFILES_AVAILABLE:
            async with aiofiles.open(path, "r") as f:
                return await f.read()
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, path.read_text)

    async def _append_file(self, file_key: str, content: str):
        """Append to a markdown file"""
        path = self.files.get(file_key)
        if not path:
            return

        if AIOFILES_AVAILABLE:
            async with aiofiles.open(path, "a") as f:
                await f.write(content)
        else:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: path.open("a").write(content))

    # ─────────────────────────────────────────────────────────────────────────
    # Learnings
    # ─────────────────────────────────────────────────────────────────────────

    async def add_learning(
        self,
        content: str,
        category: str = "General",
        source: Optional[str] = None,
    ):
        """
        Add a learning in human-readable format.

        Args:
            content: The learning content
            category: Category (General, Error, Pattern, Optimization, etc.)
            source: Optional source (task, conversation, etc.)
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {timestamp}\n\n"
        entry += f"**Category**: {category}\n"
        if source:
            entry += f"**Source**: {source}\n"
        entry += f"\n{content}\n"
        entry += "\n---\n"

        await self._append_file("learnings", entry)

    async def search_learnings(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Search learnings with simple text matching.

        Returns list of: {"timestamp": "...", "category": "...", "content": "..."}
        """
        content = await self._read_file("learnings")
        return self._parse_and_search(content, query, limit)

    async def get_recent_learnings(self, limit: int = 5) -> List[Dict]:
        """Get most recent learnings"""
        content = await self._read_file("learnings")
        entries = self._parse_entries(content)
        return entries[-limit:][::-1]  # Reverse for newest first

    # ─────────────────────────────────────────────────────────────────────────
    # Preferences
    # ─────────────────────────────────────────────────────────────────────────

    async def set_preference(self, key: str, value: str, reason: Optional[str] = None):
        """
        Set or update a preference.

        Args:
            key: Preference key (e.g., "invoice_frequency")
            value: Preference value
            reason: Optional reason for the preference
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {key}\n\n"
        entry += f"**Value**: {value}\n"
        entry += f"**Updated**: {timestamp}\n"
        if reason:
            entry += f"**Reason**: {reason}\n"
        entry += "\n---\n"

        # For preferences, we update in place if key exists
        content = await self._read_file("preferences")

        # Check if key section exists
        pattern = rf"## {re.escape(key)}\n.*?(?=\n## |\Z)"
        if re.search(pattern, content, re.DOTALL):
            # Replace existing
            new_content = re.sub(pattern, entry.strip() + "\n", content, flags=re.DOTALL)
            path = self.files["preferences"]
            if AIOFILES_AVAILABLE:
                async with aiofiles.open(path, "w") as f:
                    await f.write(new_content)
            else:
                path.write_text(new_content)
        else:
            # Append new
            await self._append_file("preferences", entry)

    async def get_preference(self, key: str) -> Optional[str]:
        """Get a preference value by key"""
        content = await self._read_file("preferences")
        pattern = rf"## {re.escape(key)}\n.*?\*\*Value\*\*:\s*(.+?)(?:\n|$)"
        match = re.search(pattern, content, re.DOTALL)
        return match.group(1).strip() if match else None

    async def get_all_preferences(self) -> Dict[str, str]:
        """Get all preferences as a dict"""
        content = await self._read_file("preferences")
        prefs = {}

        for match in re.finditer(r"## (.+?)\n.*?\*\*Value\*\*:\s*(.+?)(?:\n|$)", content, re.DOTALL):
            prefs[match.group(1).strip()] = match.group(2).strip()

        return prefs

    # ─────────────────────────────────────────────────────────────────────────
    # Skills
    # ─────────────────────────────────────────────────────────────────────────

    async def record_skill_usage(
        self,
        skill_name: str,
        success: bool,
        duration_ms: Optional[int] = None,
        notes: Optional[str] = None,
    ):
        """Record skill usage for optimization tracking"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        status = "✅" if success else "❌"
        entry = f"\n### {timestamp} — {skill_name} {status}\n\n"
        if duration_ms:
            entry += f"**Duration**: {duration_ms}ms\n"
        if notes:
            entry += f"**Notes**: {notes}\n"
        entry += "\n"

        await self._append_file("skills", entry)

    async def get_skill_stats(self, skill_name: str) -> Dict:
        """Get usage stats for a skill"""
        content = await self._read_file("skills")
        pattern = rf"### .+? — {re.escape(skill_name)} (✅|❌)"
        matches = re.findall(pattern, content)

        success = matches.count("✅")
        failure = matches.count("❌")
        total = success + failure

        return {
            "skill": skill_name,
            "total_uses": total,
            "success": success,
            "failure": failure,
            "success_rate": success / total if total > 0 else 0.0,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_entries(self, content: str) -> List[Dict]:
        """Parse markdown content into entry dicts"""
        entries = []
        pattern = r"## (.+?)\n(.*?)(?=\n## |\Z)"

        for match in re.finditer(pattern, content, re.DOTALL):
            header = match.group(1).strip()
            body = match.group(2).strip()

            entry = {"header": header, "content": body}

            # Extract metadata
            for line in body.split("\n"):
                if line.startswith("**") and "**:" in line:
                    key = line.split("**")[1].lower()
                    value = line.split("**:")[1].strip()
                    entry[key] = value

            entries.append(entry)

        return entries

    def _parse_and_search(self, content: str, query: str, limit: int) -> List[Dict]:
        """Parse and search entries"""
        entries = self._parse_entries(content)
        query_lower = query.lower()

        matches = [
            e for e in entries
            if query_lower in e.get("content", "").lower()
            or query_lower in e.get("header", "").lower()
        ]

        return matches[-limit:][::-1]  # Newest first
