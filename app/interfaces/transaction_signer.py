from abc import ABC, abstractmethod
from .key_store import UnsignedTx, SignedTx


class ITransactionSigner(ABC):

    @abstractmethod
    def sign(self, unsigned_tx: UnsignedTx) -> SignedTx:
        ...
