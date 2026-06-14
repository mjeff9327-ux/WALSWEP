import json
import logging
import os
from datetime import datetime, timezone

import httpx
from app.interfaces.webhook_client import IWebhookClient, EventResult

logger = logging.getLogger(__name__)


class WebhookClient(IWebhookClient):
    def __init__(self, target_url: str = "", output_dir: str = "events"):
        self._target_url = target_url
        self._output_dir = output_dir
        self._client = httpx.Client(timeout=10)

    def post_event(self, event: dict) -> EventResult:
        os.makedirs(self._output_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{self._output_dir}/event_{ts}.json"
        try:
            with open(filename, "w") as f:
                json.dump(event, f, indent=2)
        except Exception as e:
            logger.warning("Failed to write event file: %s", e)

        if self._target_url:
            try:
                resp = self._client.post(self._target_url, json=event, timeout=10)
                if resp.status_code == 200:
                    logger.info("Webhook sent to %s (status=%d)", self._target_url, resp.status_code)
                    return EventResult(success=True, message=f"Webhook delivered to {self._target_url}")
                else:
                    logger.warning("Webhook failed: HTTP %d from %s", resp.status_code, self._target_url)
                    return EventResult(success=False, message=f"HTTP {resp.status_code}")
            except Exception as e:
                logger.error("Webhook HTTP error: %s", e)
                return EventResult(success=False, message=str(e))

        return EventResult(success=True, message=f"Written to {filename}")
