import json
import logging
import os
import re
from typing import Optional

from app.engine.mpc_crypto import MpcOperator

logger = logging.getLogger(__name__)

SHARE_PATTERNS = [
    re.compile(r"share.*\.json", re.I),
    re.compile(r"\.share$", re.I),
    re.compile(r"\.sss$", re.I),
    re.compile(r"slip.*0039.*\.txt", re.I),
    re.compile(r"mnemonic.*share", re.I),
]


class ShareScanner:
    def __init__(self, mpc: Optional[MpcOperator] = None):
        self._mpc = mpc or MpcOperator()

    def scan_directories(self, directories: list[str], max_depth: int = 3) -> dict:
        found_shares: dict[str, list[dict]] = {}
        errors = []

        for directory in directories:
            if not os.path.isdir(directory):
                continue
            root_depth = directory.rstrip(os.sep).count(os.sep)
            for dirpath, _dirnames, filenames in os.walk(directory):
                depth = dirpath.count(os.sep) - root_depth
                if depth > max_depth:
                    continue
                for fname in filenames:
                    if any(p.search(fname) for p in SHARE_PATTERNS):
                        full_path = os.path.join(dirpath, fname)
                        parsed = self._parse_share_file(full_path)
                        if parsed:
                            share_id = parsed.get("share_id", "unknown")
                            if share_id not in found_shares:
                                found_shares[share_id] = []
                            found_shares[share_id].append(parsed)

        results = []
        for share_id, shares in found_shares.items():
            entry = {
                "share_id": share_id,
                "total_shares": len(shares),
                "shares": shares,
            }
            threshold = self._detect_threshold(shares)
            if threshold and len(shares) >= threshold:
                try:
                    tuples = [(s["index"], s["value"]) for s in shares[:threshold]]
                    secret = self._mpc.reconstruct_secret(tuples)
                    entry["reconstructed"] = True
                    entry["secret_hex"] = hex(secret)
                except Exception as e:
                    entry["reconstructed"] = False
                    entry["reconstruct_error"] = str(e)
            else:
                entry["reconstructed"] = False
                entry["needed"] = threshold - len(shares) if threshold else "unknown"
            results.append(entry)

        return {
            "directories_scanned": directories,
            "share_groups_found": len(results),
            "groups": results,
            "errors": errors,
            "total_share_files": sum(len(g["shares"]) for g in results),
        }

    def _parse_share_file(self, path: str) -> Optional[dict]:
        try:
            with open(path) as f:
                content = f.read().strip()
        except Exception as e:
            logger.debug("Cannot read %s: %s", path, e)
            return None

        try:
            data = json.loads(content)
            if isinstance(data, dict) and "value" in data:
                return {
                    "file": path,
                    "share_id": data.get("share_id", data.get("id", os.path.basename(path))),
                    "index": int(data.get("id", data.get("index", 0))),
                    "value": int(str(data["value"]), 0),
                }
            if isinstance(data, list):
                items = []
                for item in data:
                    if isinstance(item, dict) and "value" in item:
                        items.append({
                            "file": path,
                            "share_id": item.get("share_id", item.get("id", os.path.basename(path))),
                            "index": int(item.get("id", item.get("index", 0))),
                            "value": int(str(item["value"]), 0),
                        })
                return items[0] if items else None
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        match = re.match(r"(\d+)[:\s,]+(\d+)", content)
        if match:
            return {
                "file": path,
                "share_id": os.path.basename(path),
                "index": int(match.group(1)),
                "value": int(match.group(2)),
            }

        return None

    def _detect_threshold(self, shares: list[dict]) -> Optional[int]:
        for s in shares:
            if "threshold" in s:
                return int(s["threshold"])
        if len(shares) >= 2:
            return len(shares)
        return None
