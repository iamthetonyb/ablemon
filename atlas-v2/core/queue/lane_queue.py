"""
Lane Queue - OpenClaw-inspired serial execution with explicit parallelism
Default to serial, go parallel explicitly. Eliminates race conditions.
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime
from enum import Enum
from pathlib import Path
import json

class QueueMode(Enum):
    COLLECT = "collect"    # Coalesce queued messages
    STEER = "steer"        # Inject into current run
    INTERRUPT = "interrupt" # Abort current, process new

@dataclass
class QueuedTask:
    task_id: str
    lane_id: str
    payload: Any
    callback: Callable
    priority: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    mode: QueueMode = QueueMode.COLLECT

class Lane:
    """Single execution lane - processes tasks serially"""

    def __init__(self, lane_id: str, max_concurrency: int = 1, audit_dir: str = "audit/logs"):
        self.lane_id = lane_id
        self.max_concurrency = max_concurrency
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_count = 0
        self.workers: List[asyncio.Task] = []
        self.running = False

    async def start(self):
        """Start lane workers"""
        self.running = True
        for i in range(self.max_concurrency):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)

    async def stop(self):
        """Stop lane workers"""
        self.running = False
        for worker in self.workers:
            worker.cancel()

    async def _worker(self, worker_id: int):
        """Worker loop - processes tasks serially"""
        while self.running:
            try:
                task: QueuedTask = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=1.0
                )
                self.active_count += 1

                try:
                    result = await task.callback(task.payload)
                    # Log completion
                    queue_log = self.audit_dir / "queue.jsonl"
                    with open(queue_log, "a") as f:
                        f.write(json.dumps({
                            "timestamp": datetime.utcnow().isoformat(),
                            "lane_id": self.lane_id,
                            "task_id": task.task_id,
                            "worker_id": worker_id,
                            "status": "completed"
                        }) + "\n")
                except Exception as e:
                    queue_log = self.audit_dir / "queue.jsonl"
                    with open(queue_log, "a") as f:
                        f.write(json.dumps({
                            "timestamp": datetime.utcnow().isoformat(),
                            "lane_id": self.lane_id,
                            "task_id": task.task_id,
                            "worker_id": worker_id,
                            "status": "error",
                            "error": str(e)
                        }) + "\n")
                finally:
                    self.active_count -= 1
                    self.queue.task_done()

            except asyncio.TimeoutError:
                continue

    async def enqueue(self, task: QueuedTask):
        """Add task to lane queue"""
        await self.queue.put(task)

class LaneQueue:
    """
    Multi-lane queue system.
    Each session gets its own lane for serial execution.
    Parallel lanes for explicitly parallel tasks.
    """

    # Default concurrency limits per lane type
    LANE_DEFAULTS = {
        "main": 4,
        "session": 1,  # Serial by default
        "parallel": 8,
        "cron": 2
    }

    def __init__(self, audit_dir: str = "audit/logs"):
        self.audit_dir = audit_dir
        self.lanes: Dict[str, Lane] = {}
        self.running = False

    def _get_lane_type(self, lane_id: str) -> str:
        """Determine lane type from ID"""
        if lane_id.startswith("session:"):
            return "session"
        if lane_id.startswith("parallel:"):
            return "parallel"
        if lane_id.startswith("cron:"):
            return "cron"
        return "main"

    def _get_or_create_lane(self, lane_id: str) -> Lane:
        """Get existing lane or create new one"""
        if lane_id not in self.lanes:
            lane_type = self._get_lane_type(lane_id)
            max_concurrency = self.LANE_DEFAULTS.get(lane_type, 1)
            self.lanes[lane_id] = Lane(lane_id, max_concurrency, self.audit_dir)
            if self.running:
                asyncio.create_task(self.lanes[lane_id].start())
        return self.lanes[lane_id]

    async def start(self):
        """Start all lanes"""
        self.running = True
        for lane in self.lanes.values():
            await lane.start()

    async def stop(self):
        """Stop all lanes"""
        self.running = False
        for lane in self.lanes.values():
            await lane.stop()

    async def enqueue(
        self,
        task_id: str,
        lane_id: str,
        payload: Any,
        callback: Callable,
        priority: int = 0,
        mode: QueueMode = QueueMode.COLLECT
    ):
        """Enqueue a task to a specific lane"""
        lane = self._get_or_create_lane(lane_id)
        task = QueuedTask(
            task_id=task_id,
            lane_id=lane_id,
            payload=payload,
            callback=callback,
            priority=priority,
            mode=mode
        )
        await lane.enqueue(task)

    async def enqueue_session(
        self,
        session_id: str,
        payload: Any,
        callback: Callable
    ):
        """Convenience method for session tasks (always serial)"""
        lane_id = f"session:{session_id}"
        task_id = f"{session_id}:{datetime.utcnow().timestamp()}"
        await self.enqueue(task_id, lane_id, payload, callback)

    async def enqueue_parallel(
        self,
        task_id: str,
        payload: Any,
        callback: Callable
    ):
        """Convenience method for explicitly parallel tasks"""
        lane_id = f"parallel:{task_id[:8]}"
        await self.enqueue(task_id, lane_id, payload, callback)

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        return {
            "lane_count": len(self.lanes),
            "lanes": {
                lane_id: {
                    "queue_size": lane.queue.qsize(),
                    "active_tasks": lane.active_count,
                    "max_concurrency": lane.max_concurrency
                }
                for lane_id, lane in self.lanes.items()
            }
        }
