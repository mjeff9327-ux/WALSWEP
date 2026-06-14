import logging
from typing import Optional

import httpx

from app.interfaces.node_client import INodeClient, Balance
from app.components.config_manager import ConfigManager

logger = logging.getLogger(__name__)

ERC20_CONTRACTS: dict[str, list[dict[str, str]]] = {
    "ETH": [
        {"symbol": "USDT", "contract": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6},
        {"symbol": "USDC", "contract": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6},
        {"symbol": "DAI", "contract": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18},
        {"symbol": "WBTC", "contract": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "decimals": 8},
    ],
    "BNB": [
        {"symbol": "USDT", "contract": "0x55d398326f99059fF775485246999027B3197955", "decimals": 18},
        {"symbol": "USDC", "contract": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "decimals": 18},
        {"symbol": "BUSD", "contract": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56", "decimals": 18},
    ],
    "POLYGON": [
        {"symbol": "USDT", "contract": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", "decimals": 6},
        {"symbol": "USDC", "contract": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "decimals": 6},
        {"symbol": "DAI", "contract": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", "decimals": 18},
    ],
}

TRC20_CONTRACTS: list[dict[str, str]] = [
    {"symbol": "USDT", "contract": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "decimals": 6},
    {"symbol": "USDC", "contract": "TEkxiTehnzSmL2nW48w9WjC2Q8XKEGwMx8", "decimals": 6},
    {"symbol": "USDD", "contract": "TPYmHEhy5n8TCEfYGqW2rPxsghSfzghPDn", "decimals": 18},
]


def _explorer_api_for(chain: str, config: Optional[ConfigManager] = None) -> tuple[str, str]:
    base = {
        "ETH": "https://api.etherscan.io/api",
        "BNB": "https://api.bscscan.com/api",
        "POLYGON": "https://api.polygonscan.com/api",
    }
    url = base.get(chain, "")
    if not url:
        return ("", "")
    if config is None:
        return (url, "")
    key_map = {
        "ETH": config.get("api_keys", "etherscan", ""),
        "BNB": config.get("api_keys", "bscscan", ""),
        "POLYGON": config.get("api_keys", "polygonscan", ""),
    }
    api_key = key_map.get(chain, "")
    if not api_key:
        logger.warning("No API key configured for %s — token balance queries may be rate-limited", chain)
    return (url, api_key)


class TokenScanner:
    def __init__(self, node_client: INodeClient, config: Optional[ConfigManager] = None):
        self._node = node_client
        self._config = config
        self._client = httpx.AsyncClient(timeout=10)

    async def scan_erc20(self, address: str, chain: str) -> list[dict]:
        contracts = ERC20_CONTRACTS.get(chain, [])
        if not contracts:
            return []

        base_url, api_key = _explorer_api_for(chain, self._config)
        results = []

        for token in contracts:
            symbol = token["symbol"]
            contract = token["contract"]
            decimals = token["decimals"]

            try:
                if base_url and ("etherscan" in base_url or "bscscan" in base_url or "polygonscan" in base_url):
                    params = {
                        "module": "account",
                        "action": "tokenbalance",
                        "contractaddress": contract,
                        "address": address,
                        "tag": "latest",
                    }
                    if api_key:
                        params["apikey"] = api_key
                    resp = await self._client.get(base_url, params=params)
                    data = resp.json()
                    if data.get("status") == "1":
                        raw = int(data["result"])
                        balance = raw / (10 ** decimals)
                    else:
                        balance = 0.0
                else:
                    continue

                if balance > 0:
                    usd_price = await self._node.query_balance(address, chain)
                    usd_value = balance * (usd_price.usd_value or 0) if usd_price.confirmed > 0 else 0
                    results.append({
                        "symbol": symbol,
                        "contract": contract,
                        "balance": round(balance, 8),
                        "chain": chain,
                        "usd_value": round(usd_value, 2),
                    })
            except Exception as e:
                logger.debug("Token scan failed for %s %s: %s", chain, symbol, e)

        return results

    async def scan_trc20(self, address: str) -> list[dict]:
        results = []
        for token in TRC20_CONTRACTS:
            symbol = token["symbol"]
            contract = token["contract"]
            decimals = token["decimals"]
            try:
                resp = await self._client.get(
                    f"https://api.trongrid.io/v1/accounts/{address}",
                )
                data = resp.json()
                balance = 0.0
                if "data" in data and len(data["data"]) > 0:
                    trc20_list = data["data"][0].get("trc20", [])
                    for entry in trc20_list:
                        if contract in entry:
                            raw = int(entry[contract])
                            balance = raw / (10 ** decimals)
                            break
                if balance > 0:
                    usd_price = await self._node.query_balance(address, "TRON")
                    usd_value = balance * (usd_price.usd_value or 0) if usd_price.confirmed > 0 else 0
                    results.append({
                        "symbol": symbol,
                        "contract": contract,
                        "balance": round(balance, 8),
                        "chain": "TRON",
                        "usd_value": round(usd_value, 2),
                    })
            except Exception as e:
                logger.debug("TRC-20 scan failed for %s %s: %s", address, symbol, e)

        return results

    async def scan(self, address: str, chain: str) -> list[dict]:
        if chain in ERC20_CONTRACTS:
            return await self.scan_erc20(address, chain)
        elif chain == "TRON":
            return await self.scan_trc20(address)
        return []

    async def close(self) -> None:
        await self._client.aclose()
