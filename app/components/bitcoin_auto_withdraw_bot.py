import asyncio
import logging
from typing import Optional

import httpx
from app.interfaces.node_client import INodeClient, Balance
from app.interfaces.key_store import IKeyStore, UnsignedTx
from app.interfaces.transaction_signer import ITransactionSigner
from app.components.event_bus import EventBus
from app.components.config_manager import ConfigManager
from app.implementations.transaction_signer import CHAIN_COINS

logger = logging.getLogger(__name__)


class WithdrawalProposal:
    def __init__(self, address: str, amount: float, fee: float, destination: str, chain: str, usd_value: float = 0):
        self.address = address
        self.amount = amount
        self.fee = fee
        self.destination = destination
        self.chain = chain
        self.usd_value = usd_value


GAS_API = {
    "ETH": "https://api.etherscan.io/api?module=gastracker&action=gasoracle",
    "BNB": "https://api.bscscan.com/api?module=gastracker&action=gasoracle",
    "POLYGON": "https://api.polygonscan.com/api?module=gastracker&action=gasoracle",
    "BTC": "https://blockstream.info/api/fee-estimates",
}


class AutoSweepBot:
    def __init__(
        self,
        node_client: INodeClient,
        key_store: IKeyStore,
        signer: ITransactionSigner,
        event_bus: EventBus,
        config: ConfigManager,
    ):
        self._node_client = node_client
        self._key_store = key_store
        self._signer = signer
        self._event_bus = event_bus
        self._config = config
        self._running = False
        self._gas_cache: dict[str, tuple[int, float]] = {}
        self._pending_watch: list[WithdrawalProposal] = []

    @property
    def is_running(self) -> bool:
        return self._running

    async def _get_gas_price(self, chain: str) -> int:
        now = asyncio.get_event_loop().time()
        if chain in self._gas_cache:
            price, ts = self._gas_cache[chain]
            if now - ts < 60:
                return price
        try:
            url = GAS_API.get(chain)
            if not url:
                return 0
            async with httpx.AsyncClient(timeout=5) as client:
                if chain == "BTC":
                    resp = await client.get(url)
                    data = resp.json()
                    fastest = int(data.get("2", 0))
                    self._gas_cache[chain] = (fastest, now)
                    return fastest
                else:
                    resp = await client.get(url)
                    data = resp.json()
                    if data.get("status") == "1":
                        result = data.get("result", {})
                        propose = int(result.get("ProposeGasPrice", result.get("SafeGasPrice", 0)))
                        multiplier = self._config.get("sweep", "priority_fee_multiplier", 1.0)
                        price = int(propose * multiplier)
                        self._gas_cache[chain] = (price, now)
                        return price
        except Exception as e:
            logger.debug("Gas fetch failed for %s: %s", chain, e)
        return 0

    async def evaluate(self, seed_label: str, chain: str = "BTC") -> Optional[WithdrawalProposal]:
        if not seed_label:
            return None
        address = self._key_store.derive_address(seed_label, chain).address
        if not address:
            return None
        balance = await self._node_client.query_balance(address, chain)
        if balance.confirmed <= 0:
            return None
        usd_value = balance.usd_value or 0
        min_usd = self._config.get("sweep", "min_balance_usd", 10.0)
        if usd_value < min_usd:
            return None
        dest = self._config.get_destination(chain)
        if not dest:
            logger.warning("No destination wallet configured for %s", chain)
            return None
        fee_rate = await self._get_gas_price(chain)
        fee = round(balance.confirmed * 0.001, 8)
        proposal = WithdrawalProposal(
            address=address,
            amount=balance.confirmed - fee,
            fee=fee,
            destination=dest,
            chain=chain,
            usd_value=usd_value,
        )
        logger.info("Sweep proposal for %s: %f %s ($%.2f) -> %s", address, proposal.amount, chain, usd_value, dest)
        await self._event_bus.emit("WITHDRAWAL_PROPOSAL", {
            "address": address,
            "amount": proposal.amount,
            "fee": proposal.fee,
            "destination": proposal.destination,
            "chain": chain,
            "usd_value": usd_value,
        })
        auto_broadcast = self._config.get("sweep", "auto_broadcast", False)
        if auto_broadcast:
            self._signer.set_mnemonic(seed_label)
            unsigned = UnsignedTx(
                to=dest, value=proposal.amount, token=chain, chain=chain,
                from_address=address, seed_label=seed_label,
            )
            signed = await self._signer.sign(unsigned)
            if signed.broadcast_error:
                logger.warning("Signing failed for %s: %s", chain, signed.broadcast_error)
            else:
                txid = await self._signer.broadcast(signed)
                logger.info("Auto-sweep tx %s on %s (gas: %d): %s", signed.tx_id, chain, fee_rate, txid)
                await self._event_bus.emit("SWEEP_EXECUTED", {
                    "tx_id": signed.tx_id,
                    "chain": chain,
                    "amount": proposal.amount,
                    "destination": dest,
                    "broadcast_result": txid,
                })
        return proposal

    async def watch_pending_sweeps(self) -> None:
        while self._running:
            for prop in list(self._pending_watch):
                balance = await self._node_client.query_balance(prop.address, prop.chain)
                if balance.confirmed < prop.amount:
                    logger.info("Pending sweep %s on %s has been spent or moved", prop.address, prop.chain)
                    self._pending_watch.remove(prop)
            await asyncio.sleep(60)

    async def run(self, check_interval: float = 30.0) -> None:
        self._running = True
        logger.info("AutoSweepBot started (interval=%ss, auto_broadcast=%s)", check_interval, self._config.get("sweep", "auto_broadcast", False))
        pending_task = asyncio.create_task(self.watch_pending_sweeps())
        try:
            while self._running:
                for chain in CHAIN_COINS:
                    dest = self._config.get_destination(chain)
                    if not dest:
                        continue
                    balance = await self._node_client.query_balance(dest, chain)
                    if balance.confirmed > 0 and balance.usd_value and balance.usd_value > 10:
                        logger.info("Destination %s on %s has %.8f ($%.2f) waiting", dest, chain, balance.confirmed, balance.usd_value)
                await asyncio.sleep(check_interval)
        finally:
            pending_task.cancel()

    def stop(self) -> None:
        self._running = False
