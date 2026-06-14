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

    def sign_transaction(self, unsigned_tx: UnsignedTx) -> SignedTx:
        priv_key_bytes = self._derive_private_key(unsigned_tx.seed_label, unsigned_tx.chain)
        if not priv_key_bytes:
            logger.error("No private key available for %s", unsigned_tx.chain)
            return SignedTx(raw="", tx_id="", chain=unsigned_tx.chain)

        try:
            msg = f"{unsigned_tx.to}:{unsigned_tx.value}:{unsigned_tx.token}:{unsigned_tx.chain}".encode()

            if unsigned_tx.chain in SECP256K1_CHAINS:
                secp_key = Secp256k1Key.from_hex(priv_key_bytes.hex())
                raw_sig = secp_key.sign(msg).hex()
            elif unsigned_tx.chain in ED25519_CHAINS:
                ed_key = nacl.signing.SigningKey(priv_key_bytes, encoder=nacl.encoding.RawEncoder)
                signed = ed_key.sign(msg)
                raw_sig = signed.signature.hex()
            else:
                logger.error("No signing algorithm for chain %s", unsigned_tx.chain)
                return SignedTx(raw="", tx_id="", chain=unsigned_tx.chain)

            tx_id = hashlib.sha256(raw_sig.encode()).hexdigest()[:32]
            return SignedTx(raw=raw_sig, tx_id=tx_id, chain=unsigned_tx.chain)

        except Exception as e:
            logger.error("Real signing failed for %s: %s", unsigned_tx.chain, e)
            return SignedTx(raw="", tx_id="", chain=unsigned_tx.chain)

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
