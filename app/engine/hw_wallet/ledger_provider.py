import logging
from typing import Optional

logger = logging.getLogger(__name__)


class LedgerProvider:
    @staticmethod
    def is_connected() -> bool:
        try:
            from ledgerblue.comm import getDongle
            dongle = getDongle(False)
            dongle.close()
            return True
        except Exception:
            return False

    @staticmethod
    def get_public_key(path: str = "m/44'/0'/0'/0/0") -> Optional[dict]:
        try:
            from ledgerblue.comm import getDongle
            from ledgereth import accounts
            dongle = getDongle(False)
            acct = accounts.get_account(dongle, path)
            dongle.close()
            return {
                "path": path,
                "address": str(acct.address) if hasattr(acct, "address") else str(acct),
            }
        except Exception as e:
            logger.error("Ledger get_public_key failed: %s", e)
            return None

    @staticmethod
    def sign_tx(path: str, tx_bytes: bytes) -> Optional[bytes]:
        try:
            from ledgerblue.comm import getDongle
            from ledgereth import transactions
            dongle = getDongle(False)
            sig = transactions.sign_transaction(dongle, tx_bytes, path)
            dongle.close()
            return sig
        except Exception as e:
            logger.error("Ledger sign_tx failed: %s", e)
            return None
