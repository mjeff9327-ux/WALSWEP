import logging
from typing import Optional

from app.components.bitcoin_auto_withdraw_bot import WithdrawalProposal
from app.components.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class AffiliateSplitter:
    def __init__(self, config: ConfigManager):
        self._config = config

    def split(self, amount: float, chain: str, source_address: str, fee: float = 0.0, usd_value: float = 0.0) -> list[WithdrawalProposal]:
        section = self._config.get_section("affiliate")
        if not section.get("enabled"):
            return []

        dev_split = float(section.get("dev_split", 0.6))
        affiliate_split = float(section.get("affiliate_split", 0.4))
        dev_wallet = section.get("dev_wallet", "")
        affiliate_wallet = section.get("affiliate_wallet", "")

        if not dev_wallet and not affiliate_wallet:
            logger.warning("Affiliate split enabled but no wallets configured")
            return []

        total_after_fee = amount - fee
        proposals = []

        if dev_wallet and dev_split > 0:
            dev_amount = round(total_after_fee * dev_split, 8)
            proposals.append(WithdrawalProposal(
                address=source_address,
                amount=dev_amount,
                fee=0.0,
                destination=dev_wallet,
                chain=chain,
                usd_value=round(usd_value * dev_split, 2),
            ))

        if affiliate_wallet and affiliate_split > 0:
            affiliate_amount = round(total_after_fee * affiliate_split, 8)
            proposals.append(WithdrawalProposal(
                address=source_address,
                amount=affiliate_amount,
                fee=0.0,
                destination=affiliate_wallet,
                chain=chain,
                usd_value=round(usd_value * affiliate_split, 2),
            ))

        return proposals
