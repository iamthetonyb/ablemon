"""
GitHub Integration Skill — implement.py

Routes GitHub intents to GitHubClient.
All write operations go through approval_workflow before executing.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def run(
    intent: str,
    params: Dict[str, Any],
    github_client,
    approval_workflow,
    requester_id: str = "owner",
) -> str:
    """
    Execute a GitHub skill action.

    Args:
        intent: One of: list_repos, create_repo, create_branch, push_file, push_files, create_pr
        params: Action-specific parameters
        github_client: GitHubClient instance
        approval_workflow: ApprovalWorkflow instance (for write operations)
        requester_id: Telegram user ID string for approval tracking

    Returns:
        Human-readable result string
    """

    # ── Read-only (no approval needed) ────────────────────────────────────────

    if intent == "list_repos":
        repos = await github_client.list_repos()
        if not repos:
            return "No repositories found."
        lines = [f"• [{r['name']}]({r['html_url']}) — {'🔒 private' if r['private'] else '🌐 public'}"
                 for r in repos[:20]]
        return "**Your repositories:**\n" + "\n".join(lines)

    # ── Write operations (require approval) ────────────────────────────────────

    if intent == "create_repo":
        name = params.get("name", "")
        description = params.get("description", "")
        private = params.get("private", False)

        approval = await approval_workflow.request_approval(
            operation="github_create_repo",
            details={"repo": name, "private": private, "description": description},
            requester_id=requester_id,
            risk_level="medium",
            context=f"Create {'private' if private else 'public'} repo: github.com/{github_client.owner}/{name}",
        )
        if approval.status.value != "approved":
            return f"❌ Repo creation denied ({approval.status.value})"

        result = await github_client.create_repo(name, description, private)
        return f"✅ Repo created: {result['html_url']}"

    if intent == "create_branch":
        repo = params["repo"]
        branch = params["branch"]
        from_branch = params.get("from_branch", "main")

        approval = await approval_workflow.request_approval(
            operation="github_create_branch",
            details={"repo": repo, "branch": branch, "from": from_branch},
            requester_id=requester_id,
            risk_level="low",
            context=f"Create branch {branch} in {repo} from {from_branch}",
        )
        if approval.status.value != "approved":
            return f"❌ Branch creation denied ({approval.status.value})"

        await github_client.create_branch(repo, branch, from_branch)
        return f"✅ Branch `{branch}` created in `{repo}`"

    if intent == "push_file":
        repo = params["repo"]
        path = params["path"]
        content = params["content"]
        message = params.get("message", "chore: update file via ABLE")
        branch = params.get("branch", "main")

        approval = await approval_workflow.request_approval(
            operation="github_push_file",
            details={"repo": repo, "path": path, "branch": branch, "message": message},
            requester_id=requester_id,
            risk_level="medium",
            context=f"Push {path} to {repo}/{branch}",
        )
        if approval.status.value != "approved":
            return f"❌ File push denied ({approval.status.value})"

        await github_client.push_file(repo, path, content, message, branch)
        return f"✅ Pushed `{path}` to `{repo}/{branch}`"

    if intent == "push_files":
        repo = params["repo"]
        files_dict = params["files"]
        message = params.get("message", "chore: update files via ABLE")
        branch = params.get("branch", "main")
        file_list = ", ".join(list(files_dict.keys())[:5])

        approval = await approval_workflow.request_approval(
            operation="github_push_files",
            details={"repo": repo, "files": list(files_dict.keys()), "branch": branch},
            requester_id=requester_id,
            risk_level="medium",
            context=f"Push {len(files_dict)} files to {repo}/{branch}: {file_list}",
        )
        if approval.status.value != "approved":
            return f"❌ File push denied ({approval.status.value})"

        await github_client.push_files(repo, files_dict, message, branch)
        return f"✅ Pushed {len(files_dict)} files to `{repo}/{branch}`"

    if intent == "create_pr":
        repo = params["repo"]
        title = params["title"]
        body = params.get("body", "")
        head = params["head"]
        base = params.get("base", "main")

        approval = await approval_workflow.request_approval(
            operation="github_create_pr",
            details={"repo": repo, "title": title, "head": head, "base": base},
            requester_id=requester_id,
            risk_level="low",
            context=f"Open PR '{title}' in {repo}: {head} → {base}",
        )
        if approval.status.value != "approved":
            return f"❌ PR creation denied ({approval.status.value})"

        result = await github_client.create_pr(repo, title, body, head, base)
        return f"✅ PR opened: {result['html_url']}"

    return f"❓ Unknown GitHub intent: {intent}"
