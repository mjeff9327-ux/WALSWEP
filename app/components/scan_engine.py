import asyncio
import logging
import time
from typing import Optional

from app.interfaces.solver import ISolver
from app.interfaces.key_store import IKeyStore
from app.interfaces.node_client import INodeClient, Balance
from app.interfaces.webhook_client import IWebhookClient, EventResult
from app.components.event_bus import EventBus
from app.components.config_manager import ConfigManager
from app.components.token_scanner import TokenScanner
from app.engine.multisig_scanner import derive_safe_addresses
from app.engine.social_wallet_scanner import derive_argent_addresses

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
        token_scanner: Optional[TokenScanner] = None,
        chains: list[str] | None = None,
    ):
        self._solver = solver
        self._key_store = key_store
        self._node_client = node_client
        self._webhook_client = webhook_client
        self._event_bus = event_bus
        self._config = config
        self._token_scanner = token_scanner
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

        chain_addrs = [(a["chain"], a["address"]) for a in derived.addresses
                       if a["chain"] in self._chains and a["address"]]
        if chain_addrs:
            tasks = [self._node_client.query_balance(addr, c) for c, addr in chain_addrs]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (chain, address), res in zip(chain_addrs, results):
                if isinstance(res, Exception):
                    balance = Balance(token=chain, confirmed=0.0, pending=0.0)
                else:
                    balance = res
                balances.append({"chain": chain, "address": address, "balance": balance})
                if balance.confirmed > 0:
                    found_any = True

        if self._config and self._config.get("scan", "check_safe", False) and found_any:
            try:
                eth_addrs = [a["address"] for a in derived.addresses if a["chain"] == "ETH" and a["address"]]
                for eoa in eth_addrs:
                    safes = derive_safe_addresses(eoa)
                    for s in safes:
                        sb = await self._node_client.query_balance(s["address"], "ETH")
                        if sb.confirmed > 0:
                            balances.append({"chain": f"Safe_{s.get('version','?')}", "address": s["address"], "balance": sb, "note": "Smart contract wallet (multi-sig)"})
                            found_any = True
            except Exception as e:
                logger.debug("Safe derivation check failed: %s", e)

        if self._config and self._config.get("scan", "check_argent", False) and found_any:
            try:
                eth_addrs = [a["address"] for a in derived.addresses if a["chain"] == "ETH" and a["address"]]
                for eoa in eth_addrs:
                    argents = derive_argent_addresses(eoa)
                    for a in argents:
                        ab = await self._node_client.query_balance(a["address"], "ETH")
                        if ab.confirmed > 0:
                            balances.append({"chain": f"Argent_{a.get('version','?')}", "address": a["address"], "balance": ab, "note": "Social recovery smart wallet"})
                            found_any = True
            except Exception as e:
                logger.debug("Argent derivation check failed: %s", e)

                if scan_token and self._token_scanner and balance.confirmed > 0:
                    token_balances = await self._token_scanner.scan(address, chain)
                    for tb in token_balances:
                        if tb.get("balance", 0) > 0:
                            synthetic = Balance(
                                token=f"{tb['symbol']}_{chain}",
                                confirmed=tb["balance"],
                                pending=0.0,
                                usd_value=tb.get("usd_value", 0),
                            )
                            balances.append({"chain": f"{tb['symbol']}_{chain}", "address": address, "balance": synthetic})
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
