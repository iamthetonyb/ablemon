"""
skills.sh integration for ABLE.

Provides discovery and installation of skills from the open skills registry.
Registry: https://skills.sh
CLI: npx skills add <owner/repo>
"""

from .client import SkillsShClient, SkillEntry, search_skills, install_skill

__all__ = ["SkillsShClient", "SkillEntry", "search_skills", "install_skill"]
