from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass
class Address:
    chain: str
    address: str
    path: str = ""


@dataclass
class UnsignedTx:
    to: str
    value: float
    token: str
    chain: str
    seed_label: str = ""


@dataclass
class SignedTx:
    raw: str
    tx_id: str
    chain: str


class IKeyStore(ABC):

    @abstractmethod
    def derive_address(self, seed_label: str, chain: str) -> Address:
        ...

    @abstractmethod
    def sign_transaction(self, unsigned_tx: UnsignedTx) -> SignedTx:
        ...
