import base64
import hashlib
import json
import logging
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2

logger = logging.getLogger(__name__)

ITERATION_VARIANTS = [600000, 100000, 10000]


def _pbkdf2_sha512(password: str, salt: bytes, dkLen: int, count: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, count, dklen=dkLen)


def _hashcat_hash(vault_data: dict) -> Optional[str]:
    salt_b64 = vault_data.get("salt", "")
    ciphertext_b64 = vault_data.get("data", "")
    try:
        salt = base64.b64decode(salt_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
    except Exception:
        return None
    salt_hex = salt.hex()
    data_hex = ciphertext[:-16].hex()
    tag_hex = ciphertext[-16:].hex()
    return f"$metamask$1*{salt_hex}*{data_hex}*{tag_hex}"


def decrypt_vault(vault_path: str, password: str) -> Optional[dict]:
    try:
        with open(vault_path) as f:
            vault = json.load(f)
    except Exception as e:
        logger.debug("Failed to read vault %s: %s", vault_path, e)
        return None
    return _decrypt_vault_data(vault, password)


def _decrypt_vault_data(vault_data: dict, password: str) -> Optional[dict]:
    data_b64 = vault_data.get("data")
    iv_b64 = vault_data.get("iv")
    salt_b64 = vault_data.get("salt")

    if not all([data_b64, iv_b64, salt_b64]):
        logger.debug("Missing fields in vault JSON")
        return None

    try:
        ciphertext = base64.b64decode(data_b64)
        iv = base64.b64decode(iv_b64)
        salt = base64.b64decode(salt_b64)
    except Exception as e:
        logger.debug("Base64 decode failed: %s", e)
        return None

    auth_tag = ciphertext[-16:]
    encrypted = ciphertext[:-16]

    for iterations in ITERATION_VARIANTS:
        for hash_algo, kdf_fn in [
            ("SHA-256", lambda pw, s, dk, cnt: PBKDF2(pw, s, dkLen=dk, count=cnt)),
            ("SHA-512", lambda pw, s, dk, cnt: _pbkdf2_sha512(pw, s, dk, cnt)),
        ]:
            try:
                key = kdf_fn(password, salt, 32, iterations)
                if len(key) < 32:
                    continue
                key = key[:32]
                cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
                plaintext = cipher.decrypt_and_verify(encrypted, auth_tag)
                result = json.loads(plaintext.decode("utf-8"))
                logger.debug("Decrypted with %s, %d iterations", hash_algo, iterations)
                return result
            except (ValueError, KeyError, json.JSONDecodeError):
                continue

    return None
