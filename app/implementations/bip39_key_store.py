import hashlib
import logging

from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
from coincurve import PrivateKey as Secp256k1Key
import nacl.signing
import nacl.encoding

from app.interfaces.key_store import IKeyStore, Address, UnsignedTx, SignedTx

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

SECP256K1_CHAINS = {"BTC", "ETH", "LTC", "BNB", "XRP", "TRON", "POLYGON"}
ED25519_CHAINS = {"SOL"}
CHAIN_INDEX = {
    "BTC": 0, "ETH": 60, "LTC": 2, "SOL": 501,
    "BNB": 714, "XRP": 144, "TRON": 195, "POLYGON": 966,
}


class Bip39KeyStore(IKeyStore):
    def __init__(self):
        self._cache: dict[str, Address] = {}

    def derive_address(self, seed_label: str, chain: str) -> Address:
        cache_key = f"{seed_label}:{chain}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        coin = CHAIN_COINS.get(chain)
        if coin is None:
            raise ValueError(f"Unsupported chain: {chain}")

        try:
            seed = Bip39SeedGenerator(seed_label).Generate()
            bip44 = Bip44.FromSeed(seed, coin)
            addr = bip44.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().ToAddress()
            path = f"m/44'/{CHAIN_INDEX.get(chain, 0)}'/0'/0/0"
        except Exception as e:
            logger.error("Derivation failed for %s on %s: %s", seed_label[:20], chain, e)
            addr = ""
            path = ""

        address = Address(chain=chain, address=addr, path=path)
        self._cache[cache_key] = address
        return address

    async def sign_transaction(self, unsigned_tx: UnsignedTx) -> SignedTx:
        from app.implementations.transaction_signer import TransactionSigner
        signer = TransactionSigner(unsigned_tx.seed_label)
        result = await signer.sign(unsigned_tx)
        await signer.close()
        return result

    def _derive_private_key(self, seed_label: str, chain: str) -> bytes:
        coin = CHAIN_COINS.get(chain)
        if not coin or not seed_label:
            return b""
        try:
            seed = Bip39SeedGenerator(seed_label).Generate()
            bip44 = Bip44.FromSeed(seed, coin)
            return bip44.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PrivateKey().Raw().ToBytes()
        except Exception as e:
            logger.error("Failed to derive private key for %s: %s", chain, e)
            return b""
