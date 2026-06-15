import asyncio
import hashlib
import logging
import struct
import time
from typing import Optional

import httpx
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
from coincurve import PrivateKey as Secp256k1Key
from Crypto.Hash import keccak
import nacl.signing
import nacl.encoding
import rlp
from base58 import b58decode, b58encode

from app.interfaces.transaction_signer import ITransactionSigner
from app.interfaces.key_store import UnsignedTx, SignedTx

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

EVM_CHAINS = {"ETH": 1, "BNB": 56, "POLYGON": 137}


def _bech32_decode(hrp: str, addr: str):
    chr_map = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    if addr.lower() != addr and addr.upper() != addr:
        return None, None
    addr_lower = addr.lower()
    pos = addr_lower.rfind("1")
    if pos < 1 or pos + 7 > len(addr_lower) or len(addr_lower) > 90:
        return None, None
    if not all(c in chr_map for c in addr_lower[pos + 1:]):
        return None, None
    actual_hrp = addr_lower[:pos]
    if actual_hrp != hrp:
        return None, None
    data_str = addr_lower[pos + 1:]
    data = [chr_map.index(c) for c in data_str]
    polymod = 1
    for v in [ord(c) & 0x1F for c in actual_hrp] + [0] + data:
        b = polymod >> 25
        polymod = ((polymod & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (b >> i) & 1:
                polymod ^= [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3][i]
    if polymod != 1:
        return None, None
    converted = []
    acc = 0
    bits = 0
    for v in data[:-6]:
        acc = (acc << 5) | v
        bits += 5
        if bits >= 8:
            bits -= 8
            converted.append((acc >> bits) & 0xFF)
    return actual_hrp, converted


def _rlp_encode_signed_evm(nonce, gas_price, gas_limit, to, value, data, v, r, s):
    return rlp.encode([nonce, gas_price, gas_limit, to, value, data, v, r, s])


def _varint(i):
    if i < 0xFD:
        return struct.pack("B", i)
    elif i <= 0xFFFF:
        return struct.pack("<BH", 0xFD, i)
    elif i <= 0xFFFFFFFF:
        return struct.pack("<BI", 0xFE, i)
    else:
        return struct.pack("<BQ", 0xFF, i)


def _keccak256(data: bytes) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def _bitcoin_address_to_script(addr: str) -> bytes:
    if addr.startswith("1"):
        decoded = b58decode(addr)
        pubkey_hash = decoded[1:21]
        return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"
    elif addr.startswith("3"):
        decoded = b58decode(addr)
        script_hash = decoded[1:21]
        return b"\xa9\x14" + script_hash + b"\x87"
    elif addr.startswith("bc1"):
        _, data = _bech32_decode("bc", addr)
        if data and len(data) > 0:
            wit_ver = data[0]
            wit_prog = bytes(data[1:])
            if wit_ver == 0 and len(wit_prog) == 20:
                return b"\x00\x14" + wit_prog
            elif wit_ver == 0 and len(wit_prog) == 32:
                return b"\x00\x20" + wit_prog
    return b""


def _btc_utxo_to_input(txid: str, vout: int, script_sig: bytes, sequence: int = 0xFFFFFFFF) -> bytes:
    txid_bytes = bytes.fromhex(txid)[::-1]
    out = txid_bytes
    out += struct.pack("<I", vout)
    out += _varint(len(script_sig))
    out += script_sig
    out += struct.pack("<I", sequence)
    return out


def _btc_output(amount_sat: int, script_pubkey: bytes) -> bytes:
    out = struct.pack("<Q", amount_sat)
    out += _varint(len(script_pubkey))
    out += script_pubkey
    return out


def _btc_legacy_sighash(tx_version: bytes, inputs: list[dict], outputs: bytes, input_index: int, script_pubkey: bytes, locktime: bytes, sighash_type: int = 0x01) -> bytes:
    num_inputs = _varint(len(inputs))
    all_inputs = b""
    for i, inp in enumerate(inputs):
        raw_inp = bytes.fromhex(inp["txid"])[::-1] + struct.pack("<I", inp["vout"])
        if i == input_index:
            raw_inp += _varint(len(script_pubkey)) + script_pubkey
        else:
            raw_inp += _varint(0)
        raw_inp += struct.pack("<I", 0xFFFFFFFF)
        all_inputs += raw_inp
    preimage = tx_version + num_inputs + all_inputs + _varint(1) + outputs + locktime + struct.pack("<I", sighash_type)
    return hashlib.sha256(hashlib.sha256(preimage).digest()).digest()


def _btc_segwit_sighash(tx_version: bytes, inputs: list[dict], outputs: bytes, input_index: int, script_code: bytes, amount_sat: int, locktime: bytes, sighash_type: int = 0x01) -> bytes:
    hash_prevouts = hashlib.sha256(hashlib.sha256(b"".join(
        bytes.fromhex(inp["txid"])[::-1] + struct.pack("<I", inp["vout"]) for inp in inputs
    ).digest()).digest())
    hash_sequence = hashlib.sha256(hashlib.sha256(b"".join(
        struct.pack("<I", 0xFFFFFFFF) for _ in inputs
    ).digest()).digest())
    hash_outputs = hashlib.sha256(hashlib.sha256(outputs).digest()).digest()
    outpoint = bytes.fromhex(inputs[input_index]["txid"])[::-1] + struct.pack("<I", inputs[input_index]["vout"])
    preimage = (
        tx_version + hash_prevouts + hash_sequence + outpoint
        + _varint(len(script_code)) + script_code + struct.pack("<Q", amount_sat)
        + struct.pack("<I", 0xFFFFFFFF) + hash_outputs + locktime
        + struct.pack("<I", sighash_type)
    )
    return hashlib.sha256(hashlib.sha256(preimage).digest()).digest()


class TransactionSigner(ITransactionSigner):
    def __init__(self, mnemonic: str = ""):
        self._mnemonic = mnemonic
        self._private_keys: dict[str, bytes] = {}
        self._http = httpx.AsyncClient(timeout=30)

    def set_mnemonic(self, mnemonic: str) -> None:
        self._mnemonic = mnemonic
        self._private_keys.clear()

    def _get_private_key(self, chain: str) -> bytes:
        if chain in self._private_keys:
            return self._private_keys[chain]
        coin = CHAIN_COINS.get(chain)
        if not coin or not self._mnemonic:
            return b""
        try:
            seed = Bip39SeedGenerator(self._mnemonic).Generate()
            bip44 = Bip44.FromSeed(seed, coin)
            priv_key = bip44.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PrivateKey().Raw().ToBytes()
            self._private_keys[chain] = priv_key
            return priv_key
        except Exception as e:
            logger.error("Failed to derive private key for %s: %s", chain, e)
            return b""

    async def _get_nonce(self, address: str, chain: str) -> int:
        chain_id = EVM_CHAINS.get(chain)
        if not chain_id:
            return 0
        explorer = {
            1: "https://api.etherscan.io/api",
            56: "https://api.bscscan.com/api",
            137: "https://api.polygonscan.com/api",
        }[chain_id]
        for attempt in range(3):
            try:
                resp = await self._http.get(
                    explorer,
                    params={
                        "module": "proxy",
                        "action": "eth_getTransactionCount",
                        "address": address,
                        "tag": "latest",
                    },
                )
                data = resp.json()
                if data.get("status") == "1":
                    return int(data["result"], 16)
            except Exception as e:
                logger.debug("Nonce fetch attempt %d failed for %s: %s", attempt, address, e)
            if attempt < 2:
                await asyncio.sleep(1 + attempt)
        try:
            fallback_rpc = {1: "https://eth.llamarpc.com", 56: "https://bsc-dataseed1.binance.org", 137: "https://polygon-rpc.com"}[chain_id]
            resp = await self._http.post(fallback_rpc, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionCount", "params": [address, "latest"]})
            data = resp.json()
            if "result" in data:
                return int(data["result"], 16)
        except Exception:
            pass
        return 0

    async def _get_gas_price(self, chain: str) -> int:
        chain_id = EVM_CHAINS.get(chain)
        if not chain_id:
            return 50_000_000_000
        explorer = {
            1: "https://api.etherscan.io/api",
            56: "https://api.bscscan.com/api",
            137: "https://api.polygonscan.com/api",
        }[chain_id]
        for attempt in range(3):
            try:
                resp = await self._http.get(
                    explorer,
                    params={"module": "proxy", "action": "eth_gasPrice"},
                )
                data = resp.json()
                if data.get("status") == "1":
                    return int(data["result"], 16)
            except Exception as e:
                logger.debug("Gas price attempt %d failed for %s: %s", attempt, chain, e)
            if attempt < 2:
                await asyncio.sleep(1 + attempt)
        try:
            fallback_rpc = {1: "https://eth.llamarpc.com", 56: "https://bsc-dataseed1.binance.org", 137: "https://polygon-rpc.com"}[chain_id]
            resp = await self._http.post(fallback_rpc, json={"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice"})
            data = resp.json()
            if "result" in data:
                return int(data["result"], 16)
        except Exception:
            pass
        return 50_000_000_000

    async def _build_evm_tx(self, unsigned_tx: UnsignedTx) -> SignedTx:
        chain_id = EVM_CHAINS.get(unsigned_tx.chain)
        if not chain_id:
            return SignedTx(raw="", tx_id="", chain=unsigned_tx.chain, broadcast_error=f"Unknown EVM chain: {unsigned_tx.chain}")

        priv_key_bytes = self._get_private_key(unsigned_tx.chain)
        if not priv_key_bytes:
            return SignedTx(raw="", tx_id="", chain=unsigned_tx.chain, broadcast_error="No private key")

        nonce = unsigned_tx.nonce or await self._get_nonce(unsigned_tx.from_address, unsigned_tx.chain)
        gas_price = unsigned_tx.gas_price or await self._get_gas_price(unsigned_tx.chain)
        gas_limit = unsigned_tx.gas_limit or 21000
        wei_value = int(unsigned_tx.value * 1e18)

        to_bytes = bytes.fromhex(unsigned_tx.to[2:] if unsigned_tx.to.startswith("0x") else unsigned_tx.to)

        tx_data = {
            "nonce": nonce,
            "gasPrice": hex(gas_price),
            "gasLimit": hex(gas_limit),
            "to": unsigned_tx.to,
            "value": hex(wei_value),
            "data": "0x",
            "chainId": chain_id,
        }

        # EIP-155 signing: encode [nonce, gasPrice, gasLimit, to, value, data, chainId, 0, 0]
        rlp_unsigned = rlp.encode([
            nonce,
            gas_price,
            gas_limit,
            to_bytes,
            wei_value,
            b"",
            chain_id,
            0,
            0,
        ])

        tx_hash = _keccak256(rlp_unsigned)
        priv_key = Secp256k1Key.from_bytes(priv_key_bytes)
        sig = priv_key.ecdsa_sign(tx_hash, recoverable=True)

        v_raw, r_bytes, s_bytes = priv_key.ecdsa_recoverable_convert(sig)
        r_int = int.from_bytes(r_bytes, "big")
        s_int = int.from_bytes(s_bytes, "big")
        v = chain_id * 2 + 35 + v_raw

        rlp_signed = _rlp_encode_signed_evm(
            nonce, gas_price, gas_limit, to_bytes, wei_value, b"",
            v, r_int, s_int,
        )

        raw_hex = "0x" + rlp_signed.hex()
        tx_id = _keccak256(rlp_signed)[:32].hex()

        return SignedTx(raw=raw_hex, tx_id=tx_id, chain=unsigned_tx.chain)

    async def _build_btc_tx(self, unsigned_tx: UnsignedTx) -> SignedTx:
        priv_key_bytes = self._get_private_key("BTC")
        if not priv_key_bytes:
            return SignedTx(raw="", tx_id="", chain="BTC", broadcast_error="No private key")

        from bip_utils import Bip44Coins, Bip44, Bip44Changes, Bip39SeedGenerator
        seed = Bip39SeedGenerator(self._mnemonic).Generate()
        bip44_btc = Bip44.FromSeed(seed, Bip44Coins.BITCOIN)
        pub_key_bytes = bip44_btc.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().Raw().ToBytes()

        try:
            utxos = unsigned_tx.utxos
            if not utxos:
                resp = await self._http.get(f"https://blockstream.info/api/address/{unsigned_tx.from_address}/utxo")
                utxos = resp.json()

            if not utxos:
                return SignedTx(raw="", tx_id="", chain="BTC", broadcast_error="No UTXOs available")

            dest_script = _bitcoin_address_to_script(unsigned_tx.to)
            change_script = _bitcoin_address_to_script(unsigned_tx.from_address)

            amount_sat = int(unsigned_tx.value * 1e8)
            fee_sat = 5000
            total_input = 0
            selected_utxos = []
            for utxo in utxos:
                total_input += utxo.get("value", 0)
                selected_utxos.append(utxo)
                if total_input >= amount_sat + fee_sat:
                    break

            if total_input < amount_sat + fee_sat:
                return SignedTx(raw="", tx_id="", chain="BTC", broadcast_error="Insufficient funds including fee")

            inputs_list = [{"txid": u["txid"], "vout": u["vout"], "value": u.get("value", 0)} for u in selected_utxos]

            tx_version = struct.pack("<i", 2)
            locktime = struct.pack("<I", 0)

            outputs_bytes = _btc_output(amount_sat, dest_script)
            change_sat = total_input - amount_sat - fee_sat
            if change_sat > 546:
                outputs_bytes += _btc_output(change_sat, change_script)

            addr = unsigned_tx.from_address
            is_segwit = addr.startswith("bc1") or addr.startswith("ltc1")
            script_pubkey = _bitcoin_address_to_script(addr)

            signed_inputs = []
            for i, utxo in enumerate(inputs_list):
                if is_segwit:
                    if addr.startswith("bc1"):
                        _, wit_data = _bech32_decode("bc", addr)
                    else:
                        _, wit_data = _bech32_decode("ltc", addr)
                    if wit_data and len(wit_data) >= 21:
                        pubkey_hash = bytes(wit_data[1:21])
                    else:
                        pubkey_hash = b58decode(addr)[1:21]
                    script_code = b"\x19\x76\xa9\x14" + pubkey_hash + b"\x88\xac"
                    sig_hash = _btc_segwit_sighash(
                        tx_version, inputs_list, outputs_bytes, i,
                        script_code, utxo["value"], locktime, 0x01,
                    )
                else:
                    sig_hash = _btc_legacy_sighash(
                        tx_version, inputs_list, outputs_bytes, i,
                        script_pubkey, locktime, 0x01,
                    )
                priv_key = Secp256k1Key.from_bytes(priv_key_bytes)
                sig = priv_key.ecdsa_sign(sig_hash)
                sig_compact = priv_key.ecdsa_serialize_compact(sig) + b"\x01"

                pubkey_script = _varint(len(sig_compact)) + sig_compact
                pubkey_script += _varint(len(pub_key_bytes)) + pub_key_bytes

                signed_inputs.append(_btc_utxo_to_input(
                    utxo["txid"], utxo["vout"], pubkey_script,
                ))

            if is_segwit:
                tx = tx_version + b"\x00\x01"
                tx += _varint(len(signed_inputs))
                tx += b"".join(signed_inputs)
                tx += _varint(2 if change_sat > 546 else 1)
                tx += outputs_bytes
                for _ in inputs_list:
                    tx += _varint(1) + sig_compact[:64]
                tx += locktime
            else:
                tx = tx_version
                tx += _varint(len(signed_inputs))
                tx += b"".join(signed_inputs)
                tx += _varint(2 if change_sat > 546 else 1)
                tx += outputs_bytes
                tx += locktime

            tx_id = hashlib.sha256(hashlib.sha256(tx).digest()).digest()[::-1].hex()
            raw_hex = tx.hex()
            return SignedTx(raw=raw_hex, tx_id=tx_id, chain="BTC")

        except Exception as e:
            logger.error("BTC tx build failed: %s", e)
            return SignedTx(raw="", tx_id="", chain="BTC", broadcast_error=str(e))

    async def _build_ltc_tx(self, unsigned_tx: UnsignedTx) -> SignedTx:
        priv_key_bytes = self._get_private_key("LTC")
        if not priv_key_bytes:
            return SignedTx(raw="", tx_id="", chain="LTC", broadcast_error="No private key")

        try:
            resp = await self._http.get(
                f"https://api.blockcypher.com/v1/ltc/main/addrs/{unsigned_tx.from_address}?unspentOnly=true"
            )
            data = resp.json()
            utxos = data.get("txrefs", [])
            if not utxos:
                return SignedTx(raw="", tx_id="", chain="LTC", broadcast_error="No UTXOs available")

            from bip_utils import Bip44Coins, Bip44, Bip44Changes, Bip39SeedGenerator
            seed = Bip39SeedGenerator(self._mnemonic).Generate()
            bip44_ltc = Bip44.FromSeed(seed, Bip44Coins.LITECOIN)
            pub_key_bytes = bip44_ltc.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().Raw().ToBytes()

            amount_sat = int(unsigned_tx.value * 1e8)
            fee_sat = 2000
            total_input = 0
            selected = []
            for utxo in utxos:
                total_input += utxo.get("value", 0)
                selected.append({
                    "txid": utxo["tx_hash"],
                    "vout": utxo["tx_output_n"],
                    "value": utxo["value"],
                })
                if total_input >= amount_sat + fee_sat:
                    break

            if total_input < amount_sat + fee_sat:
                return SignedTx(raw="", tx_id="", chain="LTC", broadcast_error="Insufficient funds")

            inputs_list = [{"txid": s["txid"], "vout": s["vout"], "value": s.get("value", 0)} for s in selected]

            tx_version = struct.pack("<i", 2)
            locktime = struct.pack("<I", 0)

            outputs_bytes = _btc_output(amount_sat, dest_script)
            change_sat = total_input - amount_sat - fee_sat
            if change_sat > 546:
                outputs_bytes += _btc_output(change_sat, change_script)

            addr = unsigned_tx.from_address
            is_segwit = addr.startswith("ltc1") or addr.startswith("bc1")
            script_pubkey = _bitcoin_address_to_script(addr)

            signed_inputs = []
            for i, s in enumerate(inputs_list):
                if is_segwit:
                    if addr.startswith("ltc1"):
                        _, wit_data = _bech32_decode("ltc", addr)
                    else:
                        _, wit_data = _bech32_decode("bc", addr)
                    if wit_data and len(wit_data) >= 21:
                        pubkey_hash = bytes(wit_data[1:21])
                    else:
                        pubkey_hash = b58decode(addr)[1:21]
                    script_code = b"\x19\x76\xa9\x14" + pubkey_hash + b"\x88\xac"
                    sig_hash = _btc_segwit_sighash(
                        tx_version, inputs_list, outputs_bytes, i,
                        script_code, s["value"], locktime, 0x01,
                    )
                else:
                    sig_hash = _btc_legacy_sighash(
                        tx_version, inputs_list, outputs_bytes, i,
                        script_pubkey, locktime, 0x01,
                    )
                priv_key = Secp256k1Key.from_bytes(priv_key_bytes)
                sig = priv_key.ecdsa_sign(sig_hash)
                sig_compact = priv_key.ecdsa_serialize_compact(sig) + b"\x01"

                pubkey_script = _varint(len(sig_compact)) + sig_compact
                pubkey_script += _varint(len(pub_key_bytes)) + pub_key_bytes

                signed_inputs.append(_btc_utxo_to_input(s["txid"], s["vout"], pubkey_script))

            if is_segwit:
                tx = tx_version + b"\x00\x01"
                tx += _varint(len(signed_inputs))
                tx += b"".join(signed_inputs)
                tx += _varint(2 if change_sat > 546 else 1)
                tx += outputs_bytes
                for _ in inputs_list:
                    tx += _varint(1) + sig_compact[:64]
                tx += locktime
            else:
                tx = tx_version
                tx += _varint(len(signed_inputs))
                tx += b"".join(signed_inputs)
                tx += _varint(2 if change_sat > 546 else 1)
                tx += outputs_bytes
                tx += locktime

            tx_id = hashlib.sha256(hashlib.sha256(tx).digest()).digest()[::-1].hex()
            return SignedTx(raw=tx.hex(), tx_id=tx_id, chain="LTC")

        except Exception as e:
            logger.error("LTC tx build failed: %s", e)
            return SignedTx(raw="", tx_id="", chain="LTC", broadcast_error=str(e))

    def _decode_sol_address(self, addr: str) -> bytes:
        if addr.startswith("0x"):
            addr = addr[2:]
        try:
            return bytes.fromhex(addr)
        except ValueError:
            pass
        return b58decode(addr)

    async def _build_sol_tx(self, unsigned_tx: UnsignedTx) -> SignedTx:
        priv_key_bytes = self._get_private_key("SOL")
        if not priv_key_bytes:
            return SignedTx(raw="", tx_id="", chain="SOL", broadcast_error="No private key")

        try:
            resp = await self._http.post(
                "https://api.mainnet-beta.solana.com",
                json={"jsonrpc": "2.0", "id": 1, "method": "getRecentBlockhash"},
            )
            data = resp.json()
            blockhash = data.get("result", {}).get("value", {}).get("blockhash", "")
            if not blockhash:
                return SignedTx(raw="", tx_id="", chain="SOL", broadcast_error="No blockhash")

            from_bytes = self._decode_sol_address(unsigned_tx.from_address)
            to_bytes = self._decode_sol_address(unsigned_tx.to)

            lamports = int(unsigned_tx.value * 1e9)
            recent_blockhash_bytes = b58decode(blockhash)

            # Solana MessageHeader: num_required_sigs, num_readonly_signed, num_readonly_unsigned
            message = b""
            message += bytes([1])  # num required sigs
            message += bytes([0])  # num readonly signed
            message += bytes([1])  # num readonly unsigned (system program)

            # Account keys: fee-payer, destination, system program
            system_program = b58decode("11111111111111111111111111111111")
            message += _varint(3)
            message += from_bytes
            message += to_bytes
            message += system_program

            # Blockhash (32 bytes)
            message += recent_blockhash_bytes

            # Instruction: SystemProgram.Transfer
            # accounts: from_index=0, to_index=1
            accounts = bytes([2, 0, 1])
            program_index = bytes([2])
            instruction_data = struct.pack("<I", 2) + struct.pack("<Q", lamports)
            message += bytes([1])  # num instructions
            message += accounts
            message += program_index
            message += bytes([len(instruction_data)])
            message += instruction_data

            signing_key = nacl.signing.SigningKey(priv_key_bytes, encoder=nacl.encoding.RawEncoder)

            # Solana signs the raw message directly (not hashed)
            signed = signing_key.sign(message)

            # Transaction format: [1-byte sig_count][64-byte sig][message]
            tx_bytes = bytes([1]) + signed.signature + message

            tx_id = hashlib.sha256(tx_bytes).hexdigest()
            return SignedTx(raw=tx_bytes.hex(), tx_id=tx_id, chain="SOL")

        except Exception as e:
            logger.error("SOL tx build failed: %s", e)
            return SignedTx(raw="", tx_id="", chain="SOL", broadcast_error=str(e))

    async def _build_xrp_tx(self, unsigned_tx: UnsignedTx) -> SignedTx:
        priv_key_bytes = self._get_private_key("XRP")
        if not priv_key_bytes:
            return SignedTx(raw="", tx_id="", chain="XRP", broadcast_error="No private key")

        try:
            resp = await self._http.post(
                "https://s1.ripple.com:51234/",
                json={"jsonrpc": "2.0", "id": 1, "method": "account_info", "params": [{"account": unsigned_tx.from_address}]},
            )
            account_data = resp.json()
            sequence = account_data.get("result", {}).get("account_data", {}).get("Sequence", 0)

            drops = int(unsigned_tx.value * 1_000_000)
            fee_drops = 12

            # XRP Ledger canonical binary format: field ID + value-length + value
            def encode_field(field_id: int, value: bytes) -> bytes:
                if field_id < 256:
                    return bytes([field_id]) + _varint(len(value)) + value
                return bytes([field_id >> 8, field_id & 0xFF]) + _varint(len(value)) + value

            def encode_uint32(val: int) -> bytes:
                return val.to_bytes(4, "big")

            def encode_uint64(val: int) -> bytes:
                return val.to_bytes(8, "big")

            def encode_vl(val: str) -> bytes:
                b = val.encode()
                return _varint(len(b)) + b

            def encode_amount(val: int, currency: str = "XRP", issuer: str = "") -> bytes:
                if currency == "XRP":
                    return bytes([0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]) + val.to_bytes(8, "big")
                return b""

            # Field IDs (from XRP Ledger spec)
            LEDGER_ENTRY_TYPE = 0x01
            TRANSACTION_TYPE = 0x02
            FLAGS = 0x02
            SEQUENCE = 0x04
            FEE = 0x08
            ACCOUNT = 0x01
            DESTINATION = 0x03
            AMOUNT = 0x06

            # Transaction type: Payment = 0
            tx = bytes([TRANSACTION_TYPE]) + encode_uint32(0)

            # Flags: 2147483648 = 0x80000000
            tx += bytes([FLAGS]) + encode_uint32(2147483648)

            # Sequence
            tx += bytes([SEQUENCE]) + encode_uint32(sequence)

            # Fee
            tx += bytes([FEE]) + encode_amount(fee_drops)

            # Account (using field ID in type+field format)
            account_bytes = b58decode(unsigned_tx.from_address)
            tx += bytes([0x81]) + _varint(len(account_bytes)) + account_bytes

            # Destination
            dest_bytes = b58decode(unsigned_tx.to)
            tx += bytes([0x83]) + _varint(len(dest_bytes)) + dest_bytes

            # Amount (XRP drops)
            tx += bytes([0x86]) + encode_amount(drops)

            priv_key = Secp256k1Key.from_bytes(priv_key_bytes)
            pub_key_compressed = priv_key.public_key.format(compressed=True)
            msg_hash = hashlib.sha256(hashlib.sha256(tx).digest()).digest()
            sig = priv_key.ecdsa_sign(msg_hash)
            sig_compact = priv_key.ecdsa_serialize_compact(sig)

            tx += bytes([0x73]) + _varint(len(pub_key_compressed)) + pub_key_compressed
            tx += bytes([0x74]) + _varint(len(sig_compact)) + sig_compact

            # Full signed blob: hash prefix 0x53545800 + tx
            blob = b"\x53\x54\x58\x00" + tx
            raw_hex = blob.hex()

            tx_id = hashlib.sha256(hashlib.sha256(tx).digest()).digest().hex()[:64]

            return SignedTx(raw=raw_hex, tx_id=tx_id, chain="XRP")

        except Exception as e:
            logger.error("XRP tx build failed: %s", e)
            return SignedTx(raw="", tx_id="", chain="XRP", broadcast_error=str(e))

    async def _build_tron_tx(self, unsigned_tx: UnsignedTx) -> SignedTx:
        priv_key_bytes = self._get_private_key("TRON")
        if not priv_key_bytes:
            return SignedTx(raw="", tx_id="", chain="TRON", broadcast_error="No private key")

        try:
            sun_value = int(unsigned_tx.value * 1e6)
            resp = await self._http.post(
                "https://api.trongrid.io/wallet/createtransaction",
                json={
                    "to_address": unsigned_tx.to,
                    "owner_address": unsigned_tx.from_address,
                    "amount": sun_value,
                },
            )
            tx_data = resp.json()
            if "Error" in tx_data:
                return SignedTx(raw="", tx_id="", chain="TRON", broadcast_error=tx_data.get("Error"))

            raw_tx_hex = tx_data.get("raw_data_hex", "")
            tx_id = tx_data.get("txID", "")

            msg = bytes.fromhex(raw_tx_hex) if raw_tx_hex else b""
            if msg:
                priv_key = Secp256k1Key.from_bytes(priv_key_bytes)
                sig = priv_key.ecdsa_sign(msg)
                sig_der = priv_key.ecdsa_serialize_compact(sig)
                sig_hex = sig_der.hex()
            else:
                sig_hex = ""

            return SignedTx(raw=sig_hex, tx_id=tx_id, chain="TRON")

        except Exception as e:
            logger.error("TRON tx build failed: %s", e)
            return SignedTx(raw="", tx_id="", chain="TRON", broadcast_error=str(e))

    async def sign(self, unsigned_tx: UnsignedTx) -> SignedTx:
        chain = unsigned_tx.chain
        if chain == "BTC":
            return await self._build_btc_tx(unsigned_tx)
        elif chain == "LTC":
            return await self._build_ltc_tx(unsigned_tx)
        elif chain in EVM_CHAINS:
            return await self._build_evm_tx(unsigned_tx)
        elif chain == "SOL":
            return await self._build_sol_tx(unsigned_tx)
        elif chain == "XRP":
            return await self._build_xrp_tx(unsigned_tx)
        elif chain == "TRON":
            return await self._build_tron_tx(unsigned_tx)
        else:
            return SignedTx(raw="", tx_id="", chain=chain, broadcast_error=f"Unsupported chain: {chain}")

    async def broadcast(self, signed_tx: SignedTx) -> str:
        chain = signed_tx.chain
        raw = signed_tx.raw
        if not raw:
            return signed_tx.broadcast_error or "Empty transaction"

        try:
            if chain == "BTC":
                resp = await self._http.post(
                    "https://blockstream.info/api/tx",
                    content=bytes.fromhex(raw),
                    headers={"Content-Type": "application/octet-stream"},
                )
                if resp.status_code == 200:
                    txid = resp.text.strip()
                    return txid
                return f"BTC broadcast failed: {resp.text}"

            elif chain == "LTC":
                resp = await self._http.post(
                    "https://api.blockcypher.com/v1/ltc/main/txs/push",
                    json={"tx": raw},
                )
                if resp.status_code == 201:
                    return resp.json().get("tx", {}).get("hash", "")
                return f"LTC broadcast failed: {resp.text}"

            elif chain in EVM_CHAINS:
                chain_id = EVM_CHAINS[chain]
                rpc_urls = {
                    1: "https://eth.llamarpc.com",
                    56: "https://bsc-dataseed1.binance.org",
                    137: "https://polygon-rpc.com",
                }
                rpc = rpc_urls.get(chain_id)
                if not rpc:
                    return f"No RPC for chain {chain}"
                resp = await self._http.post(
                    rpc,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_sendRawTransaction",
                        "params": [raw],
                    },
                )
                data = resp.json()
                if "result" in data:
                    return data["result"]
                return f"EVM broadcast failed: {data.get('error', {}).get('message', resp.text)}"

            elif chain == "SOL":
                resp = await self._http.post(
                    "https://api.mainnet-beta.solana.com",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "sendTransaction",
                        "params": [raw, {"encoding": "hex"}],
                    },
                )
                data = resp.json()
                if "result" in data:
                    return data["result"]
                return f"SOL broadcast failed: {data.get('error', {}).get('message', resp.text)}"

            elif chain == "XRP":
                resp = await self._http.post(
                    "https://s1.ripple.com:51234/",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "submit",
                        "params": [{"tx_blob": raw}],
                    },
                )
                data = resp.json()
                result = data.get("result", {})
                if result.get("engine_result") == "tesSUCCESS":
                    return result.get("tx_json", {}).get("hash", "")
                return f"XRP broadcast failed: {result.get('engine_result_message', resp.text)}"

            elif chain == "TRON":
                resp = await self._http.post(
                    "https://api.trongrid.io/wallet/broadcasttransaction",
                    json={"signature": [raw], "txID": signed_tx.tx_id},
                )
                data = resp.json()
                if data.get("result"):
                    return data.get("txid", "")
                return f"TRON broadcast failed: {data.get('Error', resp.text)}"

            else:
                return f"Unsupported chain for broadcast: {chain}"

        except Exception as e:
            logger.error("Broadcast failed for %s: %s", chain, e)
            return f"Broadcast error: {e}"

    async def close(self) -> None:
        await self._http.aclose()