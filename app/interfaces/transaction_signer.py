from abc import ABC, abstractmethod
from .key_store import UnsignedTx, SignedTx


class ITransactionSigner(ABC):

    @abstractmethod
    async def sign(self, unsigned_tx: UnsignedTx) -> SignedTx:
        ...

    @abstractmethod
    async def broadcast(self, signed_tx: SignedTx) -> str:
        ...
