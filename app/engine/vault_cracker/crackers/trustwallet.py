import json
import logging
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import scrypt
from Crypto.Hash import keccak

logger = logging.getLogger(__name__)


def _hashcat_hash(vault_data: dict) -> Optional[str]:
    crypto = vault_data.get("crypto", vault_data.get("Crypto", {}))
    if not crypto:
        return None
    kdf_params = crypto.get("kdfparams", crypto.get("kdfparams", {}))
    salt = kdf_params.get("salt", "")
    n = kdf_params.get("n", 16384)
    r = kdf_params.get("r", 8)
    p = kdf_params.get("p", 6)
    return f"$trustwallet${n}*{r}*{p}*{salt}"


def decrypt_vault(vault_path: str, password: str) -> Optional[dict]:
    try:
        with open(vault_path) as f:
            ks = json.load(f)
    except Exception as e:
        logger.debug("Failed to read keystore %s: %s", vault_path, e)
        return None
    return _decrypt_vault_data(ks, password)


def _decrypt_vault_data(vault_data: dict, password: str) -> Optional[dict]:
    crypto = vault_data.get("crypto", vault_data.get("Crypto", {}))
    if not crypto:
        logger.debug("No crypto field in keystore")
        return None

    ciphertext_hex = crypto.get("ciphertext", "")
    kdf_params = crypto.get("kdfparams", crypto.get("kdfparams", {}))
    cipher_params = crypto.get("cipherparams", {})
    mac_hex = crypto.get("mac", "")
    kdf = crypto.get("kdf", "scrypt")

    try:
        ciphertext = bytes.fromhex(ciphertext_hex)
        salt = bytes.fromhex(kdf_params.get("salt", ""))
        iv = bytes.fromhex(cipher_params.get("iv", ""))
        mac = bytes.fromhex(mac_hex)
    except (ValueError, KeyError) as e:
        logger.debug("Hex decode failed: %s", e)
        return None

    if kdf == "scrypt":
        n = kdf_params.get("n", 16384)
        r = kdf_params.get("r", 8)
        p = kdf_params.get("p", 6)
        dklen = kdf_params.get("dklen", 32)
        try:
            derived_key = scrypt(password, salt, key_len=dklen, N=n, r=r, p=p)
        except Exception as e:
            logger.debug("Scrypt failed: %s", e)
            return None
    else:
        logger.debug("Unsupported KDF: %s", kdf)
        return None

    mac_check = keccak.new(digest_bits=256)
    mac_check.update(derived_key[16:32] + ciphertext)
    if mac_check.digest() != mac:
        logger.debug("MAC mismatch \u2014 wrong password")
        return None

    enc_key = derived_key[:16]
    try:
        cipher = AES.new(enc_key, AES.MODE_CTR, nonce=b"", initial_value=iv)
        plaintext = cipher.decrypt(ciphertext)
        decoded = plaintext.decode("utf-8", errors="replace")
        result = json.loads(decoded)
        return result
    except Exception as e:
        logger.debug("AES decrypt failed: %s", e)
        return None
