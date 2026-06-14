import asyncio
import logging
import time
from typing import Optional

from app.interfaces.solver import ISolver
from app.interfaces.key_store import IKeyStore
from app.interfaces.node_client import INodeClient
from app.interfaces.webhook_client import IWebhookClient, EventResult
from app.components.event_bus import EventBus
from app.components.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class ScanResult:
    def __init__(self, pattern: str, addresses: list[dict], balances: list[dict], found: bool):
        self.pattern = pattern
        self.addresses = addresses
        self.balances = balances
        self.found = found


class ScanEngine:
    def __init__(
        self,
        solver: ISolver,
        key_store: IKeyStore,
        node_client: INodeClient,
        webhook_client: IWebhookClient,
        event_bus: EventBus,
        config: Optional[ConfigManager] = None,
        chains: list[str] | None = None,
    ):
        self._solver = solver
        self._key_store = key_store
        self._node_client = node_client
        self._webhook_client = webhook_client
        self._event_bus = event_bus
        self._config = config
        self._chains = chains or ["BTC", "ETH", "LTC", "SOL"]
        self._running = False
        self._stats = {"scanned": 0, "found": 0, "started_at": None}
        self._on_result: Optional[callable] = None

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def is_running(self) -> bool:
        return self._running

    def set_on_result(self, callback: callable) -> None:
        self._on_result = callback

    def set_chains(self, chains: list[str]) -> None:
        self._chains = chains

    async def scan_single(self, pattern: str) -> ScanResult:
        derived = self._solver.solve(pattern)
        balances = []
        found_any = False

        scan_token = False
        if self._config:
            scan_token = self._config.get("scan", "check_erc20_tokens", False)

        for addr_info in derived.addresses:
            chain = addr_info["chain"]
            if chain not in self._chains:
                continue
            address = addr_info["address"]
            if not address:
                continue
            balance = await self._node_client.query_balance(address, chain)
            balances.append({"chain": chain, "address": address, "balance": balance})
            if balance.confirmed > 0:
                found_any = True

            if scan_token and chain == "ETH" and balance.confirmed > 0:
                usdt_bal = await self._node_client.query_balance(address, "USDT_ERC20")
                if usdt_bal.confirmed > 0:
                    balances.append({"chain": "USDT_ERC20", "address": address, "balance": usdt_bal})
                    found_any = True

        result = ScanResult(
            pattern=pattern,
            addresses=derived.addresses,
            balances=balances,
            found=found_any,
        )

        if found_any:
            self._stats["found"] += 1
            await self._event_bus.emit("FOUND", {
                "pattern": pattern,
                "balances": [(b["chain"], b["balance"].confirmed) for b in balances if b["balance"].confirmed > 0],
            })
            webhook_event = {
                "type": "FOUND",
                "pattern": pattern,
                "timestamp": time.time(),
                "balances": [{"chain": b["chain"], "address": b["address"], "confirmed": b["balance"].confirmed} for b in balances],
            }
            self._webhook_client.post_event(webhook_event)

        self._stats["scanned"] += 1

        if self._on_result:
            self._on_result(result)

        return result

    async def run_continuous(self, pattern_generator, delay: float = 0.0) -> None:
        self._running = True
        self._stats["started_at"] = time.time()
        logger.info("Scan engine started")
        await self._event_bus.emit("SCAN_STARTED", {"timestamp": self._stats["started_at"]})
        try:
            async for pattern in pattern_generator:
                if not self._running:
                    break
                await self.scan_single(pattern)
                if delay > 0:
                    await asyncio.sleep(delay)
        finally:
            self._running = False
            elapsed = time.time() - (self._stats["started_at"] or time.time())
            logger.info("Scan engine stopped. Scanned: %d, Found: %d, Elapsed: %.2fs", self._stats["scanned"], self._stats["found"], elapsed)
            await self._event_bus.emit("SCAN_STOPPED", {"scanned": self._stats["scanned"], "found": self._stats["found"]})

    def stop(self) -> None:
        self._running = False
