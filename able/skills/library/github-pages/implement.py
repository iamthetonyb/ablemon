"""
GitHub Pages Skill — implement.py

Pushes static files to the gh-pages branch and enables GitHub Pages.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def run(
    params: Dict[str, Any],
    github_client,
    approval_workflow,
    requester_id: str = "owner",
) -> str:
    """
    Deploy static files to GitHub Pages.

    Args:
        params: {
            "repo": str,                   # Repo name (must exist or will be created)
            "files": {path: content},      # Static files to push
            "commit_message": str,         # Optional commit message
            "create_repo_if_missing": bool # Default True
        }
        github_client: GitHubClient instance
        approval_workflow: ApprovalWorkflow instance
        requester_id: Telegram user ID string

    Returns:
        Human-readable result with live URL
    """
    repo = params["repo"]
    files_dict = params["files"]
    message = params.get("commit_message", "deploy: update GitHub Pages via ABLE")
    create_if_missing = params.get("create_repo_if_missing", True)

    owner = github_client.owner
    pages_url = f"https://{owner}.github.io/{repo}/"

    # Request approval
    file_list = ", ".join(list(files_dict.keys())[:5])
    approval = await approval_workflow.request_approval(
        operation="github_pages_deploy",
        details={
            "repo": repo,
            "files": list(files_dict.keys()),
            "live_url": pages_url,
        },
        requester_id=requester_id,
        risk_level="low",
        context=f"Deploy {len(files_dict)} files to {pages_url}\nFiles: {file_list}",
    )
    if approval.status.value != "approved":
        return f"❌ Pages deploy denied ({approval.status.value})"

    # Create repo if needed
    if create_if_missing:
        try:
            repos = await github_client.list_repos()
            repo_names = [r["name"] for r in repos]
            if repo not in repo_names:
                await github_client.create_repo(repo, description=f"GitHub Pages site", private=False)
                logger.info(f"Created repo {repo} for Pages deployment")
        except Exception as e:
            logger.warning(f"Could not check/create repo: {e}")

    # Push files to gh-pages branch
    try:
        await github_client.push_files(repo, files_dict, message, branch="gh-pages")
    except Exception:
        # gh-pages branch may not exist yet — create it from main first
        try:
            await github_client.create_branch(repo, "gh-pages", from_branch="main")
            await github_client.push_files(repo, files_dict, message, branch="gh-pages")
        except Exception as e:
            # If main doesn't exist either, push to gh-pages directly (new repo)
            # Push index.html first to initialize
            for path, content in files_dict.items():
                await github_client.push_file(repo, path, content, message, branch="gh-pages")

    # Enable GitHub Pages
    try:
        await github_client.enable_github_pages(repo, branch="gh-pages")
        logger.info(f"GitHub Pages enabled for {repo}")
    except Exception as e:
        # Pages may already be enabled
        logger.info(f"Pages enable response: {e} (may already be enabled)")

    return (
        f"✅ Deployed to GitHub Pages!\n\n"
        f"🌐 URL: {pages_url}\n"
        f"📁 Files: {len(files_dict)} pushed to `gh-pages` branch\n"
        f"⏱ Live in ~60 seconds (Pages build time)"
    )
