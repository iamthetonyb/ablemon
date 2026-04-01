"""
ATLAS CLI Tools — Claude Code-equivalent tool set.

get_all_tools() returns all available tools sorted deterministically
by name for prompt-cache stability.
"""

from pathlib import Path
from typing import List, Optional

from .base import CLITool, ToolContext


def get_all_tools(work_dir: Optional[Path] = None) -> List[CLITool]:
    """Return all CLI tools sorted deterministically (for prompt cache stability)."""
    from .file_tools import ReadFile, WriteFile, EditFile
    from .bash_tool import BashExecute
    from .search_tools import GlobSearch, GrepSearch

    tools: List[CLITool] = [
        ReadFile(),
        WriteFile(),
        EditFile(),
        GlobSearch(),
        GrepSearch(),
        BashExecute(),
    ]

    # Optional tools — import errors are swallowed
    try:
        from .agent_tool import SpawnAgent
        tools.append(SpawnAgent())
    except ImportError:
        pass

    try:
        from .web_tools import WebSearchTool, WebFetchTool
        tools.extend([WebSearchTool(), WebFetchTool()])
    except ImportError:
        pass

    return sorted(tools, key=lambda t: t.name)
