import hashlib
import logging
import mmap
import multiprocessing
import os
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from mnemonic import Mnemonic

from app.implementations.bip39_solver import Bip39Solver
from app.interfaces.node_client import INodeClient
from app.engine.sweep_engine import SweepEngine

logger = logging.getLogger(__name__)

SOLVER = Bip39Solver()
MNEMONIC = Mnemonic("english")

ALL_CHAINS = ["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"]

_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


class RainbowCache:
    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self._db_path = db_path
        else:
            _DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._db_path = str(_DEFAULT_CACHE_DIR / "brainwallet_cache.db")
        self._conn: Optional[sqlite3.Connection] = None
        self._insert_count = 0

    def _ensure_db(self):
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self._db_path, timeout=30)
        self._conn.execute("CREATE TABLE IF NOT EXISTS rainbow (phrase_hash TEXT PRIMARY KEY, mnemonic TEXT)")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=OFF")

    def get(self, phrase: str) -> Optional[str]:
        self._ensure_db()
        h = hashlib.sha256(phrase.encode("utf-8")).hexdigest()
        cursor = self._conn.execute("SELECT mnemonic FROM rainbow WHERE phrase_hash = ?", (h,))
        row = cursor.fetchone()
        return row[0] if row else None

    def put(self, phrase: str, mnemonic: str):
        self._ensure_db()
        h = hashlib.sha256(phrase.encode("utf-8")).hexdigest()
        self._conn.execute("INSERT OR REPLACE INTO rainbow (phrase_hash, mnemonic) VALUES (?, ?)", (h, mnemonic))
        self._insert_count += 1
        if self._insert_count % 1000 == 0:
            self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None


def _passphrase_to_mnemonic(passphrase: str) -> Optional[str]:
    try:
        entropy = hashlib.sha256(passphrase.encode("utf-8")).digest()
        mnemonic_words = MNEMONIC.to_mnemonic(entropy)
        return mnemonic_words
    except Exception as e:
        logger.debug("Failed to convert passphrase to mnemonic: %s", e)
        return None


def _scan_phrase_chunk(args: tuple) -> list[dict]:
    phrases, chains = args
    local_mnemo = Mnemonic("english")
    local_solver = Bip39Solver()
    matches = []
    for phrase in phrases:
        try:
            entropy = hashlib.sha256(phrase.encode("utf-8")).digest()
            mnemonic_words = local_mnemo.to_mnemonic(entropy)
            if not mnemonic_words:
                continue
            derived = local_solver.solve(mnemonic_words)
            for addr_info in derived.addresses:
                if addr_info["chain"] in chains and addr_info["address"]:
                    matches.append({
                        "passphrase": phrase,
                        "mnemonic": mnemonic_words,
                        "chain": addr_info["chain"],
                        "address": addr_info["address"],
                    })
        except Exception:
            pass
    return matches


