from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class OperationResult:
    operation: str
    success: bool
    wallet_type: str
    chain: str
    address: str
    private_key_hex: str
    balance_confirmed: float
    balance_usd: float
    details: dict


class IWalletOperator(ABC):

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def description(self) -> str:
        ...

    @abstractmethod
    def available_operations(self) -> list[str]:
        ...

    @abstractmethod
    def execute(self, operation: str, seed: str) -> OperationResult:
        ...
