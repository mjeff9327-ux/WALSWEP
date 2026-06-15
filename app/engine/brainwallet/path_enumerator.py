import logging
from typing import Optional

from bip_utils import Bip39SeedGenerator, Bip44, Bip49, Bip84, Bip44Coins, Bip49Coins, Bip84Coins, Bip44Changes

logger = logging.getLogger(__name__)

PURPOSE_VARIANTS = [
    (44, "BIP44 (Legacy)"),
    (49, "BIP49 (SegWit)"),
    (84, "BIP84 (Native SegWit)"),
]

CHAIN_COINS = [
    ("BTC", Bip44Coins.BITCOIN, 0),
    ("ETH", Bip44Coins.ETHEREUM, 60),
    ("LTC", Bip44Coins.LITECOIN, 2),
    ("SOL", Bip44Coins.SOLANA, 501),
    ("BNB", Bip44Coins.BINANCE_CHAIN, 714),
    ("XRP", Bip44Coins.RIPPLE, 144),
    ("TRON", Bip44Coins.TRON, 195),
    ("POLYGON", Bip44Coins.POLYGON, 966),
]

BIP_CLS_MAP: dict[int, tuple] = {
    44: (Bip44, Bip44Coins),
    49: (Bip49, Bip49Coins),
    84: (Bip84, Bip84Coins),
}


class PathEnumerator:
    def __init__(self, gap_limit: int = 20, max_account: int = 5):
        self._gap_limit = gap_limit
        self._max_account = max_account

    def enumerate(self, mnemonic: str) -> list[dict]:
        results = []
        try:
            seed = Bip39SeedGenerator(mnemonic).Generate()
        except Exception as e:
            logger.error("Invalid mnemonic: %s", e)
            return results

        for chain_name, coin, coin_type in CHAIN_COINS:
            try:
                results.extend(self._enumerate_chain(seed, chain_name, coin, coin_type))
            except Exception as e:
                logger.debug("Path enum failed for %s: %s", chain_name, e)

        return results

    def _get_bip_ctx(self, seed: bytes, purpose: int, coin_for_purpose) -> object:
        bip_cls, _ = BIP_CLS_MAP[purpose]
        return bip_cls.FromSeed(seed, coin_for_purpose)

    def _enumerate_chain(self, seed: bytes, chain_name: str, coin, coin_type: int) -> list[dict]:
        found = []

        _ctx_cache = {}

        for purpose, purpose_label in PURPOSE_VARIANTS:
            bip_cls, coin_enum = BIP_CLS_MAP[purpose]
            coin_for_purpose = None
            try:
                if coin_enum is Bip44Coins:
                    coin_for_purpose = coin
                else:
                    coin_name = chain_name.upper()
                    if coin_name == "BTC":
                        coin_for_purpose = coin_enum.BITCOIN
                    elif coin_name == "LTC":
                        coin_for_purpose = coin_enum.LITECOIN
                    elif coin_name == "DASH":
                        coin_for_purpose = coin_enum.DASH
                    elif coin_name == "DOGECOIN":
                        coin_for_purpose = coin_enum.DOGECOIN
                    else:
                        continue
            except Exception:
                continue

            if coin_for_purpose is None:
                continue

            cache_key = (purpose, coin_for_purpose)
            if cache_key not in _ctx_cache:
                _ctx_cache[cache_key] = self._get_bip_ctx(seed, purpose, coin_for_purpose)
            bip_ctx = _ctx_cache[cache_key]

            try:
                addr = bip_ctx.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().ToAddress()
                found.append({
                    "chain": chain_name,
                    "path": f"m/{purpose}'/{coin_type}'/0'/0/0",
                    "address": addr,
                    "purpose": purpose_label,
                    "account": 0,
                    "change": 0,
                    "index": 0,
                })
            except Exception:
                pass

            for account in range(1, self._max_account + 1):
                try:
                    addr = bip_ctx.Purpose().Coin().Account(account).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().ToAddress()
                    found.append({
                        "chain": chain_name,
                        "path": f"m/{purpose}'/{coin_type}'/{account}'/0/0",
                        "address": addr,
                        "purpose": purpose_label,
                        "account": account,
                        "change": 0,
                        "index": 0,
                    })
                except Exception:
                    pass

            for idx in range(1, self._gap_limit + 1):
                try:
                    addr = bip_ctx.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(idx).PublicKey().ToAddress()
                    found.append({
                        "chain": chain_name,
                        "path": f"m/{purpose}'/{coin_type}'/0'/0/{idx}",
                        "address": addr,
                        "purpose": purpose_label,
                        "account": 0,
                        "change": 0,
                        "index": idx,
                    })
                except Exception:
                    pass

            try:
                addr = bip_ctx.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_INT).AddressIndex(0).PublicKey().ToAddress()
                found.append({
                    "chain": chain_name,
                    "path": f"m/{purpose}'/{coin_type}'/0'/1/0",
                    "address": addr,
                    "purpose": purpose_label,
                    "account": 0,
                    "change": 1,
                    "index": 0,
                })
            except Exception:
                pass

        return found
