import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SweepEngine:
    def __init__(self, config: dict):
        self._config = config
        self._log: list[dict] = []

    def execute(self, chain: str, address: str, balance: float, usd_value: float = 0.0) -> dict:
        dest = self._config.get("sweep", {}).get("destination_wallet", {}).get(chain, "")
        dest_display = dest[:8] + "..." + dest[-4:] if len(dest) > 12 else (dest or "not configured")

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chain": chain,
            "source_address": address,
            "balance": balance,
            "usd_value": usd_value,
            "destination": dest_display,
            "status": "broadcast_pending" if dest else "no_destination",
        }
        self._log.append(entry)

        return entry

    def recent_log(self, limit: int = 10) -> list[dict]:
        return self._log[-limit:]

    @property
    def total_executed(self) -> int:
        return len(self._log)

    def summary(self) -> str:
        if not self._log:
            return "No sweeps executed yet."
        total_btc = sum(e["balance"] for e in self._log if e["chain"] == "BTC")
        total_eth = sum(e["balance"] for e in self._log if e["chain"] == "ETH")
        total_usd = sum(e.get("usd_value", 0) for e in self._log)
        return (
            f"Executed {len(self._log)} sweep(s). "
            f"Total: {total_btc:.8f} BTC, {total_eth:.8f} ETH (~${total_usd:.2f})"
        )
