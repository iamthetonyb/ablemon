"""
GitHub tool definitions and handlers.
Extracted from gateway.py for modularity.
"""

import os
import re
import logging
from typing import TYPE_CHECKING


def _github_available() -> bool:
    """Availability check: GITHUB_TOKEN must be set."""
    return bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))

if TYPE_CHECKING:
    from able.core.gateway.tool_registry import ToolRegistry, ToolContext

logger = logging.getLogger(__name__)


# ── Tool Definitions (OpenAI function-calling schema) ─────────────────────────

GITHUB_LIST_REPOS = {
    "type": "function",
    "function": {
        "name": "github_list_repos",
        "description": "List all GitHub repositories for the authenticated user. Read-only, no approval needed.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

GITHUB_CREATE_REPO = {
    "type": "function",
    "function": {
        "name": "github_create_repo",
        "description": "Create a new GitHub repository. Requires owner approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Repo name in kebab-case"},
                "description": {"type": "string", "description": "Short repo description"},
                "private": {"type": "boolean", "description": "True for private repo, false for public"},
            },
            "required": ["name"],
        },
    },
}

GITHUB_PUSH_FILES = {
    "type": "function",
    "function": {
        "name": "github_push_files",
        "description": "Push one or more files to a GitHub repository. Requires owner approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name"},
                "files": {
                    "type": "object",
                    "description": "Map of {filepath: file_content_string}. CRITICAL: If your code is massive (> 10,000 chars), DO NOT PUT THE CODE HERE to avoid JSON crashes. INSTEAD, output the raw code in a Markdown block in your normal conversational response FIRST, and pass the exact string '<EXTRACT>' as the value here. ABLE will auto-extract it.",
                    "additionalProperties": {"type": "string"},
                },
                "message": {"type": "string", "description": "Commit message (conventional commits format)"},
                "branch": {"type": "string", "description": "Target branch (default: main)"},
            },
            "required": ["repo", "files"],
        },
    },
}

GITHUB_CREATE_PR = {
    "type": "function",
    "function": {
        "name": "github_create_pr",
        "description": "Open a pull request on GitHub. Requires owner approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "PR description in markdown"},
                "head": {"type": "string", "description": "Source branch"},
                "base": {"type": "string", "description": "Target branch (default: main)"},
            },
            "required": ["repo", "title", "head"],
        },
    },
}

GITHUB_PAGES_DEPLOY = {
    "type": "function",
    "function": {
        "name": "github_pages_deploy",
        "description": "Deploy static HTML/CSS/JS files to GitHub Pages. Requires owner approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name"},
                "files": {
                    "type": "object",
                    "description": "Map of {filepath: file_content_string}. Must include index.html.",
                    "additionalProperties": {"type": "string"},
                },
                "commit_message": {"type": "string"},
            },
            "required": ["repo", "files"],
        },
    },
}


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_list_repos(args: dict, ctx: "ToolContext") -> str:
    repos = await ctx.metadata["github"].list_repos()
    if not repos:
        return "No repositories found."
    lines = [
        f"• [{r['name']}]({r['html_url']}) — {'🔒 private' if r['private'] else '🌐 public'}"
        for r in repos[:20]
    ]
    return "**Your repositories:**\n" + "\n".join(lines)


async def handle_create_repo(args: dict, ctx: "ToolContext") -> str:
    github = ctx.metadata["github"]
    result = await github.create_repo(
        name=args["name"],
        description=args.get("description", ""),
        private=args.get("private", False),
    )
    return f"✅ Repo created: {result['html_url']}"


async def handle_push_files(args: dict, ctx: "ToolContext") -> str:
    github = ctx.metadata["github"]
    # Handle <EXTRACT> bypass for massive code blocks
    if ctx.msgs:
        from able.core.providers.base import Role
        for path, content in args.get("files", {}).items():
            if content == "<EXTRACT>":
                for m in reversed(ctx.msgs):
                    if m.role in (Role.ASSISTANT, Role.USER) and m.content:
                        blocks = re.findall(r'```(?:\w+)?\n(.*?)```', m.content, re.DOTALL)
                        if blocks:
                            args["files"][path] = blocks[-1]
                            break

    await github.push_files(
        repo=args["repo"],
        files_dict=args["files"],
        message=args.get("message", "chore: update via ABLE"),
        branch=args.get("branch", "main"),
    )
    return f"✅ Pushed {len(args.get('files', {}))} files to `{args['repo']}`"