def _load_phrases_mmap(file_path: str, max_phrases: int) -> list[str]:
    phrases = []
    with open(file_path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            remaining = max_phrases
            start = 0
            while remaining > 0:
                end = m.find(b"\n", start)
                if end == -1:
                    line = m[start:].strip()
                    if line:
                        phrases.append(line.decode("utf-8", errors="replace"))
                    break
                line = m[start:end].strip()
                if line:
                    phrases.append(line.decode("utf-8", errors="replace"))
                    remaining -= 1
                start = end + 1
    return phrases


def _process_phrase_with_cache(args: tuple) -> list[dict]:
    phrases, chains, cache_db_path = args
    local_mnemo = Mnemonic("english")
    local_solver = Bip39Solver()
    cache = RainbowCache(cache_db_path) if cache_db_path else None
    matches = []
    for phrase in phrases:
        mnemonic_words = None
        if cache:
            mnemonic_words = cache.get(phrase)
        if mnemonic_words is None:
            try:
                entropy = hashlib.sha256(phrase.encode("utf-8")).digest()
                mnemonic_words = local_mnemo.to_mnemonic(entropy)
                if cache and mnemonic_words:
                    cache.put(phrase, mnemonic_words)
            except Exception:
                continue
        if not mnemonic_words:
            continue
        try:
            derived = local_solver.solve(mnemonic_words)
            for addr_info in derived.addresses:
                if addr_info["chain"] in chains and addr_info["address"]:
                    matches.append({
                        "passphrase": phrase,
                        "mnemonic": mnemonic_words,
                        "chain": addr_info["chain"],
                        "address": addr_info["address"],
                    })
        except Exception:
            pass
    if cache:
        cache.close()
    return matches


class BrainwalletScanner:
    def __init__(self, node_client: Optional[INodeClient] = None, sweep_engine: Optional[SweepEngine] = None,
                 max_workers: int = 0, enable_rainbow_cache: bool = True,
                 cache_db_path: Optional[str] = None):
        self._node_client = node_client
        self._sweep_engine = sweep_engine
        self._max_workers = max_workers if max_workers > 0 else multiprocessing.cpu_count()
        self._enable_rainbow_cache = enable_rainbow_cache
        self._cache_db_path = cache_db_path

    def set_node_client(self, node_client: INodeClient) -> None:
        self._node_client = node_client

    def set_sweep_engine(self, sweep_engine: SweepEngine) -> None:
        self._sweep_engine = sweep_engine

    def scan_dictionary(self, dictionary_path: str, chains: list[str] | None = None, max_phrases: int = 4000000) -> dict:
        if chains is None:
            chains = ALL_CHAINS

        results = {
            "total_phrases": 0,
            "matches_found": 0,
            "matches": [],
            "errors": [],
        }

        try:
            phrases = _load_phrases_mmap(dictionary_path, max_phrases)
        except FileNotFoundError:
            results["errors"].append(f"Dictionary not found: {dictionary_path}")
            return results
        except Exception as e:
            results["errors"].append(f"Scan failed: {e}")
            return results

        results["total_phrases"] = len(phrases)
        if not phrases:
            return results

        chunk_size = max(1, len(phrases) // self._max_workers)
        chunks = [phrases[i:i + chunk_size] for i in range(0, len(phrases), chunk_size)]

        if self._enable_rainbow_cache:
            args_list = [(chunk, chains, self._cache_db_path) for chunk in chunks]
            worker_fn = _process_phrase_with_cache
        else:
            args_list = [(chunk, chains) for chunk in chunks]
            worker_fn = _scan_phrase_chunk

        try:
            with ProcessPoolExecutor(max_workers=self._max_workers) as executor:
                futures = [executor.submit(worker_fn, args) for args in args_list]
                for future in as_completed(futures):
                    try:
                        chunk_matches = future.result()
                        results["matches"].extend(chunk_matches)
                        results["matches_found"] += len(chunk_matches)
                    except Exception as e:
                        results["errors"].append(f"Worker error: {e}")
        except Exception as e:
            results["errors"].append(f"Multiprocessing failed: {e}")
            results["errors"].append("Falling back to single-process mode...")
            cache = RainbowCache(self._cache_db_path) if self._enable_rainbow_cache else None
            for phrase in phrases:
                mnemonic = None
                if cache:
                    mnemonic = cache.get(phrase)
                if mnemonic is None:
                    mnemonic = _passphrase_to_mnemonic(phrase)
                    if cache and mnemonic:
                        cache.put(phrase, mnemonic)
                if not mnemonic:
                    continue
                try:
                    derived = SOLVER.solve(mnemonic)
                    for addr_info in derived.addresses:
                        if addr_info["chain"] in chains and addr_info["address"]:
                            results["matches"].append({
                                "passphrase": phrase,
                                "mnemonic": mnemonic,
                                "chain": addr_info["chain"],
                                "address": addr_info["address"],
                            })
                            results["matches_found"] += 1
                except Exception as e:
                    results["errors"].append(f"Phrase '{phrase[:20]}': {e}")
            if cache:
                cache.close()

        return results
