import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime
from enum import Enum
from pathlib import Path
import json
import logging
import uuid
import pickle

logger = logging.getLogger(__name__)

class QueueMode(Enum):
    COLLECT = "collect"
    STEER = "steer"
    INTERRUPT = "interrupt"

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
    """Redis Stream Execution Lane"""
    
    def __init__(self, lane_id: str, redis_client, max_concurrency: int = 1, audit_dir: str = "audit/logs"):
        self.lane_id = lane_id
        self.stream_key = f"atlas:queue:{lane_id}"
        self.group_name = "atlas_workers"
        self.consumer_name = f"worker-{uuid.uuid4().hex[:8]}"
        self.redis = redis_client
        
        self.max_concurrency = max_concurrency
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory fallback if Redis is down
        self.fallback_queue: asyncio.Queue = asyncio.Queue()
        self.active_count = 0
        self.workers: List[asyncio.Task] = []
        self.running = False

    async def _init_stream(self):
        """Create the consumer group if it doesn't exist"""
        if self.redis:
            try:
                await self.redis.xgroup_create(self.stream_key, self.group_name, id="0", mkstream=True)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    logger.warning(f"Failed to create Redis stream group {self.stream_key}: {e}")

    async def start(self):
        self.running = True
        await self._init_stream()
        for i in range(self.max_concurrency):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)

    async def stop(self):
        self.running = False
        for worker in self.workers:
            worker.cancel()

    async def _worker(self, worker_id: int):
        while self.running:
            try:
                if self.redis:
                    # Redis Stream Worker
                    messages = await self.redis.xreadgroup(
                        self.group_name, 
                        self.consumer_name,
                        {self.stream_key: '>'},
                        count=1,
                        block=1000
                    )
                    
                    if not messages:
                        continue
                        
                    for stream, msg_list in messages:
                        for message_id, msg_data in msg_list:
                            self.active_count += 1
                            try:
                                # Deserialize task
                                raw_task = msg_data.get(b'task')
                                if not raw_task:
                                    continue
                                    
                                task: QueuedTask = pickle.loads(raw_task)
                                
                                # Execute
                                await task.callback(task.payload)
                                
                                # Acknowledge
                                await self.redis.xack(self.stream_key, self.group_name, message_id)
                                
                                self._log_audit(task, worker_id, "completed")
                            except Exception as e:
                                logger.error(f"Worker error processing Redis task: {e}")
                                self._log_audit(task, worker_id, "error", str(e))
                            finally:
                                self.active_count -= 1
                else:
                    # In-memory Fallback Worker
                    task: QueuedTask = await asyncio.wait_for(self.fallback_queue.get(), timeout=1.0)
                    self.active_count += 1
                    try:
                        await task.callback(task.payload)
                        self._log_audit(task, worker_id, "completed")
                    except Exception as e:
                        self._log_audit(task, worker_id, "error", str(e))
                    finally:
                        self.active_count -= 1
                        self.fallback_queue.task_done()
                        
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Worker {worker_id} crashed: {e}")
                await asyncio.sleep(2)  # Backoff

    def _log_audit(self, task: QueuedTask, worker_id: int, status: str, error: str = None):
        queue_log = self.audit_dir / "queue.jsonl"
        with open(queue_log, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.utcnow().isoformat(),
                "lane_id": self.lane_id,
                "task_id": task.task_id,
                "worker_id": worker_id,
                "status": status,
                "error": error
            }) + "\n")

    async def enqueue(self, task: QueuedTask):
        if self.redis:
            try:
                # Serialize and push to stream
                task_bytes = pickle.dumps(task)
                await self.redis.xadd(self.stream_key, {b'task': task_bytes})
                return
            except Exception as e:
                logger.warning(f"Redis XADD failed, falling back to memory queue: {e}")
                
        # Fallback
        await self.fallback_queue.put(task)

class LaneQueue:
    """
    Distributed Multi-lane queue system using Redis Streams.
    Provides session-isolation and parallel/cron execution paths.
    """

    LANE_DEFAULTS = {
        "main": 4,
        "session": 1,  # Serial by default to prevent LLM race conditions
        "parallel": 8,
        "cron": 2
    }

    def __init__(self, audit_dir: str = "audit/logs", redis_url: str = "redis://localhost"):
        self.audit_dir = audit_dir
        self.redis_url = redis_url
        self.redis = None
        self.lanes: Dict[str, Lane] = {}
        self.running = False

    async def connect(self):
        """Connect to Redis"""
        try:
            from redis import asyncio as aioredis
            self.redis = await aioredis.from_url(self.redis_url)
            await self.redis.ping()
            logger.info("🟢 LaneQueue connected to Redis Streams")
        except ImportError:
            logger.warning("🟡 redis library not found. LaneQueue running in fallback memory mode.")
            self.redis = None
        except Exception as e:
            logger.error(f"🔴 Redis connection failed. LaneQueue running in fallback memory mode: {e}")
            self.redis = None

    def _get_lane_type(self, lane_id: str) -> str:
        if lane_id.startswith("session:"): return "session"
        if lane_id.startswith("parallel:"): return "parallel"
        if lane_id.startswith("cron:"): return "cron"
        return "main"

    def _get_or_create_lane(self, lane_id: str) -> Lane:
        if lane_id not in self.lanes:
            lane_type = self._get_lane_type(lane_id)
            max_concurrency = self.LANE_DEFAULTS.get(lane_type, 1)
            self.lanes[lane_id] = Lane(lane_id, self.redis, max_concurrency, self.audit_dir)
            if self.running:
                asyncio.create_task(self.lanes[lane_id].start())
        return self.lanes[lane_id]

    async def start(self):
        if not self.redis:
            await self.connect()
        self.running = True
        for lane in self.lanes.values():
            await lane.start()

    async def stop(self):
        self.running = False
        for lane in self.lanes.values():
            await lane.stop()
        if self.redis:
            await self.redis.aclose()

    async def enqueue(
        self,
        task_id: str,
        lane_id: str,
        payload: Any,
        callback: Callable,
        priority: int = 0,
        mode: QueueMode = QueueMode.COLLECT
    ):
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

    async def enqueue_session(self, session_id: str, payload: Any, callback: Callable):
        lane_id = f"session:{session_id}"
        task_id = f"{session_id}:{datetime.utcnow().timestamp()}"
        await self.enqueue(task_id, lane_id, payload, callback)

    async def enqueue_parallel(self, task_id: str, payload: Any, callback: Callable):
        lane_id = f"parallel:{task_id[:8]}"
        await self.enqueue(task_id, lane_id, payload, callback)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "backend": "redis_streams" if self.redis else "memory_fallback",
            "lane_count": len(self.lanes),
            "lanes": {
                lane_id: {
                    "active_tasks": lane.active_count,
                    "max_concurrency": lane.max_concurrency
                }
                for lane_id, lane in self.lanes.items()
            }
        }
