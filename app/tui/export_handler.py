import json
import os
import time


class ExportHandler:
    def __init__(self, export_dir: str = "exports"):
        self._export_dir = export_dir

    def export_wallets(self, wallets: list[dict], seed_label: str = "") -> str:
        os.makedirs(self._export_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{self._export_dir}/found_wallets_{ts}.json"
        data = {
            "exported_at": time.time(),
            "seed": seed_label,
            "wallets": wallets,
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        return filename

    def export_all(self, found_wallets: list[dict]) -> str:
        os.makedirs(self._export_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{self._export_dir}/all_results_{ts}.json"
        data = {
            "exported_at": time.time(),
            "total_found": len(found_wallets),
            "wallets": found_wallets,
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        return filename
