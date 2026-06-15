import logging
from typing import Optional

logger = logging.getLogger(__name__)

CHAIN_BIP44_PATHS = {
    "BTC": "m/44'/0'/0'/0/0",
    "ETH": "m/44'/60'/0'/0/0",
    "LTC": "m/44'/2'/0'/0/0",
    "SOL": "m/44'/501'/0'/0/0",
    "BNB": "m/44'/714'/0'/0/0",
    "XRP": "m/44'/144'/0'/0/0",
    "TRON": "m/44'/195'/0'/0/0",
    "POLYGON": "m/44'/966'/0'/0/0",
}

CHAIN_COIN_TYPES = {
    "BTC": 0, "ETH": 60, "LTC": 2, "SOL": 501,
    "BNB": 714, "XRP": 144, "TRON": 195, "POLYGON": 966,
}


class HwDetector:
    def __init__(self):
        self._trezor_provider = None
        self._ledger_provider = None

    def import_trezor(self):
        try:
            from .trezor_provider import TrezorProvider
            self._trezor_provider = TrezorProvider
            return True
        except ImportError as e:
            logger.warning("Trezor not available: %s", e)
            return False

    def import_ledger(self):
        try:
            from .ledger_provider import LedgerProvider
            self._ledger_provider = LedgerProvider
            return True
        except ImportError as e:
            logger.warning("Ledger not available: %s", e)
            return False

    def detect_all(self) -> dict:
        result = {
            "trezor": {"detected": False, "error": None},
            "ledger": {"detected": False, "error": None},
        }

        if not self._trezor_provider:
            self.import_trezor()
        if self._trezor_provider:
            try:
                devices = self._trezor_provider.list_devices()
                result["trezor"]["detected"] = len(devices) > 0
                result["trezor"]["devices"] = [d.to_dict() if hasattr(d, "to_dict") else str(d) for d in devices]
                result["trezor"]["device_count"] = len(devices)
            except Exception as e:
                result["trezor"]["error"] = str(e)

        if not self._ledger_provider:
            self.import_ledger()
        if self._ledger_provider:
            try:
                result["ledger"]["detected"] = self._ledger_provider.is_connected()
            except Exception as e:
                result["ledger"]["error"] = str(e)

        return result

    def get_all_addresses(self) -> dict[str, str]:
        addresses = {}
        for chain, path in CHAIN_BIP44_PATHS.items():
            try:
                key = self.get_public_key(path)
                if key and key.get("address"):
                    addresses[chain] = key["address"]
            except Exception as e:
                logger.debug("HW addr fail for %s: %s", chain, e)
        return addresses

    def get_public_key(self, path: str = "m/44'/0'/0'/0/0") -> Optional[dict]:
        try:
            if self._trezor_provider:
                return self._trezor_provider.get_public_key(path)
            if self._ledger_provider:
                return self._ledger_provider.get_public_key(path)
        except Exception as e:
            logger.error("Failed to get public key: %s", e)
        return None

    def sign_tx(self, path: str, tx_bytes: bytes) -> Optional[bytes]:
        try:
            if self._trezor_provider:
                return self._trezor_provider.sign_tx(path, tx_bytes)
            if self._ledger_provider:
                return self._ledger_provider.sign_tx(path, tx_bytes)
        except Exception as e:
            logger.error("Failed to sign with HW wallet: %s", e)
        return None
