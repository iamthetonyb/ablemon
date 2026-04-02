"""
skills.sh Client — Discovery and installation of skills from the open registry.

Registry: https://skills.sh
Install CLI: npx skills add <owner/repo>

Usage:
    from able.tools.skills_sh import search_skills, install_skill

    # Search for relevant skills
    results = await search_skills("pdf processing")

    # Install a skill
    await install_skill("anthropics/skills")  # installs pdf, docx, xlsx, pptx

    # Weekly auto-check
    await SkillsShClient().weekly_discovery_check(context="current task list")
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Skills known to be high-value for ABLE workflows (pre-evaluated)
RECOMMENDED_SKILLS = [
    {
        "skill": "find-skills",
        "source": "vercel-labs/skills",
        "use_case": "Discover relevant skills.sh skills for any task",
        "install": "npx skills add vercel-labs/skills",
    },
    {
        "skill": "systematic-debugging",
        "source": "obra/superpowers",
        "use_case": "Structured debugging workflows for hard problems",
        "install": "npx skills add obra/superpowers",
    },
    {
        "skill": "brainstorming",
        "source": "obra/superpowers",
        "use_case": "Structured ideation and creative problem solving",
        "install": "npx skills add obra/superpowers",
    },
    {
        "skill": "pdf",
        "source": "anthropics/skills",
        "use_case": "PDF creation, editing, form filling, extraction",
        "install": "npx skills add anthropics/skills",
    },
    {
        "skill": "docx",
        "source": "anthropics/skills",
        "use_case": "Word document creation, editing, tracked changes",
        "install": "npx skills add anthropics/skills",
    },
    {
        "skill": "xlsx",
        "source": "anthropics/skills",
        "use_case": "Spreadsheet creation, editing, formulas",
        "install": "npx skills add anthropics/skills",
    },
    {
        "skill": "skill-creator",
        "source": "anthropics/skills",
        "use_case": "Best practices for creating new skills",
        "install": "npx skills add anthropics/skills",
    },
    {
        "skill": "copywriting",
        "source": "coreyhaines31/marketingskills",
        "use_case": "Direct-response copywriting and sales copy",
        "install": "npx skills add coreyhaines31/marketingskills",
    },
    {
        "skill": "seo-audit",
        "source": "coreyhaines31/marketingskills",
        "use_case": "SEO analysis and optimization recommendations",
        "install": "npx skills add coreyhaines31/marketingskills",
    },
    {
        "skill": "agent-browser",
        "source": "vercel-labs/agent-browser",
        "use_case": "Browser automation for web tasks",
        "install": "npx skills add vercel-labs/agent-browser",
    },
]


@dataclass
class SkillEntry:
    """A skill from the skills.sh registry"""
    name: str
    source: str          # "owner/repo"
    description: str = ""
    installs: int = 0
    use_case: str = ""
    install_cmd: str = ""
    tags: list = field(default_factory=list)

    @property
    def owner(self) -> str:
        return self.source.split("/")[0] if "/" in self.source else ""

    @property
    def repo(self) -> str:
        return self.source.split("/")[1] if "/" in self.source else self.source


class SkillsShClient:
    """
    Client for the skills.sh open agent skills registry.

    Handles:
    - Skill discovery (search by keyword/task)
    - Skill installation (npx skills add)
    - Weekly auto-discovery checks
    - Relevance scoring for upcoming tasks
    """

    SKILLS_LIBRARY_PATH = Path("able/skills/library")
    SKILLS_INDEX_PATH = Path("able/skills/SKILL_INDEX.yaml")

    def __init__(self, skills_library: str = None):
        self.skills_library = Path(skills_library) if skills_library else self.SKILLS_LIBRARY_PATH

    def search_local(self, query: str) -> list[SkillEntry]:
        """Search installed skills for matching entries"""
        query_lower = query.lower()
        matches = []

        for skill_data in RECOMMENDED_SKILLS:
            if (query_lower in skill_data["skill"].lower() or
                    query_lower in skill_data["use_case"].lower()):
                matches.append(SkillEntry(
                    name=skill_data["skill"],
                    source=skill_data["source"],
                    use_case=skill_data["use_case"],
                    install_cmd=skill_data["install"],
                ))

        return matches

    def get_recommended(self, task_description: str = "") -> list[SkillEntry]:
        """
        Get recommended skills for a task description.
        Returns top matches from the pre-evaluated recommended list.
        """
        if not task_description:
            return [
                SkillEntry(
                    name=s["skill"],
                    source=s["source"],
                    use_case=s["use_case"],
                    install_cmd=s["install"],
                )
                for s in RECOMMENDED_SKILLS
            ]

        return self.search_local(task_description)

    def is_installed(self, skill_name: str) -> bool:
        """Check if a skill is already installed locally"""
        skill_dir = self.skills_library / skill_name
        return skill_dir.exists() and (skill_dir / "SKILL.md").exists()

    def install(self, source: str, target_dir: str = None) -> bool:
        """
        Install a skill from skills.sh using npx.

        Args:
            source: "owner/repo" format
            target_dir: Override default install location

        Returns:
            True if successful
        """
        cmd = ["npx", "skills", "add", source]
        if target_dir:
            cmd.extend(["--dir", target_dir])

        logger.info(f"Installing skill from {source}: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode == 0:
                logger.info(f"Successfully installed skill from {source}")
                logger.debug(result.stdout)
                return True
            else:
                logger.error(f"Failed to install skill from {source}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout installing skill from {source}")
            return False
        except FileNotFoundError:
            logger.error("npx not found — install Node.js to use skills.sh")
            return False
        except Exception as e:
            logger.error(f"Error installing skill: {e}")
            return False

    def update_all(self) -> bool:
        """Run npx skills update to refresh the registry"""
        try:
            result = subprocess.run(
                ["npx", "skills", "update"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("skills.sh registry updated")
                return True
            else:
                logger.warning(f"skills update warning: {result.stderr}")
                return False
        except FileNotFoundError:
            logger.warning("npx not found — skipping skills.sh update")
            return False
        except Exception as e:
            logger.error(f"skills update error: {e}")
            return False

    def weekly_discovery_check(self, context: str = "") -> dict:
        """
        Weekly check for new relevant skills.

        Called by the proactive engine every Monday.
        Returns a report of new skills found.
        """
        report = {
            "checked": True,
            "new_suggestions": [],
            "already_installed": [],
        }

        # Update registry first
        self.update_all()

        # Check which recommended skills aren't installed
        for skill_data in RECOMMENDED_SKILLS:
            skill_name = skill_data["skill"]
            if self.is_installed(skill_name):
                report["already_installed"].append(skill_name)
            else:
                report["new_suggestions"].append({
                    "skill": skill_name,
                    "source": skill_data["source"],
                    "use_case": skill_data["use_case"],
                    "install": skill_data["install"],
                })

        if report["new_suggestions"]:
            logger.info(
                f"skills.sh: {len(report['new_suggestions'])} new skills available. "
                f"Top: {report['new_suggestions'][0]['skill']}"
            )

        return report

    def plan_with_discovery(self, task: str) -> Optional[SkillEntry]:
        """
        During task planning, check if a relevant skill exists on skills.sh.

        Returns the best matching skill if found (not yet installed).
        """
        matches = self.search_local(task)

        for match in matches:
            if not self.is_installed(match.name):
                logger.info(
                    f"skills.sh: Found relevant skill '{match.name}' "
                    f"from {match.source} for task: {task[:50]}"
                )
                return match

        return None


# ─────────────────────────────────────────────────────────────────────────────
# Convenience functions
# ─────────────────────────────────────────────────────────────────────────────

async def search_skills(query: str) -> list[SkillEntry]:
    """Search skills.sh for skills matching a query"""
    client = SkillsShClient()
    return client.get_recommended(query)


async def install_skill(source: str) -> bool:
    """Install a skill from skills.sh (owner/repo format)"""
    client = SkillsShClient()
    return client.install(source)
