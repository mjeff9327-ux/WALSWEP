import logging
from typing import Callable
from collections import defaultdict

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callable) -> None:
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        if event_type in self._subscribers:
            self._subscribers[event_type].remove(callback)

    async def emit(self, event_type: str, data: dict) -> None:
        logger.debug("Event emitted: %s", event_type)
        for cb in self._subscribers.get(event_type, []):
            try:
                result = cb(data)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error("Event callback error on %s: %s", event_type, e)
