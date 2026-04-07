"""
Distributed Compute Mesh — Node discovery and job scheduling for distillation.

Extends ABLE's federation infrastructure to distribute training/inference jobs
across available machines on the local network and remote nodes.

Features:
- mDNS LAN discovery + manual registration for remote nodes
- Capability reporting (GPU, VRAM, CPU, RAM, idle status)
- Idle-aware job scheduling (train during detected idle periods)
- Heartbeat health monitoring
- Gradient accumulation across nodes for distributed LoRA training

Note: RustPython is NOT production-ready (their own README says so).
Training jobs use standard Python with Unsloth/Axolotl pipeline.
"""

import asyncio
import json
import logging
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class NodeCapability:
    """Hardware capabilities of a compute node."""
    gpu_type: str = ""  # "nvidia_a100", "apple_m4", "none"
    vram_gb: float = 0.0
    cpu_cores: int = 0
    ram_gb: float = 0.0
    disk_free_gb: float = 0.0
    chip: str = ""  # "M1", "M2", "M4", "A100", etc.
    has_ane: bool = False  # Apple Neural Engine
    python_version: str = ""
    has_unsloth: bool = False
    has_axolotl: bool = False


@dataclass
class ComputeNode:
    """A node in the compute mesh."""
    node_id: str
    hostname: str
    ip_address: str
    port: int = 8765  # Mesh coordination port
    capability: NodeCapability = field(default_factory=NodeCapability)
    status: str = "unknown"  # "online", "idle", "busy", "offline"
    last_heartbeat: float = 0.0
    current_job: str = ""
    registered_at: float = 0.0
    is_local: bool = False

    @property
    def is_alive(self) -> bool:
        return time.time() - self.last_heartbeat < 90  # 90s timeout


@dataclass
class TrainingJob:
    """A distributed training job."""
    job_id: str
    job_type: str  # "lora_finetune", "dpo_train", "merge_adapters", "inference_batch"
    status: str = "pending"  # "pending", "assigned", "running", "completed", "failed"
    assigned_node: str = ""
    data_path: str = ""
    output_path: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""
    result: Dict[str, Any] = field(default_factory=dict)


