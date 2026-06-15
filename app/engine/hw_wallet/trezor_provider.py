import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TrezorProvider:
    @staticmethod
    def list_devices():
        try:
            from trezorlib.transport import enumerate_devices
            return enumerate_devices()
        except Exception as e:
            logger.error("Failed to enumerate Trezor devices: %s", e)
            return []

    @staticmethod
    def get_client():
        try:
            from trezorlib.client import TrezorClient
            from trezorlib.transport import enumerate_devices
            devices = enumerate_devices()
            if not devices:
                return None
            return TrezorClient(devices[0])
        except Exception as e:
            logger.error("Failed to create Trezor client: %s", e)
            return None

    @staticmethod
    def get_public_key(path: str = "m/44'/0'/0'/0/0") -> Optional[dict]:
        try:
            from trezorlib import btc
            client = TrezorProvider.get_client()
            if not client:
                return None
            key = btc.get_public_node(client, [path])
            client.close()
            return {
                "path": path,
                "xpub": key.xpub if hasattr(key, "xpub") else str(key),
                "address": key.address if hasattr(key, "address") else None,
            }
        except Exception as e:
            logger.error("Trezor get_public_key failed: %s", e)
            return None

    @staticmethod
    def sign_tx(path: str, tx_bytes: bytes) -> Optional[bytes]:
        try:
            from trezorlib import btc
            client = TrezorProvider.get_client()
            if not client:
                return None
            sig = btc.sign_tx(client, [path], [tx_bytes])
            client.close()
            return sig
        except Exception as e:
            logger.error("Trezor sign_tx failed: %s", e)
            return None
