import asyncio
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from app.implementations.bip39_solver import Bip39Solver
from app.implementations.live_node_client import LiveNodeClient
from app.implementations.bip39_key_store import Bip39KeyStore
from app.implementations.recording_webhook_client import WebhookClient
from app.implementations.transaction_signer import TransactionSigner
from app.implementations.license_verifier import ProductionLicenseVerifier
from app.implementations.telegram_notifier import TelegramNotifier
from app.implementations.license_validator import TelegramLicenseValidator
from app.engine.derivation import WalletDeriver
from app.engine.orchestrator import SweepOrchestrator

from app.components.event_bus import EventBus
from app.components.scan_engine import ScanEngine
from app.components.license_service import LicenseService
from app.components.mempool_monitor import MempoolMonitor
from app.components.funding_detector import FundingDetector
from app.components.bitcoin_auto_withdraw_bot import AutoSweepBot
from app.components.config_manager import ConfigManager
from app.components.token_scanner import TokenScanner

from app.storage.sqlite_storage import SqliteStorage
from textual_web import run_textual_ui

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


_background_loop: asyncio.AbstractEventLoop | None = None
_background_thread: threading.Thread | None = None


def _start_background_loop() -> asyncio.AbstractEventLoop:
    global _background_loop, _background_thread
    if _background_loop is not None and _background_loop.is_running():
        return _background_loop
    _background_loop = asyncio.new_event_loop()

    def _run_forever():
        asyncio.set_event_loop(_background_loop)
        _background_loop.run_forever()

    _background_thread = threading.Thread(target=_run_forever, daemon=True)
    _background_thread.start()
    return _background_loop


def run_async(coro):
    loop = _start_background_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop)


def main() -> None:
    logger.info("Starting Prodigy-TUI v2.0")
    _start_background_loop()

    config = ConfigManager()
    event_bus = EventBus()
    solver = Bip39Solver()
    key_store = Bip39KeyStore()
    api_keys = {
        "etherscan": config.get("api_keys", "etherscan", ""),
        "bscscan": config.get("api_keys", "bscscan", ""),
        "polygonscan": config.get("api_keys", "polygonscan", ""),
    }
    node_client = LiveNodeClient(api_keys=api_keys, timeout=15.0)
    webhook_client = WebhookClient(
        target_url=config.get("webhook", "target_url", ""),
        output_dir="events",
    )
    telegram_notifier = TelegramNotifier(
        bot_token=config.get("telegram", "bot_token", ""),
        chat_id=config.get("telegram", "chat_id", ""),
        enabled=config.telegram_enabled,
    )
    signer = TransactionSigner()

    wallet_operator = WalletDeriver(config=config)
    sweep_orchestrator = SweepOrchestrator(config=config, node_client=node_client)

    license_verifier = ProductionLicenseVerifier(
        secret_key=config.get("license", "secret_key", ""),
    )
    telegram_license = TelegramLicenseValidator(
        bot_token=config.get("license", "telegram_bot_token", ""),
        enabled=config.get("license", "enabled", False),
        secret_key=config.get("license", "secret_key", ""),
    )

    storage = SqliteStorage()
    storage.initialize()

    token_scanner = TokenScanner(node_client=node_client, config=config)

    scan_engine = ScanEngine(
        solver=solver,
        key_store=key_store,
        node_client=node_client,
        webhook_client=webhook_client,
        event_bus=event_bus,
        config=config,
        token_scanner=token_scanner,
        chains=["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"],
    )

    license_service = LicenseService(
        verifier=license_verifier,
        secret_key=config.get("license", "secret_key", ""),
    )
    mempool_monitor = MempoolMonitor(node_client=node_client)
    funding_detector = FundingDetector(mempool_monitor=mempool_monitor)
    sweep_bot = AutoSweepBot(
        node_client=node_client,
        key_store=key_store,
        signer=signer,
        event_bus=event_bus,
        config=config,
    )

    async def _on_found(data: dict) -> None:
        pattern = data.get("pattern", "")
        balances = data.get("balances", [])
        if not pattern or not balances:
            return
        for chain_name, bal_amount in balances:
            try:
                proposal = await sweep_bot.evaluate(pattern, chain_name)
                if proposal:
                    logger.info("Auto-sweep triggered for %s on %s (%.8f)", pattern[:20], chain_name, bal_amount)
            except Exception as e:
                logger.debug("Sweep eval failed for %s on %s: %s", pattern[:20], chain_name, e)

    def _on_found_sync(data: dict) -> None:
        run_async(_on_found(data))

    event_bus.subscribe("FOUND", _on_found_sync)

    async def _on_funding(data: dict) -> None:
        addr = data.get("address", "")
        chain = data.get("chain", "")
        amount = data.get("amount", 0)
        if addr and amount > 0:
            logger.info("Funding detected on %s: %.8f %s — triggering sweep", addr, amount, chain)

    def _on_funding_sync(data: dict) -> None:
        run_async(_on_funding(data))

    funding_detector.subscribe(_on_funding_sync)

    if telegram_notifier.enabled:
        logger.info("Telegram notifier enabled")

    mempool_interval = config.get("scan", "mempool_poll_interval", 30)
    for chain in ["BTC", "ETH", "SOL"]:
        run_async(mempool_monitor.watch_chain(chain, mempool_interval))

    sweep_interval = config.get("sweep", "check_interval", 30)
    run_async(sweep_bot.run(sweep_interval))

    logger.info("Components initialized")
    logger.info("Starting TUI...")

    auto_start = config.get("api", "auto_start_server", False)
    if auto_start:
        from app.api.gateway import create_app
        import uvicorn
        host = config.get("api", "host", "127.0.0.1")
        port = config.get("api", "port", 8000)

        def _start_api():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            app = loop.run_until_complete(create_app(
                scan_engine, license_service, event_bus,
                config=config, node_client=node_client,
            ))
            uvicorn.run(app, host=host, port=port, log_level="info")

        api_thread = threading.Thread(target=_start_api, daemon=True)
        api_thread.start()
        logger.info("API server started on %s:%d", host, port)

    try:
        run_textual_ui(scan_engine, event_bus, config, solver, wallet_operator=wallet_operator, sweep_orchestrator=sweep_orchestrator)
    finally:
        if _background_loop and _background_loop.is_running():
            _background_loop.call_soon_threadsafe(_background_loop.stop)
        if _background_thread and _background_thread.is_alive():
            _background_thread.join(timeout=3)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)
