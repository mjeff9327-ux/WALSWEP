import asyncio
import logging
from typing import Callable, Optional

import httpx
from app.components.mempool_monitor import MempoolMonitor

logger = logging.getLogger(__name__)


class FundingDetector:
    def __init__(self, mempool_monitor: MempoolMonitor):
        self._monitor = mempool_monitor
        self._running = False
        self._watched: dict[str, list[str]] = {}
        self._listeners: list[Callable] = []

    def subscribe(self, callback: Callable) -> None:
        self._listeners.append(callback)

    async def _notify(self, data: dict) -> None:
        for cb in self._listeners:
            try:
                result = cb(data)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error("Funding listener error: %s", e)

    def watch_address(self, address: str, chain: str) -> None:
        if chain not in self._watched:
            self._watched[chain] = []
        self._watched[chain].append(address)
        logger.info("Now watching %s on %s for incoming funds", address, chain)

    async def _check_address(self, address: str, chain: str) -> Optional[float]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if chain == "BTC":
                    resp = await client.get(f"https://blockstream.info/api/address/{address}")
                    data = resp.json()
                    ms = data.get("mempool_stats", {})
                    pending = (ms.get("funded_txo_sum", 0) - ms.get("spent_txo_sum", 0)) / 1e8
                    return pending if pending > 0 else None
                elif chain == "ETH":
                    resp = await client.get(
                        "https://api.etherscan.io/api",
                        params={"module": "account", "action": "balance", "address": address, "tag": "pending"},
                    )
                    data = resp.json()
                    if data.get("status") == "1":
                        bal = int(data["result"]) / 1e18
                        return bal if bal > 0 else None
        except Exception as e:
            logger.debug("Funding check error for %s: %s", address, e)
        return None

    async def run(self, poll_interval: float = 30.0) -> None:
        self._running = True
        logger.info("Funding detector started")
        while self._running:
            for chain, addresses in self._watched.items():
                for addr in addresses:
                    result = await self._check_address(addr, chain)
                    if result is not None:
                        logger.info("Incoming funds detected on %s: %f %s", addr, result, chain)
                        await self._notify({"address": addr, "chain": chain, "amount": result})
            await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        self._running = False
