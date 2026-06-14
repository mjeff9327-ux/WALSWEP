from mnemonic import Mnemonic
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
from app.interfaces.solver import ISolver, DerivedAddressSet


CHAIN_COINS = [
    ("BTC", Bip44Coins.BITCOIN),
    ("ETH", Bip44Coins.ETHEREUM),
    ("LTC", Bip44Coins.LITECOIN),
    ("SOL", Bip44Coins.SOLANA),
    ("BNB", Bip44Coins.BINANCE_CHAIN),
    ("XRP", Bip44Coins.RIPPLE),
    ("TRON", Bip44Coins.TRON),
    ("POLYGON", Bip44Coins.POLYGON),
]


class Bip39Solver(ISolver):
    def __init__(self):
        self._mnemo = Mnemonic("english")

    def generate_mnemonic(self) -> str:
        return self._mnemo.generate(strength=128)

    def solve(self, input_pattern: str) -> DerivedAddressSet:
        mnemonic_phrase = input_pattern.strip()
        try:
            seed = Bip39SeedGenerator(mnemonic_phrase).Generate()
        except Exception:
            return DerivedAddressSet(pattern=input_pattern, synthetic=False)

        addresses = []
        for chain, coin in CHAIN_COINS:
            try:
                bip44 = Bip44.FromSeed(seed, coin)
                addr = bip44.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().ToAddress()
                addresses.append({"chain": chain, "address": addr})
            except Exception:
                addresses.append({"chain": chain, "address": ""})
        return DerivedAddressSet(pattern=input_pattern, addresses=addresses, synthetic=False)
