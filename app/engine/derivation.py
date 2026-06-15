import logging

from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

from app.interfaces.wallet_operator import IWalletOperator, OperationResult
from app.components.config_manager import ConfigManager

logger = logging.getLogger(__name__)

CHAIN_COINS = {
    "BTC": Bip44Coins.BITCOIN,
    "ETH": Bip44Coins.ETHEREUM,
    "LTC": Bip44Coins.LITECOIN,
    "SOL": Bip44Coins.SOLANA,
    "BNB": Bip44Coins.BINANCE_CHAIN,
    "XRP": Bip44Coins.RIPPLE,
    "TRON": Bip44Coins.TRON,
    "POLYGON": Bip44Coins.POLYGON,
}

CHAIN_INDEX = {
    "BTC": 0, "ETH": 60, "LTC": 2, "SOL": 501,
    "BNB": 714, "XRP": 144, "TRON": 195, "POLYGON": 966,
}

OPERATIONS = [
    "derive_btc",
    "derive_eth",
    "derive_ltc",
    "derive_sol",
    "derive_bnb",
    "derive_xrp",
    "derive_tron",
    "derive_polygon",
]

OPERATION_LABELS = {
    "derive_btc": "Derive BTC Address & Key",
    "derive_eth": "Derive ETH Address & Key",
    "derive_ltc": "Derive LTC Address & Key",
    "derive_sol": "Derive SOL Address & Key",
    "derive_bnb": "Derive BNB Address & Key",
    "derive_xrp": "Derive XRP Address & Key",
    "derive_tron": "Derive TRON Address & Key",
    "derive_polygon": "Derive POLYGON Address & Key",
}


class WalletDeriver(IWalletOperator):

    def __init__(self, config: ConfigManager):
        self._config = config

    def name(self) -> str:
        return "Wallet Derivation"

    def description(self) -> str:
        return (
            "Derives cryptocurrency addresses and private keys from "
            "a BIP39 seed phrase across all 8 supported chains. "
            "Live mainnet derivation with no test data."
        )

    def available_operations(self) -> list[str]:
        return list(OPERATIONS)

    def execute(self, operation: str, seed: str) -> OperationResult:
        if operation not in OPERATIONS:
            return OperationResult(
                operation=operation, success=False, wallet_type="software",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": f"Unknown operation: {operation}"},
            )

        chain = operation.split("_", 1)[1].upper()
        if chain == "POLYGON":
            chain = "POLYGON"

        if not seed or len(seed.split()) < 12:
            return OperationResult(
                operation=operation, success=False, wallet_type="software",
                chain=chain, address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": "A valid 12+ word BIP39 seed phrase is required"},
            )

        try:
            coin = CHAIN_COINS.get(chain)
            if coin is None:
                return OperationResult(
                    operation=operation, success=False, wallet_type="software",
                    chain=chain, address="", private_key_hex="",
                    balance_confirmed=0.0, balance_usd=0.0,
                    details={"error": f"Unsupported chain: {chain}"},
                )

            seed_bytes = Bip39SeedGenerator(seed).Generate()
            bip44 = Bip44.FromSeed(seed_bytes, coin)
            node = bip44.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0)
            addr = node.PublicKey().ToAddress()
            priv_key = node.PrivateKey().Raw().ToBytes()
            priv_hex = priv_key.hex()
            path = f"m/44'/{CHAIN_INDEX.get(chain, 0)}'/0'/0/0"

            details = {
                "derivation_path": path,
                "coin": chain,
                "address": addr,
                "private_key_present": bool(priv_hex),
            }

            return OperationResult(
                operation=operation,
                success=True,
                wallet_type="software",
                chain=chain,
                address=addr,
                private_key_hex=priv_hex,
                balance_confirmed=0.0,
                balance_usd=0.0,
                details=details,
            )

        except Exception as e:
            logger.error("Derivation failed for %s: %s", chain, e)
            return OperationResult(
                operation=operation, success=False, wallet_type="software",
                chain=chain, address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )
