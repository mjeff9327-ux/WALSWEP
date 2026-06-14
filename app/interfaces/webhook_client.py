from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass
class EventResult:
    success: bool
    message: str = ""


class IWebhookClient(ABC):

    @abstractmethod
    def post_event(self, event: dict) -> EventResult:
        ...
