import time
import logging

import httpx
from app.interfaces.node_client import INodeClient, Balance, EventStream

logger = logging.getLogger(__name__)


SOL_RPC = "https://api.mainnet-beta.solana.com"


ERC20_USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
TRC20_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class LiveNodeClient(INodeClient):
    def __init__(self, timeout: float = 15.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache: dict[str, tuple[Balance, float]] = {}
        self._cache_ttl = 30.0
        self._price_cache: dict[str, tuple[float, float]] = {}
        self._subscriptions: list[EventStream] = []

    async def _get_usd_price(self, token: str) -> float:
        now = time.time()
        if token in self._price_cache:
            price, ts = self._price_cache[token]
            if now - ts < 300:
                return price
        coin_id_map = {
            "BTC": "bitcoin", "ETH": "ethereum", "LTC": "litecoin", "SOL": "solana",
            "BNB": "binancecoin", "XRP": "ripple", "TRON": "tron", "POLYGON": "polygon",
            "USDT": "tether", "USDC": "usd-coin",
        }
        cid = coin_id_map.get(token)
        if not cid:
            return 0.0
        try:
            resp = await self._client.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd",
                timeout=5,
            )
            data = resp.json()
            price = float(data.get(cid, {}).get("usd", 0))
            self._price_cache[token] = (price, now)
            return price
        except Exception as e:
            logger.debug("Price fetch failed for %s: %s", token, e)
            return 0.0

    async def _check_btc(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(f"https://blockstream.info/api/address/{address}")
            data = resp.json()
            cs = data.get("chain_stats", {})
            ms = data.get("mempool_stats", {})
            funded = cs.get("funded_txo_sum", 0)
            spent = cs.get("spent_txo_sum", 0)
            confirmed = (funded - spent) / 1e8
            pending = (ms.get("funded_txo_sum", 0) - ms.get("spent_txo_sum", 0)) / 1e8
            usd = confirmed * await self._get_usd_price("BTC")
            return confirmed, pending, usd
        except Exception as e:
            logger.debug("BTC balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_eth(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(
                "https://api.etherscan.io/api",
                params={"module": "account", "action": "balance", "address": address, "tag": "latest"},
            )
            data = resp.json()
            if data.get("status") == "1":
                confirmed = int(data["result"]) / 1e18
            else:
                confirmed = 0.0
            usd = confirmed * await self._get_usd_price("ETH")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("ETH balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_ltc(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/balance")
            data = resp.json()
            confirmed = data.get("balance", 0) / 1e8
            pending = data.get("unconfirmed_balance", 0) / 1e8
            usd = confirmed * await self._get_usd_price("LTC")
            return confirmed, pending, usd
        except Exception as e:
            logger.debug("LTC balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_sol(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.post(
                SOL_RPC,
                json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]},
            )
            data = resp.json()
            lamports = data.get("result", {}).get("value", 0)
            confirmed = lamports / 1e9
            usd = confirmed * await self._get_usd_price("SOL")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("SOL balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_bnb(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(
                "https://api.bscscan.com/api",
                params={"module": "account", "action": "balance", "address": address, "tag": "latest"},
            )
            data = resp.json()
            if data.get("status") == "1":
                confirmed = int(data["result"]) / 1e18
            else:
                confirmed = 0.0
            usd = confirmed * await self._get_usd_price("BNB")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("BNB balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_xrp(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(f"https://api.xrpscan.com/api/v1/account/{address}")
            data = resp.json()
            confirmed = float(data.get("xrpBalance", 0))
            usd = confirmed * await self._get_usd_price("XRP")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("XRP balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_tron(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(f"https://api.trongrid.io/v1/accounts/{address}")
            data = resp.json()
            confirmed = 0.0
            if "data" in data and len(data["data"]) > 0:
                bal_data = data["data"][0].get("balance", 0)
                confirmed = bal_data / 1e6 if bal_data else 0.0
            usd = confirmed * await self._get_usd_price("TRON")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("TRON balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_polygon(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(
                "https://api.polygonscan.com/api",
                params={"module": "account", "action": "balance", "address": address, "tag": "latest"},
            )
            data = resp.json()
            if data.get("status") == "1":
                confirmed = int(data["result"]) / 1e18
            else:
                confirmed = 0.0
            usd = confirmed * await self._get_usd_price("POLYGON")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("POLYGON balance check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_erc20_usdt(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.get(
                "https://api.etherscan.io/api",
                params={
                    "module": "account", "action": "tokenbalance",
                    "contractaddress": ERC20_USDT, "address": address, "tag": "latest",
                },
            )
            data = resp.json()
            if data.get("status") == "1":
                confirmed = int(data["result"]) / 1e6
            else:
                confirmed = 0.0
            usd = confirmed * await self._get_usd_price("USDT")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("ERC-20 USDT check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def _check_trc20_usdt(self, address: str) -> tuple[float, float, float]:
        try:
            resp = await self._client.post(
                "https://api.trongrid.io/v1/accounts",
                json={
                    "address": address,
                    "contract_address": TRC20_USDT,
                },
            )
            data = resp.json()
            confirmed = 0.0
            if "data" in data and len(data["data"]) > 0:
                trc20_data = data["data"][0].get("trc20", [])
                for token in trc20_data:
                    if TRC20_USDT in token:
                        confirmed = int(token[TRC20_USDT]) / 1e6
                        break
            usd = confirmed * await self._get_usd_price("USDT")
            return confirmed, 0.0, usd
        except Exception as e:
            logger.debug("TRC-20 USDT check failed for %s: %s", address, e)
            return 0.0, 0.0, 0.0

    async def query_balance(self, address: str, token: str) -> Balance:
        now = time.time()
        cache_key = f"{token}:{address}"
        if cache_key in self._cache:
            bal, ts = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return bal

        method_map = {
            "BTC": self._check_btc,
            "ETH": self._check_eth,
            "LTC": self._check_ltc,
            "SOL": self._check_sol,
            "BNB": self._check_bnb,
            "XRP": self._check_xrp,
            "TRON": self._check_tron,
            "POLYGON": self._check_polygon,
            "USDT_ERC20": self._check_erc20_usdt,
            "USDT_TRC20": self._check_trc20_usdt,
        }

        checker = method_map.get(token)
        if checker is None:
            return Balance(token=token, confirmed=0.0, pending=0.0)

        confirmed, pending, usd = await checker(address)

        if confirmed > 0 or pending > 0:
            confirmed = round(confirmed, 8)
            pending = round(pending, 8)
            usd = round(usd, 2)

        balance = Balance(token=token, confirmed=confirmed, pending=pending, usd_value=usd)
        self._cache[cache_key] = (balance, now)
        return balance

    async def subscribe_mempool(self, filter_data: EventStream) -> None:
        self._subscriptions.append(filter_data)
        logger.info("Subscribed to mempool events for %s", filter_data.chain)

    async def close(self) -> None:
        await self._client.aclose()
