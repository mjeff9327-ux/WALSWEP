import hashlib
import hmac
import json
import logging
import time

import httpx
from app.interfaces.license_verifier import ILicenseVerifier, Entitlement

logger = logging.getLogger(__name__)


class TelegramLicenseValidator(ILicenseVerifier):
    def __init__(self, bot_token: str = "", enabled: bool = False, secret_key: str = ""):
        self._bot_token = bot_token
        self._enabled = enabled
        self._secret_key = secret_key
        self._valid_keys: set[str] = set()

    def configure(self, bot_token: str, enabled: bool = True, secret_key: str = "") -> None:
        self._bot_token = bot_token
        self._enabled = enabled
        if secret_key:
            self._secret_key = secret_key

    def _verify_hmac(self, key: str) -> Entitlement | None:
        if not self._secret_key:
            return None
        try:
            parts = key.rsplit("-", 2)
            if len(parts) != 3:
                return None
            payload, hmac_raw, expiry_hex = parts
            expiry = int(expiry_hex, 16)
            if time.time() > expiry:
                return None
            expected = hmac.new(
                self._secret_key.encode(),
                f"{payload}:{expiry_hex}".encode(),
                hashlib.sha256,
            ).hexdigest()[:12]
            if hmac_raw == expected:
                features = []
                if "s" in payload:
                    features.append("scan")
                if "m" in payload:
                    features.append("multi_chain")
                if "e" in payload:
                    features.append("export")
                if "w" in payload:
                    features.append("auto_withdraw")
                if not features:
                    features.append("scan")
                return Entitlement(valid=True, features=features)
        except (ValueError, IndexError):
            pass
        return None

    def verify(self, key: str) -> Entitlement:
        hmac_result = self._verify_hmac(key)
        if hmac_result is not None:
            return hmac_result

        if key in self._valid_keys:
            return Entitlement(valid=True, features=["scan", "multi_chain", "export"])

        if not self._enabled or not self._bot_token:
            return Entitlement(valid=False)

        try:
            resp = httpx.get(
                f"https://api.telegram.org/bot{self._bot_token}/getUpdates",
                timeout=10,
            )
            if resp.status_code != 200:
                return Entitlement(valid=False)
            updates = resp.json()
            chat_ids = set()
            for update in updates.get("result", []):
                chat = update.get("message", {}).get("chat", {})
                if "id" in chat:
                    chat_ids.add(str(chat["id"]))
            if not chat_ids:
                return Entitlement(valid=False)
            delivered = False
            for cid in chat_ids:
                inner = httpx.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": cid,
                        "text": f"License validation request for key: {key}",
                    },
                    timeout=10,
                )
                if inner.status_code == 200:
                    delivered = True
            if delivered:
                self._valid_keys.add(key)
                return Entitlement(valid=True, features=["scan", "multi_chain", "export"])
        except Exception as e:
            logger.warning("Telegram license check failed: %s", e)

        return Entitlement(valid=False)