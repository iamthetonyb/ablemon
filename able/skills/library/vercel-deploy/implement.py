"""
Vercel Deploy Skill — implement.py

Deploys files to Vercel and returns the live preview URL.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def run(
    params: Dict[str, Any],
    vercel_client,
    approval_workflow,
    requester_id: str = "owner",
) -> str:
    """
    Deploy to Vercel.

    Args:
        params: {
            "project_name": str,           # Vercel project name (kebab-case)
            "files": {filename: content},  # Files to deploy
            "env_vars": {key: value},      # Optional environment variables
        }
        vercel_client: VercelClient instance
        approval_workflow: ApprovalWorkflow instance
        requester_id: Telegram user ID string

    Returns:
        Human-readable result with live URL
    """
    project_name = params["project_name"]
    files_dict = params["files"]
    env_vars: Optional[Dict[str, str]] = params.get("env_vars")

    file_list = ", ".join(list(files_dict.keys())[:5])

    # Request approval
    approval = await approval_workflow.request_approval(
        operation="vercel_deploy",
        details={
            "project": project_name,
            "files": list(files_dict.keys()),
            "env_vars": list(env_vars.keys()) if env_vars else [],
        },
        requester_id=requester_id,
        risk_level="low",
        context=f"Deploy {len(files_dict)} files to Vercel project '{project_name}'\nFiles: {file_list}",
    )
    if approval.status.value != "approved":
        return f"❌ Vercel deploy denied ({approval.status.value})"

    # Deploy
    result = await vercel_client.create_deployment(project_name, files_dict, env_vars)

    state = result.get("readyState", "UNKNOWN")
    url = result.get("url", "")
    deployment_id = result.get("id", "")

    if state == "READY":
        live_url = f"https://{url}" if url and not url.startswith("http") else url
        return (
            f"✅ Deployed to Vercel!\n\n"
            f"🌐 URL: {live_url}\n"
            f"📦 Project: {project_name}\n"
            f"🆔 Deployment: {deployment_id}\n\n"
            f"Promote to production at vercel.com/dashboard"
        )
    elif state == "ERROR":
        error = result.get("errorMessage", "Unknown error")
        return f"❌ Vercel deployment failed: {error}"
    else:
        return (
            f"⏳ Deployment in progress (state: {state})\n"
            f"ID: {deployment_id}\n"
            f"Check vercel.com/dashboard for status."
        )
