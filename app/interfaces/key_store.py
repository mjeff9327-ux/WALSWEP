from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Optional


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
    from_address: str = ""
    nonce: int = 0
    gas_price: int = 0
    gas_limit: int = 21000
    utxos: list[dict] = None


@dataclass
class SignedTx:
    raw: str
    tx_id: str
    chain: str
    broadcasted: bool = False
    broadcast_error: Optional[str] = None


class IKeyStore(ABC):

    @abstractmethod
    def derive_address(self, seed_label: str, chain: str) -> Address:
        ...

    @abstractmethod
    async def sign_transaction(self, unsigned_tx: UnsignedTx) -> SignedTx:
        ...