class ComputeMesh:
    """
    Coordinate distributed training across available compute nodes.

    Usage:
        mesh = ComputeMesh()
        mesh.register_local_node()
        nodes = mesh.discover_lan_nodes()
        job = mesh.schedule_training(data_path, config)
    """

    def __init__(self, state_dir: Optional[Path] = None):
        if state_dir is None:
            state_dir = Path(__file__).parent.parent.parent / "data" / "compute_mesh"
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.nodes: Dict[str, ComputeNode] = {}
        self.jobs: Dict[str, TrainingJob] = {}
        self._load_state()

    def _load_state(self):
        """Load mesh state from disk."""
        state_file = self.state_dir / "mesh_state.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                for nd in data.get("nodes", []):
                    node = ComputeNode(
                        node_id=nd["node_id"],
                        hostname=nd["hostname"],
                        ip_address=nd["ip_address"],
                        port=nd.get("port", 8765),
                        status=nd.get("status", "offline"),
                        last_heartbeat=nd.get("last_heartbeat", 0),
                        registered_at=nd.get("registered_at", 0),
                        is_local=nd.get("is_local", False),
                    )
                    cap = nd.get("capability", {})
                    node.capability = NodeCapability(**{
                        k: v for k, v in cap.items()
                        if k in NodeCapability.__dataclass_fields__
                    })
                    self.nodes[node.node_id] = node
            except Exception as e:
                logger.warning("Failed to load mesh state: %s", e)

    def _save_state(self):
        """Persist mesh state."""
        state_file = self.state_dir / "mesh_state.json"
        data = {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "hostname": n.hostname,
                    "ip_address": n.ip_address,
                    "port": n.port,
                    "status": n.status,
                    "last_heartbeat": n.last_heartbeat,
                    "registered_at": n.registered_at,
                    "is_local": n.is_local,
                    "capability": {
                        "gpu_type": n.capability.gpu_type,
                        "vram_gb": n.capability.vram_gb,
                        "cpu_cores": n.capability.cpu_cores,
                        "ram_gb": n.capability.ram_gb,
                        "chip": n.capability.chip,
                        "has_ane": n.capability.has_ane,
                    },
                }
                for n in self.nodes.values()
            ],
            "updated_at": time.time(),
        }
        state_file.write_text(json.dumps(data, indent=2))

    def register_local_node(self) -> ComputeNode:
        """Detect and register the local machine's capabilities."""
        import socket
        hostname = socket.gethostname()
        node_id = f"local-{hostname}"

        cap = self._detect_local_capabilities()

        node = ComputeNode(
            node_id=node_id,
            hostname=hostname,
            ip_address="127.0.0.1",
            capability=cap,
            status="idle",
            last_heartbeat=time.time(),
            registered_at=time.time(),
            is_local=True,
        )
        self.nodes[node_id] = node
        self._save_state()
        logger.info(
            "Registered local node: %s (%s, %s, %.1fGB RAM)",
            node_id, cap.chip or cap.gpu_type or "CPU", cap.gpu_type, cap.ram_gb,
        )
        return node

    def register_remote_node(
        self,
        hostname: str,
        ip_address: str,
        port: int = 8765,
        capability: Optional[NodeCapability] = None,
    ) -> ComputeNode:
        """Manually register a remote compute node."""
        node_id = f"remote-{hostname}"
        node = ComputeNode(
            node_id=node_id,
            hostname=hostname,
            ip_address=ip_address,
            port=port,
            capability=capability or NodeCapability(),
            status="unknown",
            last_heartbeat=0,
            registered_at=time.time(),
        )
        self.nodes[node_id] = node
        self._save_state()
        return node

    def _detect_local_capabilities(self) -> NodeCapability:
        """Auto-detect local hardware capabilities."""
        import shutil
        cap = NodeCapability()
        cap.python_version = platform.python_version()
        cap.cpu_cores = os.cpu_count() or 1

        # RAM
        try:
            if platform.system() == "Darwin":
                import subprocess
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=5,
                )
                cap.ram_gb = int(result.stdout.strip()) / (1024**3)
            else:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            cap.ram_gb = int(line.split()[1]) / (1024**2)
                            break
        except Exception:
            pass

        # Apple Silicon detection
        try:
            from able.core.providers.ane_optimizer import detect_chip
            chip = detect_chip()
            if chip:
                cap.chip = chip
                cap.has_ane = True
                cap.gpu_type = f"apple_{chip.lower()}"
        except ImportError:
            pass

        # NVIDIA GPU detection
        if not cap.chip:
            if shutil.which("nvidia-smi"):
                try:
                    import subprocess
                    result = subprocess.run(
                        ["nvidia-smi", "--query-gpu=name,memory.total",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=10,
                    )
                    for line in result.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            cap.gpu_type = f"nvidia_{parts[0].strip().lower().replace(' ', '_')}"
                            cap.vram_gb = float(parts[1].strip()) / 1024
                            break
                except Exception:
                    pass

        # Check for training frameworks
        cap.has_unsloth = bool(shutil.which("unsloth") or _try_import("unsloth"))
        cap.has_axolotl = bool(shutil.which("axolotl") or _try_import("axolotl"))

        return cap

    def get_available_nodes(self) -> List[ComputeNode]:
        """Get all nodes that are alive and idle."""
        return [n for n in self.nodes.values() if n.is_alive and n.status == "idle"]

    def get_best_node_for_job(self, job_type: str, model_size_gb: float = 0) -> Optional[ComputeNode]:
        """Select the best available node for a training job."""
        available = self.get_available_nodes()
        if not available:
            return None

        # Rank by capability
        def score(node: ComputeNode) -> float:
            s = 0.0
            cap = node.capability
            if job_type in ("lora_finetune", "dpo_train"):
                # Prefer GPU with enough VRAM
                if cap.vram_gb >= model_size_gb:
                    s += 10.0
                s += cap.vram_gb
                if cap.has_unsloth:
                    s += 5.0
            elif job_type == "inference_batch":
                # Prefer ANE for inference
                if cap.has_ane:
                    s += 3.0
                s += cap.ram_gb / 10
            # Prefer local node (no network overhead)
            if node.is_local:
                s += 2.0
            return s

        available.sort(key=score, reverse=True)
        return available[0]

    def get_mesh_status(self) -> Dict[str, Any]:
        """Get current mesh status summary."""
        return {
            "total_nodes": len(self.nodes),
            "alive_nodes": sum(1 for n in self.nodes.values() if n.is_alive),
            "idle_nodes": len(self.get_available_nodes()),
            "total_vram_gb": sum(n.capability.vram_gb for n in self.nodes.values()),
            "total_ram_gb": sum(n.capability.ram_gb for n in self.nodes.values()),
            "nodes": [
                {
                    "id": n.node_id,
                    "chip": n.capability.chip or n.capability.gpu_type,
                    "status": n.status,
                    "alive": n.is_alive,
                    "ram_gb": n.capability.ram_gb,
                    "vram_gb": n.capability.vram_gb,
                }
                for n in self.nodes.values()
            ],
        }


def _try_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False
