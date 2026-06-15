import json
import logging
import mmap
import multiprocessing
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from app.engine.vault_scanner import WALLET_CONFIGS, VaultScanResult
from app.engine.vault_cracker.crackers import metamask, exodus, phantom, trustwallet

logger = logging.getLogger(__name__)

CRACKER_MAP = {
    "MetaMask": ("metamask_json", metamask.decrypt_vault, metamask._hashcat_hash, metamask._decrypt_vault_data),
    "Exodus": ("seco_binary", exodus.decrypt_vault, exodus._hashcat_hash, exodus._decrypt_vault_data),
    "Phantom": ("nacl_encrypted", phantom.decrypt_vault, phantom._hashcat_hash, phantom.decrypt_vault),
    "Trust Wallet": ("keystore_v3", trustwallet.decrypt_vault, trustwallet._hashcat_hash, trustwallet._decrypt_vault_data),
}

HASHCAT_MODES = {
    "metamask_json": 26610,
    "seco_binary": 15600,
    "nacl_encrypted": 15600,
    "keystore_v3": 15600,
}


def _load_passwords_mmap(file_path: str, max_passwords: int) -> list[str]:
    passwords = []
    with open(file_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            remaining = max_passwords
            start = 0
            while remaining > 0:
                end = m.find(b"\n", start)
                if end == -1:
                    line = m[start:].strip()
                    if line:
                        passwords.append(line.decode("utf-8", errors="replace"))
                    break
                line = m[start:end].strip()
                if line:
                    passwords.append(line.decode("utf-8", errors="replace"))
                    remaining -= 1
                start = end + 1
    return passwords


def _generate_smart_candidates(base_passwords: list[str], max_total: int) -> list[str]:
    candidates = []
    append_suffixes = ["123", "1234", "12345", "123456", "!", "@", "#", "2024", "2025",
                       "2026", "1", "11", "111", "admin", "pass", "wallet", "btc", "eth"]
    for pw in base_passwords:
        candidates.append(pw)
        if len(candidates) >= max_total:
            return candidates[:max_total]
        pw_lower = pw.lower()
        pw_cap = pw.capitalize()
        pw_upper = pw.upper()
        for var in [pw_lower, pw_cap, pw_upper]:
            if var != pw and var not in candidates:
                candidates.append(var)
                if len(candidates) >= max_total:
                    return candidates[:max_total]
        for suffix in append_suffixes:
            candidate = pw + suffix
            if candidate not in candidates:
                candidates.append(candidate)
                if len(candidates) >= max_total:
                    return candidates[:max_total]
            candidate_cap = pw_cap + suffix
            if candidate_cap not in candidates:
                candidates.append(candidate_cap)
                if len(candidates) >= max_total:
                    return candidates[:max_total]
    return candidates[:max_total]


def _try_password_batch(args: tuple) -> Optional[dict]:
    crack_type, vault_data, wallet_type, file_path, passwords_chunk = args
    if crack_type == "metamask_json":
        for pw in passwords_chunk:
            try:
                result = metamask._decrypt_vault_data(vault_data, pw)
                if result is not None:
                    return {"wallet_type": wallet_type, "file_path": file_path, "password": pw, "decrypted": result}
            except Exception:
                pass
    elif crack_type == "seco_binary":
        for pw in passwords_chunk:
            try:
                result = exodus._decrypt_vault_data(vault_data, pw)
                if result is not None:
                    return {"wallet_type": wallet_type, "file_path": file_path, "password": pw, "decrypted": result}
            except Exception:
                pass
    elif crack_type == "nacl_encrypted":
        for pw in passwords_chunk:
            try:
                result = phantom.decrypt_vault(vault_data, pw)
                if result is not None:
                    return {"wallet_type": wallet_type, "file_path": file_path, "password": pw, "decrypted": result}
            except Exception:
                pass
    elif crack_type == "keystore_v3":
        for pw in passwords_chunk:
            try:
                result = trustwallet._decrypt_vault_data(vault_data, pw)
                if result is not None:
                    return {"wallet_type": wallet_type, "file_path": file_path, "password": pw, "decrypted": result}
            except Exception:
                pass
    return None


def _read_vault_once(file_path: str, crack_type: str):
    if crack_type == "metamask_json":
        with open(file_path) as f:
            return json.load(f)
    elif crack_type == "seco_binary":
        with open(file_path, "rb") as f:
            return f.read()
    elif crack_type == "nacl_encrypted":
        with open(file_path) as f:
            return json.load(f)
    elif crack_type == "keystore_v3":
        with open(file_path) as f:
            return json.load(f)
    return None


class WalletCracker:
    def __init__(self, password_list_path: Optional[str] = None, max_workers: int = 0,
                 enable_hashcat: bool = True, hashcat_path: str = "",
                 hashcat_mode: str = "auto", smart_rules: bool = False):
        self._password_list_path = password_list_path
        self._passwords: list[str] = []
        self._loaded = False
        self._max_workers = max_workers if max_workers > 0 else multiprocessing.cpu_count()
        self._enable_hashcat = enable_hashcat
        self._hashcat_path = hashcat_path
        self._hashcat_mode = hashcat_mode
        self._smart_rules = smart_rules
        if not password_list_path:
            _module_root = Path(__file__).resolve().parent
            candidates = [
                _module_root / "wordlists" / "common.txt",
                _module_root.parent.parent / "wordlists" / "common.txt",
            ]
            for c in candidates:
                if c.is_file():
                    self._password_list_path = str(c)
                    break

    def set_password_list(self, path: str) -> None:
        self._password_list_path = path
        self._loaded = False

    def _load_passwords(self, max_passwords: int = 4000000) -> list[str]:
        if self._loaded:
            return self._passwords
        self._passwords = []
        path = self._password_list_path
        if path and os.path.isfile(path):
            try:
                self._passwords = _load_passwords_mmap(path, max_passwords)
            except Exception as e:
                logger.warning("mmap load failed, falling back to line-by-line: %s", e)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f):
                            if i >= max_passwords:
                                break
                            pw = line.strip()
                            if pw:
                                self._passwords.append(pw)
                except Exception as e2:
                    logger.warning("Failed to load password list %s: %s", path, e2)
        if not self._passwords:
            self._passwords = ["password", "123456", "admin", "bitcoin", "wallet", "ethereum",
                               "trust", "metamask", "exodus", "phantom", "test", "123", "1234",
                               "12345", "12345678", "qwerty", "abc123", "passw0rd"]
        if self._smart_rules:
            self._passwords = _generate_smart_candidates(self._passwords, max_passwords)
        self._loaded = True
        return self._passwords

    def _try_hashcat(self, vault_data, crack_type: str, passwords: list[str], wallet_type: str, file_path: str) -> Optional[dict]:
        hashcat_bin = self._hashcat_path or shutil.which("hashcat")
        if not hashcat_bin:
            logger.debug("Hashcat not found, skipping GPU acceleration")
            return None

        hc_mode = self._hashcat_mode
        if hc_mode == "auto":
            hc_mode = str(HASHCAT_MODES.get(crack_type, 10000))

        hc_hash = None
        if crack_type == "metamask_json":
            hc_hash = metamask._hashcat_hash(vault_data)
        elif crack_type == "seco_binary":
            hc_hash = exodus._hashcat_hash(vault_data)
        elif crack_type == "nacl_encrypted":
            hc_hash = phantom._hashcat_hash(vault_data)
        elif crack_type == "keystore_v3":
            hc_hash = trustwallet._hashcat_hash(vault_data)

        if not hc_hash:
            return None

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                hash_file = os.path.join(tmpdir, "hash.txt")
                dict_file = os.path.join(tmpdir, "dict.txt")
                pot_file = os.path.join(tmpdir, "potfile.txt")

                with open(hash_file, "w") as f:
                    f.write(hc_hash + "\n")
                with open(dict_file, "w", encoding="utf-8", errors="ignore") as f:
                    for pw in passwords:
                        f.write(pw + "\n")

                cmd = [
                    hashcat_bin,
                    "-m", hc_mode,
                    "-a", "0",
                    hash_file,
                    dict_file,
                    "--potfile-path", pot_file,
                    "--outfile-format", "2",
                    "-O",
                    "--force",
                ]
                logger.info("Running hashcat: %s", " ".join(cmd[:6]) + "...")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )

                if os.path.exists(pot_file):
                    with open(pot_file) as f:
                        for line in f:
                            line = line.strip()
                            if ":" in line:
                                cracked_pw = line.split(":", 1)[1]
                                decrypt_data = None
                                if crack_type == "metamask_json":
                                    decrypt_data = metamask._decrypt_vault_data(vault_data, cracked_pw)
                                elif crack_type == "seco_binary":
                                    decrypt_data = exodus._decrypt_vault_data(vault_data, cracked_pw)
                                elif crack_type == "nacl_encrypted":
                                    decrypt_data = phantom.decrypt_vault(vault_data, cracked_pw)
                                elif crack_type == "keystore_v3":
                                    decrypt_data = trustwallet._decrypt_vault_data(vault_data, cracked_pw)
                                if decrypt_data is not None:
                                    logger.info("Hashcat cracked %s vault %s with password: %s", wallet_type, file_path, cracked_pw)
                                    return {
                                        "wallet_type": wallet_type,
                                        "file_path": file_path,
                                        "password": cracked_pw,
                                        "decrypted": decrypt_data,
                                        "attempts": -1,
                                        "hashcat": True,
                                    }
        except FileNotFoundError:
            logger.debug("Hashcat binary not found: %s", hashcat_bin)
        except subprocess.TimeoutExpired:
            logger.debug("Hashcat timed out")
        except Exception as e:
            logger.debug("Hashcat failed: %s", e)
        return None

    def crack(self, result: VaultScanResult, max_attempts: int = 4000000) -> Optional[dict]:
        wallet_type = result.wallet_type
        if wallet_type not in CRACKER_MAP:
            return None

        crack_type, _, _, decrypt_data_fn = CRACKER_MAP[wallet_type]
        file_path = result.file_path

        if not os.path.isfile(file_path):
            return None

        passwords = self._load_passwords(max_attempts)
        passwords = passwords[:max_attempts]
        if not passwords:
            return None

        vault_data = _read_vault_once(file_path, crack_type)
        if vault_data is None:
            return None

        if self._enable_hashcat:
            hc_result = self._try_hashcat(vault_data, crack_type, passwords, wallet_type, file_path)
            if hc_result is not None:
                return hc_result

        chunk_size = max(1, len(passwords) // self._max_workers)
        chunks = [passwords[i:i + chunk_size] for i in range(0, len(passwords), chunk_size)]

        args_list = [
            (crack_type, vault_data, wallet_type, file_path, chunk)
            for chunk in chunks
        ]

        with ProcessPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [executor.submit(_try_password_batch, args) for args in args_list]
            for future in as_completed(futures):
                try:
                    outcome = future.result()
                    if outcome is not None:
                        outcome["attempts"] = outcome.get("attempts", 1)
                        return outcome
                except Exception as e:
                    logger.debug("Worker failed: %s", e)

        return None

    def crack_batch(self, results: list[VaultScanResult], max_attempts: int = 4000000) -> list[dict]:
        cracked = []
        for result in results:
            outcome = self.crack(result, max_attempts)
            if outcome:
                cracked.append(outcome)
        return cracked
