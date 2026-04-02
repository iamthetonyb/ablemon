"""Prompt bank for active corpus generation.

Manages prompts organized by domain and difficulty. Loads from JSONL files
in prompt_bank_data/ directory. Used by the corpus-generator skill to
drive distillation data collection sessions.

Auto-refreshes from:
- ABLE's real interaction patterns (anonymized)
- AutoImprover's failure patterns (target student weaknesses)
- Promptfoo eval cases that found gaps
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DOMAINS = ["coding", "security", "reasoning", "creative", "agentic", "tools"]
DIFFICULTIES = ["easy", "medium", "hard"]
_DOMAIN_ALIASES = {
    "code": "coding",
    "tool": "tools",
}


@dataclass
class PromptEntry:
    """Single prompt for corpus generation."""

    prompt: str
    domain: str
    difficulty: str
    expected_skills: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "prompt": self.prompt,
            "domain": self.domain,
            "difficulty": self.difficulty,
        }
        if self.expected_skills:
            d["expected_skills"] = self.expected_skills
        if self.tags:
            d["tags"] = self.tags
        return d

    @classmethod
    def from_dict(cls, data: dict) -> PromptEntry:
        return cls(
            prompt=data["prompt"],
            domain=data["domain"],
            difficulty=data["difficulty"],
            expected_skills=data.get("expected_skills", []),
            tags=data.get("tags", []),
        )


class PromptBank:
    """Manages prompts for active corpus generation sessions.

    Organized by domain and difficulty. Loads from JSONL files in
    prompt_bank_data/ directory.
    """

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(
            data_dir or (Path(__file__).parent / "prompt_bank_data")
        )
        self._prompts: dict[str, dict[str, list[PromptEntry]]] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load all prompt JSONL files from data directory."""
        if not self.data_dir.exists():
            return

        for domain_dir in sorted(self.data_dir.iterdir()):
            if not domain_dir.is_dir():
                continue
            jsonl_files = sorted(domain_dir.glob("*.jsonl"))
            if not jsonl_files:
                continue

            domain = self._canonicalize_domain(domain_dir.name)
            self._prompts.setdefault(domain, {})

            for jsonl_file in jsonl_files:
                difficulty = jsonl_file.stem
                entries = self._load_jsonl(jsonl_file)
                if not entries:
                    continue
                bucket = self._prompts[domain].setdefault(difficulty, [])
                seen = {self._entry_key(entry) for entry in bucket}
                for entry in entries:
                    normalized = PromptEntry(
                        prompt=entry.prompt.strip(),
                        domain=self._canonicalize_domain(entry.domain or domain),
                        difficulty=entry.difficulty,
                        expected_skills=entry.expected_skills,
                        tags=entry.tags,
                    )
                    key = self._entry_key(normalized)
                    if key in seen:
                        continue
                    bucket.append(normalized)
                    seen.add(key)

    @staticmethod
    def _load_jsonl(path: Path) -> list[PromptEntry]:
        """Load a single JSONL file into PromptEntry list."""
        entries: list[PromptEntry] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entries.append(PromptEntry.from_dict(data))
        return entries

    def sample(
        self,
        domain: Optional[str] = None,
        difficulty: Optional[str] = None,
        n: int = 10,
    ) -> list[PromptEntry]:
        """Random sample of prompts.

        If domain/difficulty not specified, sample across all.
        Returns up to *n* prompts (fewer if pool is smaller).
        """
        pool = self._collect(domain, difficulty)
        return random.sample(pool, min(n, len(pool)))

    def all_domains(self) -> list[str]:
        """Return sorted list of available domains."""
        return sorted(self._prompts.keys())

    def count(
        self,
        domain: Optional[str] = None,
        difficulty: Optional[str] = None,
    ) -> int:
        """Count available prompts, optionally filtered."""
        return len(self._collect(domain, difficulty))

    def add_prompt(self, prompt: PromptEntry) -> None:
        """Add a new prompt to the bank and persist to JSONL."""
        normalized = PromptEntry(
            prompt=prompt.prompt.strip(),
            domain=self._canonicalize_domain(prompt.domain),
            difficulty=prompt.difficulty,
            expected_skills=prompt.expected_skills,
            tags=prompt.tags,
        )
        self._prompts.setdefault(normalized.domain, {})
        self._prompts[normalized.domain].setdefault(normalized.difficulty, [])
        bucket = self._prompts[normalized.domain][normalized.difficulty]
        key = self._entry_key(normalized)
        if any(self._entry_key(existing) == key for existing in bucket):
            return
        bucket.append(normalized)

        # Persist to disk
        domain_dir = self.data_dir / normalized.domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = domain_dir / f"{normalized.difficulty}.jsonl"
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(normalized.to_dict(), ensure_ascii=False) + "\n")

    def add_from_failures(self, failure_patterns: list[dict]) -> int:
        """Generate prompts from failure patterns. Returns count added.

        Each failure_pattern dict should have:
          - category: str (e.g. "skill_gap", "under_routing")
          - domain: str
          - description: str (used as the prompt basis)
          - difficulty: str (optional, defaults to "medium")
          - tags: list[str] (optional)
        """
        added = 0
        for pattern in failure_patterns:
            domain = pattern.get("domain", "coding")
            difficulty = pattern.get("difficulty", "medium")
            description = pattern.get("description", "")
            if not description:
                continue

            entry = PromptEntry(
                prompt=description,
                domain=domain,
                difficulty=difficulty,
                tags=pattern.get("tags", ["from_failure"]),
            )
            self.add_prompt(entry)
            added += 1
        return added

    # -- internal helpers --

    def _collect(
        self,
        domain: Optional[str] = None,
        difficulty: Optional[str] = None,
    ) -> list[PromptEntry]:
        """Collect prompts matching optional filters."""
        results: list[PromptEntry] = []
        domains = [self._canonicalize_domain(domain)] if domain else list(self._prompts.keys())
        for d in domains:
            if d not in self._prompts:
                continue
            difficulties = (
                [difficulty] if difficulty else list(self._prompts[d].keys())
            )
            for diff in difficulties:
                results.extend(self._prompts[d].get(diff, []))
        return results

    @staticmethod
    def _canonicalize_domain(domain: str) -> str:
        """Normalize domain names so duplicate folders do not fragment coverage."""
        normalized = re.sub(r"\s+\d+$", "", (domain or "").strip().lower())
        return _DOMAIN_ALIASES.get(normalized, normalized)

    @staticmethod
    def _entry_key(entry: PromptEntry) -> tuple[str, str, str]:
        return (
            entry.prompt.strip(),
            entry.domain.strip().lower(),
            entry.difficulty.strip().lower(),
        )
