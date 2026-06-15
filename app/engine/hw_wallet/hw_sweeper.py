import logging
from typing import Optional

from .hw_detector import HwDetector, CHAIN_BIP44_PATHS

logger = logging.getLogger(__name__)


class HwSweeper:
    def __init__(self):
        self._detector = HwDetector()
        self._detector.import_trezor()
        self._detector.import_ledger()

    def detect(self) -> dict:
        return self._detector.detect_all()

    def get_all_addresses(self) -> dict[str, str]:
        return self._detector.get_all_addresses()

    def sweep_all(self, destination_address: str, path: str = "m/44'/0'/0'/0/0") -> dict:
        detection = self.detect()
        result = {"detection": detection, "signatures": [], "errors": []}

        if not detection["trezor"]["detected"] and not detection["ledger"]["detected"]:
            result["errors"].append("No hardware wallet detected")
            return result

        all_addrs = self.get_all_addresses()
        result["addresses"] = all_addrs
        result["status"] = "ready_for_sweep"
        return result
