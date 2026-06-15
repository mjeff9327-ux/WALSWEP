import asyncio
import time
import logging

import httpx
from app.interfaces.node_client import INodeClient, Balance, EventStream

logger = logging.getLogger(__name__)


SOL_RPC = "https://api.mainnet-beta.solana.com"


ERC20_USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
TRC20_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class LiveNodeClient(INodeClient):
    def __init__(self, api_keys: dict | None = None, timeout: float = 15.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache: dict[str, tuple[Balance, float]] = {}
        self._cache_ttl = 30.0
        self._price_cache: dict[str, tuple[float, float]] = {}
        self._subscriptions: list[EventStream] = []
        self._api_keys = api_keys or {}

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
            params = {"module": "account", "action": "balance", "address": address, "tag": "latest"}
            if self._api_keys.get("etherscan"):
                params["apikey"] = self._api_keys["etherscan"]
            resp = await self._client.get(
                "https://api.etherscan.io/api",
                params=params,
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
            params = {"module": "account", "action": "balance", "address": address, "tag": "latest"}
            if self._api_keys.get("bscscan"):
                params["apikey"] = self._api_keys["bscscan"]
            resp = await self._client.get(
                "https://api.bscscan.com/api",
                params=params,
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
            params = {"module": "account", "action": "balance", "address": address, "tag": "latest"}
            if self._api_keys.get("polygonscan"):
                params["apikey"] = self._api_keys["polygonscan"]
            resp = await self._client.get(
                "https://api.polygonscan.com/api",
                params=params,
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
            params = {
                "module": "account", "action": "tokenbalance",
                "contractaddress": ERC20_USDT, "address": address, "tag": "latest",
            }
            if self._api_keys.get("etherscan"):
                params["apikey"] = self._api_keys["etherscan"]
            resp = await self._client.get(
                "https://api.etherscan.io/api",
                params=params,
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

    async def query_balances(self, address: str, chains: list[str]) -> dict[str, Balance]:
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

        async def _check_one(chain: str) -> tuple[str, Balance]:
            cache_key = f"{chain}:{address}"
            now = time.time()
            if cache_key in self._cache:
                bal, ts = self._cache[cache_key]
                if now - ts < self._cache_ttl:
                    return chain, bal

            checker = method_map.get(chain)
            if checker is None:
                return chain, Balance(token=chain, confirmed=0.0, pending=0.0)

            confirmed, pending, usd = await checker(address)
            if confirmed > 0 or pending > 0:
                confirmed = round(confirmed, 8)
                pending = round(pending, 8)
                usd = round(usd, 2)

            balance = Balance(token=chain, confirmed=confirmed, pending=pending, usd_value=usd)
            self._cache[cache_key] = (balance, now)
            return chain, balance

        results = await asyncio.gather(*[_check_one(c) for c in chains], return_exceptions=True)
        out = {}
        for r in results:
            if isinstance(r, Exception):
                continue
            chain, bal = r
            out[chain] = bal
        return out

    async def query_balances_batch(self, chain: str, addresses: list[str]) -> dict[str, Balance]:
        if not addresses:
            return {}

        if chain in ("ETH",) and self._api_keys.get("etherscan"):
            return await self._batch_etherscan("etherscan", addresses, "ETH")
        if chain in ("BNB",) and self._api_keys.get("bscscan"):
            return await self._batch_etherscan("bscscan", addresses, "BNB")
        if chain in ("POLYGON",) and self._api_keys.get("polygonscan"):
            return await self._batch_etherscan("polygonscan", addresses, "POLYGON")
        if chain == "SOL":
            return await self._batch_solana(addresses)

        results = {}
        tasks = {a: self.query_balance(a, chain) for a in addresses}
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for addr, res in zip(tasks.keys(), gathered):
            if isinstance(res, Exception):
                results[addr] = Balance(token=chain, confirmed=0.0, pending=0.0)
            else:
                results[addr] = res
        return results

    async def _batch_etherscan(self, api_key_name: str, addresses: list[str], chain: str) -> dict[str, Balance]:
        base_urls = {
            "etherscan": "https://api.etherscan.io/api",
            "bscscan": "https://api.bscscan.com/api",
            "polygonscan": "https://api.polygonscan.com/api",
        }
        url = base_urls.get(api_key_name)
        if not url:
            return {}

        results = {}
        for i in range(0, len(addresses), 20):
            batch = addresses[i:i + 20]
            params = {
                "module": "account", "action": "balancemulti",
                "address": ",".join(batch), "tag": "latest",
            }
            if self._api_keys.get(api_key_name):
                params["apikey"] = self._api_keys[api_key_name]
            try:
                resp = await self._client.get(url, params=params)
                data = resp.json()
                if data.get("status") == "1":
                    for entry in data.get("result", []):
                        addr = entry.get("account", "")
                        bal = int(entry.get("balance", 0)) / 1e18
                        usd = bal * await self._get_usd_price(chain)
                        results[addr] = Balance(token=chain, confirmed=round(bal, 8), pending=0.0, usd_value=round(usd, 2))
                else:
                    for addr in batch:
                        bal = await self.query_balance(addr, chain)
                        results[addr] = bal
            except Exception:
                for addr in batch:
                    bal = await self.query_balance(addr, chain)
                    results[addr] = bal
        return results

    async def _batch_solana(self, addresses: list[str]) -> dict[str, Balance]:
        results = {}
        for i in range(0, len(addresses), 100):
            batch = addresses[i:i + 100]
            try:
                resp = await self._client.post(
                    SOL_RPC,
                    json={"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts", "params": [batch]},
                )
                data = resp.json()
                account_infos = data.get("result", {}).get("value", [])
                for addr, info in zip(batch, account_infos):
                    if info is not None:
                        lamports = info.get("lamports", 0)
                        bal = lamports / 1e9
                        usd = bal * await self._get_usd_price("SOL")
                        results[addr] = Balance(token="SOL", confirmed=round(bal, 8), pending=0.0, usd_value=round(usd, 2))
                    else:
                        results[addr] = Balance(token="SOL", confirmed=0.0, pending=0.0)
            except Exception:
                for addr in batch:
                    bal = await self.query_balance(addr, "SOL")
                    results[addr] = bal
        return results

    async def subscribe_mempool(self, filter_data: EventStream) -> None:
        self._subscriptions.append(filter_data)
        logger.info("Subscribed to mempool events for %s", filter_data.chain)

    async def close(self) -> None:
        await self._client.aclose()
