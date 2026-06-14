from dataclasses import dataclass
from abc import ABC, abstractmethod


@dataclass
class Entitlement:
    valid: bool
    features: list[str] | None = None
    expires_at: str = ""


class ILicenseVerifier(ABC):

    @abstractmethod
    def verify(self, key: str) -> Entitlement:
        ...
