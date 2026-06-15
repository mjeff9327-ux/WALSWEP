import base58
import json
import logging
from typing import Optional

from Crypto.Protocol.KDF import scrypt
import nacl.secret
import nacl.bindings

logger = logging.getLogger(__name__)


def _hashcat_hash(vault_data: dict) -> Optional[str]:
    ek = vault_data.get("encryptedKey", {})
    if not ek:
        return None
    try:
        salt = base58.b58decode(ek.get("salt", ""))
        encrypted = base58.b58decode(ek.get("encrypted", ""))
    except Exception:
        return None
    kdf = ek.get("kdf", "scrypt")
    salt_hex = salt.hex()
    data_hex = encrypted.hex()
    if kdf == "scrypt":
        return f"$phantom_scrypt$4096*8*1*{salt_hex}*{data_hex}"
    iterations = ek.get("iterations", 4096)
    return f"$phantom_pbkdf2${iterations}*{salt_hex}*{data_hex}"


def decrypt_vault(vault_data: dict, password: str) -> Optional[dict]:
    encrypted_key = vault_data.get("encryptedKey")
    if not encrypted_key:
        logger.debug("No encryptedKey in vault data")
        return None

    try:
        encrypted = base58.b58decode(encrypted_key["encrypted"])
        nonce = base58.b58decode(encrypted_key["nonce"])
        salt = base58.b58decode(encrypted_key["salt"])
        kdf = encrypted_key.get("kdf", "scrypt")
        iterations = encrypted_key.get("iterations", 4096)
    except Exception as e:
        logger.debug("Failed to parse encryptedKey: %s", e)
        return None

    if kdf == "scrypt":
        try:
            n = 4096
            r = 8
            p = 1
            key = scrypt(password, salt, key_len=32, N=n, r=r, p=p)
        except Exception as e:
            logger.debug("Scrypt failed: %s", e)
            return None
    elif kdf == "pbkdf2":
        from Crypto.Protocol.KDF import PBKDF2
        try:
            key = PBKDF2(password, salt, dkLen=32, count=iterations)
        except Exception as e:
            logger.debug("PBKDF2 failed: %s", e)
            return None
    else:
        logger.debug("Unknown KDF: %s", kdf)
        return None

    try:
        box = nacl.secret.SecretBox(key)
        decrypted = box.decrypt(encrypted, nonce=nonce)
        return json.loads(decrypted.decode("utf-8"))
    except Exception as e:
        logger.debug("NaCl decrypt failed: %s", e)
        return None
