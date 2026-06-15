import logging
import os
import glob

logger = logging.getLogger(__name__)

ARGENT_CONTRACTS = {
    "v1": {
        "base_wallet": "0x2BE0eDe3C1F991A3A2bC4Cb0fC4bD48b9E5eA5D5",
        "implementation": "0x3E043B0C9CdA39FcCFb15A8E19e73db7f9f92b52",
    },
}

FILESYSTEM_PATTERNS = ["argent*", "argent-*", "argent_backup*", "argent_*"]


def _checksum_address(addr: str) -> str:
    addr = addr.lower().replace("0x", "")
    try:
        from Crypto.Hash import keccak
        hash_bytes = keccak.new(digest_bits=256).update(addr.encode()).digest()
        result = "0x"
        for i, c in enumerate(addr):
            nibble = hash_bytes[i // 2] >> (4 * (1 - i % 2)) if i % 2 == 0 else hash_bytes[i // 2] & 0xF
            if nibble >= 8:
                result += c.upper()
            else:
                result += c
        return result
    except Exception:
        return "0x" + addr


def derive_argent_addresses(owner_address: str) -> list[dict]:
    owner = owner_address.lower().replace("0x", "")
    results = []
    for version, cfg in ARGENT_CONTRACTS.items():
        impl = cfg["implementation"].lower().replace("0x", "")
        base = cfg["base_wallet"].lower().replace("0x", "")
        salt = _keccak256(bytes.fromhex(owner + impl.replace("0x", "")))
        impl_padded = bytes.fromhex(impl.zfill(64))
        base_padded = bytes.fromhex(base.zfill(64))
        raw = salt + impl_padded + base_padded
        addr_bytes = _keccak256(raw)[12:]
        addr = _checksum_address(addr_bytes.hex())
        results.append({
            "address": addr,
            "version": version,
            "chain": "ETH",
            "source": "argent_create2",
            "note": f"Argent {version} wallet for owner {owner_address[:10]}...",
        })
    return results


def _keccak256(data: bytes) -> bytes:
    from Crypto.Hash import keccak
    return keccak.new(digest_bits=256).update(data).digest()


def scan_filesystem() -> list[dict]:
    found = []
    search_dirs = [
        os.path.expandvars("%APPDATA%"),
        os.path.expandvars("%LOCALAPPDATA%"),
        os.path.expandvars("%USERPROFILE%"),
        os.path.expandvars("%HOMEDRIVE%%HOMEPATH%"),
    ]
    for base in search_dirs:
        if not base or not os.path.isdir(base):
            continue
        for pattern in FILESYSTEM_PATTERNS:
            full_pattern = os.path.join(base, "**", pattern)
            try:
                for fp in glob.glob(full_pattern, recursive=True):
                    if os.path.isfile(fp):
                        try:
                            size = os.path.getsize(fp)
                            found.append({
                                "file_path": fp,
                                "wallet_type": "Argent",
                                "file_size_bytes": size,
                                "detected_by": "name_pattern",
                            })
                        except Exception:
                            pass
            except Exception:
                pass
    return found
