"""
Async event bus for ABLE gateway component decoupling.

Replaces ad-hoc _push_event / _sse_subscribers with a typed, topic-based
publish-subscribe system. Components emit events without knowing who
listens; subscribers register handlers without coupling to producers.

Inspired by RhysSullivan/executor's plugin event model — adapted for
Python asyncio with backpressure and typed events.

Usage:
    bus = EventBus()

    # Subscribe
    bus.subscribe("routing.decision", my_handler)
    bus.subscribe("interaction.*", wildcard_handler)  # glob patterns

    # Publish
    await bus.emit("routing.decision", {"tier": 2, "provider": "gpt-5.4"})

    # SSE bridge
    sse_bridge = SSEBridge(bus)
    sse_bridge.subscribe_all()  # Routes bus events to SSE subscribers
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[["Event"], Awaitable[None]]


@dataclass
class Event:
    """Typed event flowing through the bus."""
    topic: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # Component that emitted the event

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "data": self.data,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class Subscription:
    """A registered event subscription."""
    pattern: str  # Topic pattern (supports * glob matching)
    handler: EventHandler
    subscriber_name: str = ""
    created_at: float = field(default_factory=time.time)

    def matches(self, topic: str) -> bool:
        """Check if a topic matches this subscription's pattern."""
        return fnmatch.fnmatch(topic, self.pattern)


class EventBus:
    """
    Lightweight async event bus with topic-based pub/sub.

    Features:
    - Topic glob matching (e.g., "routing.*" matches "routing.decision")
    - Async handlers with error isolation (one failing handler doesn't block others)
    - Backpressure via bounded internal queue
    - Event history for debugging (last N events)
    - Metrics: events emitted, handlers invoked, errors
    """

    def __init__(self, history_size: int = 100, max_queue: int = 1000):
        self._subscriptions: List[Subscription] = []
        self._history: List[Event] = []
        self._history_size = history_size
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue)
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Metrics
        self.events_emitted: int = 0
        self.handlers_invoked: int = 0
        self.handler_errors: int = 0

    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        subscriber_name: str = "",
    ) -> Subscription:
        """Register a handler for events matching the given topic pattern."""
        sub = Subscription(
            pattern=pattern,
            handler=handler,
            subscriber_name=subscriber_name or handler.__qualname__,
        )
        self._subscriptions.append(sub)
        logger.debug("EventBus: subscribed %s to '%s'", sub.subscriber_name, pattern)
        return sub

    def unsubscribe(self, subscription: Subscription) -> bool:
        """Remove a subscription. Returns True if found and removed."""
        try:
            self._subscriptions.remove(subscription)
            return True
        except ValueError:
            return False

    async def emit(self, topic: str, data: Dict[str, Any], source: str = "") -> None:
        """
        Publish an event to the bus.

        If the processing loop is running, events are queued.
        Otherwise, handlers are invoked directly (synchronous mode).
        """
        event = Event(topic=topic, data=data, source=source)
        self.events_emitted += 1

        # Store in history
        self._history.append(event)
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size:]

        if self._running:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("EventBus queue full — dropping event: %s", topic)
        else:
            # Direct dispatch (no processing loop)
            await self._dispatch(event)

    async def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all matching subscribers."""
        for sub in self._subscriptions:
            if sub.matches(event.topic):
                try:
                    await sub.handler(event)
                    self.handlers_invoked += 1
                except Exception as e:
                    self.handler_errors += 1
                    logger.error(
                        "EventBus handler error: %s on '%s': %s",
                        sub.subscriber_name, event.topic, e,
                    )

    async def start(self) -> None:
        """Start the background event processing loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("EventBus started")

    async def stop(self) -> None:
        """Stop the processing loop and drain remaining events."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Drain remaining events
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                await self._dispatch(event)
            except asyncio.QueueEmpty:
                break
        logger.info("EventBus stopped (emitted=%d, invoked=%d, errors=%d)",
                     self.events_emitted, self.handlers_invoked, self.handler_errors)

    async def _process_loop(self) -> None:
        """Background loop that processes queued events."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("EventBus process loop error: %s", e)

    @property
    def recent_events(self) -> List[Dict[str, Any]]:
        """Return recent event history as dicts."""
        return [e.to_dict() for e in self._history[-20:]]

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)

    @property
    def topics(self) -> Set[str]:
        """All unique topics seen in recent history."""
        return {e.topic for e in self._history}

    def stats(self) -> Dict[str, Any]:
        """Return bus statistics."""
        return {
            "events_emitted": self.events_emitted,
            "handlers_invoked": self.handlers_invoked,
            "handler_errors": self.handler_errors,
            "subscriptions": self.subscription_count,
            "queue_size": self._queue.qsize(),
            "running": self._running,
        }


class SSEBridge:
    """
    Bridges the EventBus to SSE (Server-Sent Events) subscribers.

    Replaces the gateway's ad-hoc _sse_subscribers list with a proper
    event bus consumer that forwards events to SSE connections.
    """

    MAX_SUBSCRIBERS = 100

    def __init__(self, bus: EventBus):
        self.bus = bus
        self._subscribers: List[asyncio.Queue] = []
        self._subscription: Optional[Subscription] = None

    def start(self) -> None:
        """Subscribe to all bus events and forward to SSE connections."""
        self._subscription = self.bus.subscribe(
            "*",
            self._forward_to_sse,
            subscriber_name="SSEBridge",
        )

    def stop(self) -> None:
        """Unsubscribe from bus."""
        if self._subscription:
            self.bus.unsubscribe(self._subscription)

    async def _forward_to_sse(self, event: Event) -> None:
        """Forward a bus event to all SSE subscriber queues."""
        import json
        payload = json.dumps({
            "type": event.topic,
            "data": event.data,
            "ts": event.timestamp,
        })
        dead: List[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def add_subscriber(self) -> Optional[asyncio.Queue]:
        """Register a new SSE connection. Returns queue or None if at capacity."""
        if len(self._subscribers) >= self.MAX_SUBSCRIBERS:
            return None
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        return q

    def remove_subscriber(self, q: asyncio.Queue) -> None:
        """Unregister an SSE connection."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
