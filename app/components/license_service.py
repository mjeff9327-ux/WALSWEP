import uuid
import time
import hmac
import hashlib
from app.interfaces.license_verifier import ILicenseVerifier, Entitlement


class LicenseService:
    def __init__(self, verifier: ILicenseVerifier):
        self._verifier = verifier
        self._tokens: dict[str, dict] = {}

    def verify(self, key: str) -> Entitlement:
        return self._verifier.verify(key)

    def generate(self, admin_key: str) -> str:
        secret = self._get_secret()
        if not secret:
            return ""
        expected = hmac.new(
            secret.encode(),
            b"admin:license:generate",
            hashlib.sha256,
        ).hexdigest()[:16]
        if not hmac.compare_digest(admin_key, expected):
            return ""
        expiry = int(time.time()) + 365 * 86400
        payload = "sme"
        mac = hmac.new(
            secret.encode(),
            f"{payload}:{expiry:x}".encode(),
            hashlib.sha256,
        ).hexdigest()[:12]
        token = f"{payload}-{mac}-{expiry:x}"
        self._tokens[token] = {
            "features": ["scan", "multi_chain", "export"],
            "created_at": time.time(),
            "expires_at": expiry,
        }
        return token

    def _get_secret(self) -> str:
        if hasattr(self._verifier, "_secret_key"):
            return self._verifier._secret_key or ""
        return ""

    def generate_dev_token(self, features: list[str] | None = None) -> str:
        token = f"DEV-{uuid.uuid4().hex[:16].upper()}"
        self._tokens[token] = {
            "features": features or ["scan", "multi_chain", "export"],
            "created_at": time.time(),
            "type": "dev",
        }
        return token

    def validate_token(self, token: str) -> bool:
        return token in self._tokens
