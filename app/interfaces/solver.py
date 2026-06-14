from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class DerivedAddressSet:
    pattern: str
    addresses: list[dict] = field(default_factory=list)
    synthetic: bool = False


class ISolver(ABC):

    @abstractmethod
    def solve(self, input_pattern: str) -> DerivedAddressSet:
        ...
