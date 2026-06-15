import hashlib
import logging
import struct
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import scrypt

logger = logging.getLogger(__name__)

SECO_MAGIC = b"SECO"


def _read_until_null(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end == -1:
        return data[offset:].decode("utf-8", errors="replace"), len(data)
    return data[offset:end].decode("utf-8", errors="replace"), end + 1


def _hashcat_hash(vault_data: bytes) -> Optional[str]:
    if vault_data[:4] != SECO_MAGIC:
        return None
    metadata_offset = vault_data.find(b"\x00" * 32, 4)
    if metadata_offset == -1:
        return None
    metadata_offset -= 32
    metadata = vault_data[metadata_offset:metadata_offset + 256]
    if len(metadata) < 256:
        return None
    salt = metadata[0:32].hex()
    n = struct.unpack(">I", metadata[32:36])[0]
    r = struct.unpack(">I", metadata[36:40])[0]
    p = struct.unpack(">I", metadata[40:44])[0]
    return f"$exodus${n}*{r}*{p}*{salt}"


def decrypt_vault(vault_path: str, password: str) -> Optional[dict]:
    try:
        with open(vault_path, "rb") as f:
            data = f.read()
    except Exception as e:
        logger.debug("Failed to read Exodus vault %s: %s", vault_path, e)
        return None
    return _decrypt_vault_data(data, password)


def _decrypt_vault_data(vault_data: bytes, password: str) -> Optional[dict]:
    if vault_data[:4] != SECO_MAGIC:
        logger.debug("Not a valid SECO container")
        return None

    offset = 4
    version = struct.unpack(">I", vault_data[offset:offset + 4])[0]
    offset += 8

    version_tag_len = vault_data[offset]
    offset += 1
    version_tag = vault_data[offset:offset + version_tag_len].decode("utf-8", errors="replace")
    offset += version_tag_len

    app_name, offset = _read_until_null(vault_data, offset)
    app_version, offset = _read_until_null(vault_data, offset)

    if not version_tag.startswith("seco"):
        logger.debug("Unknown SECO version tag: %s", version_tag)
        return None

    metadata_offset = vault_data.find(b"\x00" * 32, offset)
    if metadata_offset == -1:
        metadata_offset = offset + 224 - (offset % 224) if offset < 224 else offset
    else:
        metadata_offset -= 32

    metadata = vault_data[metadata_offset:metadata_offset + 256]
    if len(metadata) < 256:
        logger.debug("Metadata block too short")
        return None

    salt = metadata[0:32]
    n = struct.unpack(">I", metadata[32:36])[0]
    r = struct.unpack(">I", metadata[36:40])[0]
    p = struct.unpack(">I", metadata[40:44])[0]

    if n == 0 or r == 0:
        logger.debug("Invalid Scrypt params: N=%d, r=%d", n, r)
        return None

    try:
        key = scrypt(password, salt, key_len=32, N=n, r=r, p=p)
    except Exception as e:
        logger.debug("Scrypt failed: %s", e)
        return None

    blob_key_iv = metadata[76:88]
    blob_key_tag = metadata[88:104]
    blob_key_cipher = metadata[104:136]

    blob_offset = metadata_offset + 256
    if blob_offset + 4 > len(vault_data):
        return None
    blob_len = struct.unpack(">I", vault_data[blob_offset:blob_offset + 4])[0]
    blob_offset += 4
    if blob_offset + blob_len > len(vault_data):
        return None
    blob_encrypted = vault_data[blob_offset:blob_offset + blob_len]

    try:
        cipher = AES.new(key, AES.MODE_GCM, nonce=blob_key_iv)
        blob_key = cipher.decrypt_and_verify(blob_key_cipher, blob_key_tag)
    except Exception:
        return None

    blob_iv = metadata[136:148]
    blob_tag = metadata[148:164]

    try:
        cipher = AES.new(blob_key, AES.MODE_GCM, nonce=blob_iv)
        plaintext = cipher.decrypt_and_verify(blob_encrypted, blob_tag)
        decoded = plaintext.decode("utf-8", errors="replace")
        return {"plaintext": decoded, "app": app_name, "version": app_version}
    except Exception:
        return None