async def handle_create_pr(args: dict, ctx: "ToolContext") -> str:
    github = ctx.metadata["github"]
    result = await github.create_pr(
        repo=args["repo"],
        title=args["title"],
        body=args.get("body", ""),
        head=args["head"],
        base=args.get("base", "main"),
    )
    return f"✅ PR opened: {result['html_url']}"


async def handle_pages_deploy(args: dict, ctx: "ToolContext") -> str:
    github = ctx.metadata["github"]
    repo = args["repo"]
    files_dict = args["files"]
    message = args.get("commit_message", "deploy: update GitHub Pages via ABLE")
    pages_url = f"https://{github.owner}.github.io/{repo}/"

    try:
        await github.push_files(repo, files_dict, message, branch="gh-pages")
    except Exception:
        try:
            await github.create_branch(repo, "gh-pages", from_branch="main")
            await github.push_files(repo, files_dict, message, branch="gh-pages")
        except Exception:
            for path, content in files_dict.items():
                await github.push_file(repo, path, content, message, branch="gh-pages")

    try:
        await github.enable_github_pages(repo, branch="gh-pages")
    except Exception:
        pass

    return (
        f"✅ Deployed to GitHub Pages!\n\n"
        f"🌐 URL: {pages_url}\n"
        f"📁 Files: {len(files_dict)} pushed to `gh-pages`\n"
        f"⏱ Live in ~60 seconds"
    )


# ── Registration ──────────────────────────────────────────────────────────────

def register_tools(registry: "ToolRegistry"):
    """Register all GitHub tools with the registry."""
    registry.register(
        name="github_list_repos",
        definition=GITHUB_LIST_REPOS,
        handler=handle_list_repos,
        display_name="GitHub / List Repositories",
        requires_approval=False,
        category="search-fetch",
        read_only=True,
        concurrent_safe=True,
        surface="github",
        artifact_kind="markdown",
        tags=["github", "read"],
        availability_check=_github_available,
    )
    registry.register(
        name="github_create_repo",
        definition=GITHUB_CREATE_REPO,
        handler=handle_create_repo,
        display_name="GitHub / Create Repository",
        requires_approval=True,
        risk_level="medium",
        category="execution",
        read_only=False,
        concurrent_safe=False,
        surface="github",
        artifact_kind="markdown",
        tags=["github", "write"],
        availability_check=_github_available,
    )
    registry.register(
        name="github_push_files",
        definition=GITHUB_PUSH_FILES,
        handler=handle_push_files,
        display_name="GitHub / Push Files",
        requires_approval=True,
        risk_level="medium",
        category="execution",
        read_only=False,
        concurrent_safe=False,
        surface="github",
        artifact_kind="markdown",
        tags=["github", "write", "code"],
        availability_check=_github_available,
    )
    registry.register(
        name="github_create_pr",
        definition=GITHUB_CREATE_PR,
        handler=handle_create_pr,
        display_name="GitHub / Create Pull Request",
        requires_approval=True,
        risk_level="low",
        category="execution",
        read_only=False,
        concurrent_safe=False,
        surface="github",
        artifact_kind="markdown",
        tags=["github", "review"],
        availability_check=_github_available,
    )
    registry.register(
        name="github_pages_deploy",
        definition=GITHUB_PAGES_DEPLOY,
        handler=handle_pages_deploy,
        display_name="GitHub Pages / Deploy",
        requires_approval=True,
        risk_level="low",
        category="execution",
        read_only=False,
        concurrent_safe=False,
        surface="deploy",
        artifact_kind="markdown",
        tags=["github", "deploy"],
        availability_check=_github_available,
    )
