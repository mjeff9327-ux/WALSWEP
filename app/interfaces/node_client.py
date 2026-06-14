from dataclasses import dataclass, field
from typing import Optional
from abc import ABC, abstractmethod


@dataclass
class Balance:
    token: str
    confirmed: float
    pending: float
    usd_value: Optional[float] = None


@dataclass
class EventStream:
    chain: str
    filters: list[str] = field(default_factory=list)


class INodeClient(ABC):

    @abstractmethod
    async def query_balance(self, address: str, token: str) -> Balance:
        ...

    @abstractmethod
    async def subscribe_mempool(self, filter_data: EventStream) -> None:
        ...
