import dataclasses
import datetime
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "wallet_configs")

ENV_PLACEHOLDERS = {
    "APPDATA": os.environ.get("APPDATA", ""),
    "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
    "USERPROFILE": os.environ.get("USERPROFILE", ""),
    "HOME": os.environ.get("HOME", ""),
}


def _resolve_path(template: str) -> str:
    result = template
    for key, val in ENV_PLACEHOLDERS.items():
        placeholder = "{{" + key + "}}"
        if placeholder in result:
            result = result.replace(placeholder, val)
    return result.replace("/", os.sep)


def load_wallet_configs() -> dict[str, dict]:
    configs: dict[str, dict] = {}
    if not os.path.isdir(FIXTURES_DIR):
        logger.warning("Wallet configs directory not found: %s", FIXTURES_DIR)
        return configs
    for fname in os.listdir(FIXTURES_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(FIXTURES_DIR, fname)
        try:
            with open(fpath) as f:
                cfg = json.load(f)
            name = cfg.get("wallet", "")
            if name:
                cfg["_source_file"] = fpath
                cfg["_resolved_paths"] = [_resolve_path(p) for p in cfg.get("known_paths", [])]
                configs[name] = cfg
        except Exception as e:
            logger.warning("Failed to load wallet config %s: %s", fname, e)
    return configs


WALLET_CONFIGS: dict[str, dict] = load_wallet_configs()

WALLET_PATTERNS: list[dict] = [
    {"name": name, "patterns": [re.compile(p, re.I) for p in cfg.get("file_patterns", [name.lower()])]}
    for name, cfg in WALLET_CONFIGS.items()
]

EXTRA_PATTERNS = [
    {"name": "Electrum", "patterns": [re.compile(r"electrum", re.I)]},
    {"name": "Bitcoin Core", "patterns": [re.compile(r"wallet\.dat$", re.I)]},
    {"name": "Coinbase Wallet", "patterns": [re.compile(r"coinbase", re.I)]},
    {"name": "Generic Keystore", "patterns": [re.compile(r"keystore", re.I)]},
    {"name": "Atomic Wallet", "patterns": [re.compile(r"\.wallet$", re.I)]},
    {"name": "Unknown Wallet JSON", "patterns": [re.compile(r"wallet", re.I)]},
    {"name": "Gnosis Safe", "patterns": [re.compile(r"safe-config\.json", re.I), re.compile(r"gnosis.safe", re.I), re.compile(r"safe.multisig", re.I)]},
    {"name": "Argent", "patterns": [re.compile(r"argent", re.I)]},
    {"name": "Web3Auth", "patterns": [re.compile(r"torus", re.I), re.compile(r"web3auth", re.I)]},
    {"name": "ZenGo", "patterns": [re.compile(r"zengo", re.I)]},
]

ALL_PATTERNS = WALLET_PATTERNS + EXTRA_PATTERNS

_COMBINED_PATTERN = re.compile(
    "|".join(f"({p.pattern})" for wp in ALL_PATTERNS for p in wp["patterns"]),
    re.I,
)
_PATTERN_INDEX_TO_NAME = [
    wp["name"] for wp in ALL_PATTERNS for _ in wp["patterns"]
]


def _match_pattern_index(basename: str) -> int:
    m = _COMBINED_PATTERN.search(basename)
    if m and m.lastindex:
        return m.lastindex - 1
    return -1

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


@dataclasses.dataclass
class VaultScanResult:
    file_path: str
    wallet_type: str
    file_size_bytes: int
    modified_time: str
    detected_by: str
    wallet_info: dict | None = None


def get_wallet_config(wallet_type: str) -> dict | None:
    return WALLET_CONFIGS.get(wallet_type)


def identify_wallet_type(filename: str) -> str:
    basename = os.path.basename(filename)
    idx = _match_pattern_index(basename)
    if 0 <= idx < len(_PATTERN_INDEX_TO_NAME):
        return _PATTERN_INDEX_TO_NAME[idx]
    return "Unknown"


def _scan_directory_sync(directory: str, max_depth: int = 3) -> list[VaultScanResult]:
    results: list[VaultScanResult] = []
    root_depth = directory.rstrip(os.sep).count(os.sep)

    for dirpath, _dirnames, filenames in os.walk(directory):
        depth = dirpath.count(os.sep) - root_depth
        if depth > max_depth:
            continue
        for fname in filenames:
            if fname.startswith("."):
                continue
            full_path = os.path.join(dirpath, fname)
            try:
                stat_res = os.stat(full_path)
            except OSError:
                continue
            if stat_res.st_size > MAX_FILE_SIZE_BYTES or stat_res.st_size == 0:
                continue
            identified = identify_wallet_type(fname)
            if identified == "Unknown":
                continue
            mtime = datetime.datetime.fromtimestamp(stat_res.st_mtime, tz=datetime.timezone.utc).isoformat()
            results.append(VaultScanResult(
                file_path=full_path,
                wallet_type=identified,
                file_size_bytes=stat_res.st_size,
                modified_time=mtime,
                detected_by="filename_pattern",
                wallet_info=get_wallet_config(identified),
            ))
            if len(results) >= 10000:
                return results
    return results


def scan_directory(directory: str, max_depth: int = 3) -> list[VaultScanResult]:
    return _scan_directory_sync(directory, max_depth)


def scan_known_wallet_directories() -> list[VaultScanResult]:
    results: list[VaultScanResult] = []
    scanned: set[str] = set()

    dirs_to_scan = []
    for cfg in WALLET_CONFIGS.values():
        for resolved in cfg.get("_resolved_paths", []):
            if resolved in scanned:
                continue
            scanned.add(resolved)
            dirs_to_scan.append(resolved)

    with ThreadPoolExecutor(max_workers=min(8, len(dirs_to_scan) or 1)) as executor:
        futures = {executor.submit(_scan_directory_sync, d, 2): d for d in dirs_to_scan}
        for future in as_completed(futures):
            try:
                chunk = future.result()
                results.extend(chunk)
            except Exception as e:
                logger.debug("Directory scan failed: %s", e)

    unique: dict[str, VaultScanResult] = {}
    for r in results:
        if r.file_path not in unique:
            unique[r.file_path] = r

    return list(unique.values())


BROWSER_STORAGE_PATTERNS = [
    re.compile(r"(metamask|phantom|coinbase).*\.(ldb|log)$", re.I),
    re.compile(r"(chrome|moz-extension).*\.sqlite$", re.I),
]

MNEMONIC_RE = re.compile(r"\b([a-z]{3,9}\s+){11,23}[a-z]{3,9}\b")
PRIVATE_KEY_RE = re.compile(r"\b0x[a-fA-F0-9]{64}\b")
WIF_RE = re.compile(r"\b[5KL][1-9A-HJ-NP-Za-km-z]{51}\b")


def _scan_content_sync(directory: str, max_depth: int = 3) -> list[VaultScanResult]:
    results: list[VaultScanResult] = []
    root_depth = directory.rstrip(os.sep).count(os.sep)
    for dirpath, _dirnames, filenames in os.walk(directory):
        depth = dirpath.count(os.sep) - root_depth
        if depth > max_depth:
            continue
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            try:
                stat_res = os.stat(full_path)
            except OSError:
                continue
            if stat_res.st_size > MAX_FILE_SIZE_BYTES or stat_res.st_size == 0:
                continue
            try:
                with open(full_path, "rb") as f:
                    head = f.read(4096)
                text = head.decode("utf-8", errors="replace")
                mnemonic_match = MNEMONIC_RE.search(text)
                pk_match = PRIVATE_KEY_RE.search(text)
                wif_match = WIF_RE.search(text)
                if mnemonic_match or pk_match or wif_match:
                    detected = "Content:"
                    if mnemonic_match:
                        detected += "Mnemonic/"
                    if pk_match:
                        detected += "HexKey/"
                    if wif_match:
                        detected += "WIF/"
                    detected = detected.rstrip("/")
                    mtime = datetime.datetime.fromtimestamp(stat_res.st_mtime, tz=datetime.timezone.utc).isoformat()
                    results.append(VaultScanResult(
                        file_path=full_path,
                        wallet_type=detected,
                        file_size_bytes=stat_res.st_size,
                        modified_time=mtime,
                        detected_by="content_scan",
                    ))
            except Exception:
                continue
    return results


def scan_directory_content(directory: str, max_depth: int = 3) -> list[VaultScanResult]:
    return _scan_content_sync(directory, max_depth)
