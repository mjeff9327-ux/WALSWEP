import asyncio
import logging
from typing import Callable

import httpx
from app.interfaces.node_client import INodeClient

logger = logging.getLogger(__name__)


MEMPOOL_API = {
    "BTC": "https://blockstream.info/api/mempool",
    "ETH": "https://api.etherscan.io/api?module=proxy&action=eth_blockNumber",
    "SOL": "https://api.mainnet-beta.solana.com",
}


class MempoolMonitor:
    def __init__(self, node_client: INodeClient):
        self._node_client = node_client
        self._listeners: list[Callable] = []
        self._running = False

    def subscribe(self, callback: Callable) -> None:
        self._listeners.append(callback)

    async def watch_chain(self, chain: str, poll_interval: float = 30.0) -> None:
        self._running = True
        logger.info("Mempool monitor started for %s (poll every %.1fs)", chain, poll_interval)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                while self._running:
                    try:
                        if chain == "BTC":
                            resp = await client.get(MEMPOOL_API["BTC"])
                            if resp.status_code == 200:
                                tx_count = len(resp.json() if isinstance(resp.json(), list) else [])
                                await self._notify({"chain": chain, "mempool_tx_count": tx_count})
                        elif chain == "ETH":
                            resp = await client.get(MEMPOOL_API["ETH"])
                            if resp.status_code == 200:
                                data = resp.json()
                                if data.get("status") == "1":
                                    await self._notify({"chain": chain, "latest_block": data["result"]})
                        elif chain == "SOL":
                            resp = await client.post(
                                MEMPOOL_API["SOL"],
                                json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"},
                            )
                            if resp.status_code == 200:
                                slot = resp.json().get("result", 0)
                                await self._notify({"chain": chain, "slot": slot})
                    except Exception as e:
                        logger.debug("Mempool poll error for %s: %s", chain, e)
                    await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    async def _notify(self, data: dict) -> None:
        for cb in self._listeners:
            try:
                result = cb(data)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error("Mempool listener error: %s", e)

    def stop(self) -> None:
        self._running = False
