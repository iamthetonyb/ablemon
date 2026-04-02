"""Nomad-style resource inventory and lifecycle adapters for ABLE."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResourceRecord:
    """A single control-plane resource exposed to ABLE Studio."""

    id: str
    kind: str
    name: str
    status: str
    summary: str
    owner: str = "able"
    control_mode: str = "inspect"
    endpoint: Optional[str] = None
    ports: List[int] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    storage_paths: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    approval_history: List[Dict[str, Any]] = field(default_factory=list)
    last_action: Optional[Dict[str, Any]] = None
    approval_required: bool = True
    last_checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CollectionProfile:
    """Curated install/operations bundle surfaced in the control plane."""

    id: str
    name: str
    summary: str
    resources: List[str]
    modules: List[str]
    notes: List[str]
    maturity: str = "beta"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ResourcePlane:
    """Backend-managed resource registry and lifecycle adapter."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or Path(__file__).resolve().parents[3]
        self.audit_path = self.project_root / "data" / "resource_actions.jsonl"
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    def list_resources(self) -> List[Dict[str, Any]]:
        resources: List[ResourceRecord] = []
        resources.extend(self._core_service_resources())
        resources.extend(self._docker_resources())
        resources.extend(self._ollama_resources())
        resources.extend(self._knowledge_resources())
        history = self._recent_actions()
        for resource in resources:
            resource_history = history.get(resource.id, [])
            resource.approval_history = resource_history[:10]
            resource.last_action = resource_history[0] if resource_history else None
        return [resource.to_dict() for resource in resources]

    def get_resource(self, resource_id: str) -> Optional[Dict[str, Any]]:
        for resource in self.list_resources():
            if resource["id"] == resource_id:
                detail = dict(resource)
                detail["log_artifact"] = self.get_resource_logs(resource_id)
                return detail
        return None

    def list_collections(self) -> List[Dict[str, Any]]:
        collections = [
            CollectionProfile(
                id="distillation-box",
                name="Distillation Box",
                summary="Gateway, health plane, Ollama, corpus storage, and T4/H100 training workflow.",
                resources=[
                    "service:able",
                    "http:gateway-health",
                    "runtime:ollama",
                    "storage:distillation-corpus",
                ],
                modules=["gateway", "distillation", "ollama", "control-plane"],
                notes=[
                    "Use for T4 data prep and H100 promotion cycles.",
                    "Pins the current 27B and 9B quant targets from config/distillation.",
                ],
            ),
            CollectionProfile(
                id="offline-knowledge",
                name="Offline Knowledge",
                summary="Attachable offline/local knowledge stack inspired by Nomad.",
                resources=["content:offline-knowledge", "notes:operator-notes"],
                modules=["kiwix", "flatnotes", "knowledge-import"],
                notes=[
                    "Optional bundle only. Not required for ABLE core runtime.",
                    "Route lifecycle actions through approval-gated adapters.",
                ],
            ),
            CollectionProfile(
                id="pentest-lab",
                name="Pentest Lab",
                summary="Security audit and red-team bundle for ABLE's weekly pentest and audit loops.",
                resources=["service:able", "http:gateway-health"],
                modules=["security-audit", "weekly-pentest", "strix-inspired checks"],
                notes=[
                    "Use existing approval workflow for any mutating security action.",
                ],
            ),
            CollectionProfile(
                id="research-stack",
                name="Research Stack",
                summary="Web research, provider health, audit traces, and artifact rendering for operator review.",
                resources=["service:able", "http:gateway-health", "storage:audit-log"],
                modules=["web-research", "audit", "artifact-viewer"],
                notes=[
                    "Bright Data-style ingestion belongs here as an optional approved lane.",
                ],
            ),
        ]
        return [collection.to_dict() for collection in collections]

    def get_setup_wizard(self) -> Dict[str, Any]:
        return {
            "title": "ABLE Setup Wizard",
            "steps": [
                {
                    "id": "gateway",
                    "label": "Gateway",
                    "status": self._service_status("able"),
                    "description": "Confirm the packaged ABLE service entrypoint and health endpoint are reachable.",
                },
                {
                    "id": "webhook",
                    "label": "Webhook + Control API",
                    "status": self._http_status("http://127.0.0.1:8080/health"),
                    "description": "Expose metrics, tool catalog, and resource inventory to ABLE Studio.",
                },
                {
                    "id": "ollama",
                    "label": "Ollama Runtime",
                    "status": self._http_status(f"{self.ollama_base_url.rstrip('/')}/api/tags"),
                    "description": "Inventory the pinned Qwen 27B / 9B artifacts and runtime capacity.",
                },
                {
                    "id": "vector-store",
                    "label": "Vector Store",
                    "status": self._path_status(Path.home() / ".able" / "memory"),
                    "description": "Validate local memory/vector storage before enabling heavier RAG workflows.",
                },
            ],
            "collections": self.list_collections(),
        }

    def get_resource_logs(self, resource_id: str, tail: int = 120) -> Dict[str, Any]:
        if resource_id.startswith("service:"):
            service_name = resource_id.split(":", 1)[1]
            output = self._run_capture(["journalctl", "-u", service_name, "-n", str(tail), "--no-pager"])
            return {"kind": "text", "title": f"{service_name} logs", "content": output}
        if resource_id.startswith("container:"):
            container_name = resource_id.split(":", 1)[1]
            output = self._run_capture(["docker", "logs", container_name, "--tail", str(tail)])
            return {"kind": "text", "title": f"{container_name} logs", "content": output}
        return {
            "kind": "json",
            "title": "No direct logs",
            "content": json.dumps({"resource_id": resource_id, "message": "No direct log adapter configured."}, indent=2),
        }

    def perform_action(
        self,
        resource_id: str,
        action: str,
        *,
        parameters: Optional[Dict[str, Any]] = None,
        approved_by: Optional[str] = None,
        service_token_verified: bool = False,
    ) -> Dict[str, Any]:
        if not approved_by:
            return {
                "status": "approval_required",
                "resource_id": resource_id,
                "action": action,
                "message": "Lifecycle actions require explicit approval metadata.",
            }

        # Guard: only accept approval from service-token-authenticated callers.
        # The gateway handler sets service_token_verified=True after checking
        # the x-able-service-token header.  Without this, any HTTP client
        # could claim approval by sending a fake approved_by string.
        if not service_token_verified:
            logger.warning(
                "Resource action %s on %s rejected: caller did not pass service-token gate",
                action,
                resource_id,
            )
            return {
                "status": "unauthorized",
                "resource_id": resource_id,
                "action": action,
                "message": "Resource actions require service-token-authenticated callers.",
            }

        commands = self._action_command(resource_id, action)
        if commands is None:
            return {
                "status": "unsupported",
                "resource_id": resource_id,
                "action": action,
            }

        result = subprocess.run(
            commands,
            capture_output=True,
            text=True,
            timeout=30,
        )
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resource_id": resource_id,
            "action": action,
            "parameters": parameters or {},
            "approved_by": approved_by,
            "command": commands,
            "exit_code": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:4000],
        }
        with open(self.audit_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

        return {
            "status": "completed" if result.returncode == 0 else "failed",
            "resource_id": resource_id,
            "action": action,
            "parameters": parameters or {},
            "exit_code": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:4000],
        }

    def _core_service_resources(self) -> List[ResourceRecord]:
        return [
            ResourceRecord(
                id="service:able",
                kind="service",
                name="ABLE Gateway Service",
                status=self._service_status("able"),
                summary="Primary Telegram/control-plane runtime managed by systemd.",
                control_mode="systemd",
                endpoint="http://127.0.0.1:8080/health",
                ports=[8080],
                dependencies=["runtime:ollama"],
                storage_paths=[str(Path.home() / ".able")],
                allowed_actions=["start", "stop", "restart", "status"],
                metadata={"service_name": "able"},
                artifacts=[
                    {
                        "kind": "json",
                        "title": "Health endpoint",
                        "content": json.dumps(
                            {"endpoint": "http://127.0.0.1:8080/health"},
                            indent=2,
                        ),
                    }
                ],
            ),
            ResourceRecord(
                id="http:gateway-health",
                kind="workflow",
                name="Gateway Health + Control API",
                status=self._http_status("http://127.0.0.1:8080/health"),
                summary="Read-only health, tool catalog, and resource inventory surface for ABLE Studio.",
                control_mode="http",
                endpoint="http://127.0.0.1:8080",
                ports=[8080],
                allowed_actions=["refresh"],
                approval_required=False,
            ),
            ResourceRecord(
                id="storage:distillation-corpus",
                kind="workflow",
                name="Distillation Corpus Store",
                status=self._path_status(Path.home() / ".able" / "distillation"),
                summary="Versioned corpus, outputs, and GPU-budget state for training runs.",
                storage_paths=[
                    str(Path.home() / ".able" / "distillation"),
                    str(self.project_root / "config" / "distillation"),
                ],
                allowed_actions=["refresh"],
                approval_required=False,
            ),
            ResourceRecord(
                id="storage:audit-log",
                kind="workflow",
                name="Audit + Metrics Store",
                status=self._path_status(self.project_root / "data"),
                summary="Interaction log, traces, and control-plane audit artifacts.",
                storage_paths=[str(self.project_root / "data")],
                allowed_actions=["refresh"],
                approval_required=False,
            ),
        ]

    def _docker_resources(self) -> List[ResourceRecord]:
        if not shutil.which("docker"):
            return []
        result = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        resources: List[ResourceRecord] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = entry.get("Names") or entry.get("ID", "container")
            resources.append(
                ResourceRecord(
                    id=f"container:{name}",
                    kind="container",
                    name=name,
                    status="running",
                    summary=entry.get("Image", "Docker container"),
                    control_mode="docker",
                    allowed_actions=["stop", "restart"],
                    metadata=entry,
                )
            )
        return resources

    def _ollama_resources(self) -> List[ResourceRecord]:
        resources = [
            ResourceRecord(
                id="runtime:ollama",
                kind="model",
                name="Ollama Runtime",
                status=self._http_status(f"{self.ollama_base_url.rstrip('/')}/api/tags"),
                summary="Local model runtime for Tier 5 and quantized student deployment.",
                control_mode="http",
                endpoint=self.ollama_base_url,
                allowed_actions=["refresh"],
                approval_required=False,
            )
        ]

        tags = self._ollama_tags()
        for model in tags:
            model_name = model.get("name", "unknown")
            resources.append(
                ResourceRecord(
                    id=f"model:{model_name}",
                    kind="model",
                    name=model_name,
                    status="available",
                    summary=f"Ollama model ({model.get('size', 'unknown size')})",
                    control_mode="inspect",
                    endpoint=self.ollama_base_url,
                    allowed_actions=["refresh"],
                    approval_required=False,
                    metadata=model,
                )
            )
        return resources

    def _knowledge_resources(self) -> List[ResourceRecord]:
        return [
            ResourceRecord(
                id="content:offline-knowledge",
                kind="content_library",
                name="Offline Knowledge Bundle",
                status="planned",
                summary="Attachable Kiwix/knowledge-library inspired bundle for local/offline operation.",
                control_mode="planned",
                allowed_actions=["install"],
                metadata={"inspiration": "project-nomad"},
            ),
            ResourceRecord(
                id="notes:operator-notes",
                kind="notes_app",
                name="Operator Notes",
                status="available",
                summary="Studio note/memory surface for operator tracking and local knowledge.",
                control_mode="studio",
                allowed_actions=["refresh"],
                approval_required=False,
            ),
        ]

    def _action_command(self, resource_id: str, action: str) -> Optional[List[str]]:
        if resource_id.startswith("service:") and action in {"start", "stop", "restart", "status"}:
            service_name = resource_id.split(":", 1)[1]
            return ["systemctl", action, service_name]
        if resource_id.startswith("container:") and action in {"stop", "restart"}:
            container_name = resource_id.split(":", 1)[1]
            return ["docker", action, container_name]
        if action == "refresh":
            return ["/usr/bin/env", "true"]
        return None

    def _service_status(self, service_name: str) -> str:
        if not shutil.which("systemctl"):
            return "unknown"
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = result.stdout.strip() or result.stderr.strip()
        return status or "unknown"

    def _http_status(self, url: str) -> str:
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=5) as response:
                if 200 <= response.status < 400:
                    return "healthy"
                return f"http_{response.status}"
        except Exception:
            return "offline"

    def _ollama_tags(self) -> List[Dict[str, Any]]:
        url = f"{self.ollama_base_url.rstrip('/')}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            return list(data.get("models", []))
        except Exception:
            return []

    @staticmethod
    def _path_status(path: Path) -> str:
        return "available" if path.exists() else "missing"

    @staticmethod
    def _run_capture(command: List[str]) -> str:
        if not shutil.which(command[0]):
            return f"{command[0]} unavailable on this host."
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = (result.stdout or result.stderr or "").strip()
            return output[:12000] or "No output."
        except Exception as exc:  # pragma: no cover - defensive
            return f"Failed to collect output: {exc}"

    def _recent_actions(self) -> Dict[str, List[Dict[str, Any]]]:
        if not self.audit_path.exists():
            return {}

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}

        for line in reversed(lines[-200:]):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            resource_id = record.get("resource_id")
            if not resource_id:
                continue
            grouped.setdefault(resource_id, []).append(record)
        return grouped
