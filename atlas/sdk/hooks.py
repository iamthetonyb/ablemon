"""Hook system for agent lifecycle events."""

import inspect
import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HookManager:
    """Manages pre/post hooks for agent lifecycle events.

    Events: pre_tool_use, post_tool_use, on_error, on_model_response, on_session_end
    """

    def __init__(self):
        self._hooks: Dict[str, List[Callable]] = {}

    def on(self, event: str):
        """Decorator to register a hook."""

        def decorator(func):
            self._hooks.setdefault(event, []).append(func)
            return func

        return decorator

    async def trigger(self, event: str, **kwargs) -> Optional[bool]:
        """Trigger all hooks for an event. Returns False if any hook denies."""
        handlers = self._hooks.get(event, [])
        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    result = await handler(**kwargs)
                else:
                    result = handler(**kwargs)
                if result is False:
                    return False
            except Exception as e:
                logger.warning(f"Hook {event} error: {e}")
        return True
