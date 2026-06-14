import asyncio
import json
import os
import threading
import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Header, Footer, RichLog, Input, Label, Static

from app.components.scan_engine import ScanEngine
from app.components.event_bus import EventBus
from app.components.license_service import LicenseService
from app.implementations.license_verifier import ProductionLicenseVerifier
from app.tui.export_handler import ExportHandler
from app.tui.wallet_panel import WalletSecurityPanel
from app.components.config_manager import ConfigManager

CHAINS = ["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"]


class LicenseScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Label("Enter License Key to unlock features", id="license-label")
        yield Input(placeholder="Paste license key here...", id="license-input")
        yield Button(" Unlock ", id="unlock-btn", variant="primary")
        yield Button(" Skip (limited mode) ", id="skip-btn", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "unlock-btn":
            key = self.query_one("#license-input", Input).value
            self.dismiss(key)
        elif event.button.id == "skip-btn":
            self.dismiss("")


class ChainButton(Button):
    def __init__(self, label: str, chain: str, **kwargs):
        super().__init__(label, **kwargs)
        self.chain = chain
        self.active = True

    def on_click(self) -> None:
        self.active = not self.active
        self.classes = "active" if self.active else "inactive"
        self.app.update_chain_state(self.chain, self.active)


class ProdigyTextualUI(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    LicenseScreen {
        align: center middle;
    }

    #license-label {
        text-align: center;
        margin-bottom: 1;
        text-style: bold;
    }

    #license-input {
        width: 50;
        margin-bottom: 1;
    }

    #unlock-btn, #skip-btn {
        min-width: 20;
        margin: 0 1;
    }

    #chain-row {
        height: 5;
        align: center middle;
        padding: 0 1;
    }

    ChainButton {
        min-width: 10;
        margin: 0 1;
    }

    ChainButton.active {
        background: #00aa00;
        color: white;
    }

    ChainButton.inactive {
        background: #444444;
        color: #888888;
    }

    #panels-row {
        height: 1fr;
    }

    #checked-panel, #found-panel {
        width: 1fr;
        height: 100%;
        border: solid $primary;
        margin: 0 1;
    }

    #checked-panel {
        margin-right: 0;
    }

    #found-panel {
        margin-left: 0;
    }

    #action-row {
        height: 5;
        align: center middle;
        padding: 0 1;
    }

    #action-row Button {
        min-width: 18;
        margin: 0 1;
    }

    #test-btn {
        background: #885500;
        color: white;
    }

    #back-btn {
        background: #555555;
        color: white;
    }

    #search-btn {
        background: #555555;
        color: white;
    }

    #stop-btn {
        background: #555555;
        color: white;
    }

    #stop-btn.active {
        background: #aa0000;
        color: white;
    }

    #export-btn {
        background: #0055aa;
        color: white;
    }

    #export-btn.disabled {
        background: #333333;
        color: #666666;
    }

    #license-status {
        height: 1;
        text-align: right;
        padding: 0 1;
    }
    """

    scanned = reactive(0)
    found_count = reactive(0)

    def __init__(self, scan_engine: ScanEngine, event_bus: EventBus, config: ConfigManager, solver=None, wallet_tester=None):
        super().__init__()
        self._scan_engine = scan_engine
        self._event_bus = event_bus
        self._config = config
        self._solver = solver
        self._wallet_tester = wallet_tester
        self._licenser = LicenseService(ProductionLicenseVerifier(
            secret_key=config.get("license", "secret_key", ""),
        ))
        self._export_handler = ExportHandler()
        self._licensed = False
        self._features = []
        self._on = {c: True for c in CHAINS}
        self._scanning = False
        self._found = []
        self._checked_mnemonics = []
        self._scan_thread = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("", id="license-status")
        with Horizontal(id="chain-row"):
            for c in CHAINS:
                yield ChainButton(c, chain=c, classes="active", id=f"chain-{c.lower()}")
        with Horizontal(id="panels-row"):
            yield RichLog(id="checked-panel", highlight=True, markup=True)
            yield RichLog(id="found-panel", highlight=True, markup=True)
        with Horizontal(id="action-row"):
            yield Button(" SEARCH ", id="search-btn", variant="default")
            yield Button(" STOP ", id="stop-btn", variant="default")
            yield Button(" EXPORT ", id="export-btn", variant="primary")
            if self._wallet_tester:
                yield Button(" TEST WALLET ", id="test-btn", variant="default")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#checked-panel", RichLog).write("[dim]waiting...[/]")
        self.query_one("#found-panel", RichLog).write("[bold green]Found: 0[/]\n[dim green](wallets will appear here)[/]")
        license_enabled = self._config.get("license", "enabled", False)
        if license_enabled:
            self.push_screen(LicenseScreen(), self._handle_license_result)
        else:
            self._licensed = True
            self._features = ["scan", "multi_chain", "export"]
            self.query_one("#license-status", Label).update("[dim]License: disabled[/]")

    def _handle_license_result(self, key: str) -> None:
        if key:
            entitlement = self._licenser.verify(key)
            if entitlement.valid:
                self._licensed = True
                self._features = entitlement.features or []
                self.query_one("#license-status", Label).update(f"[bold green]Licensed[/]")
                self.query_one("#checked-panel", RichLog).write("[bold green]License validated![/]")
                return
            else:
                self.query_one("#license-status", Label).update("[bold red]Invalid license key[/]")
                self.query_one("#checked-panel", RichLog).write("[bold red]Invalid license key. Features limited.[/]")
        else:
            self.query_one("#license-status", Label).update("[yellow]License skipped - limited mode[/]")
            self.query_one("#checked-panel", RichLog).write("[yellow]Running in limited mode (export locked)[/]")

        self._licensed = False
        self._features = []
        self._update_export_button()

    def _update_export_button(self) -> None:
        btn = self.query_one("#export-btn", Button)
        if not self._can("export"):
            btn.classes = "disabled"
            btn.disabled = True

    def _can(self, feature: str) -> bool:
        return feature in self._features

    def update_chain_state(self, chain: str, active: bool) -> None:
        self._on[chain] = active
        self._scan_engine.set_chains([c for c, a in self._on.items() if a])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "search-btn":
            if not self._licensed or self._can("scan"):
                self._toggle_scan()
            else:
                self.query_one("#checked-panel", RichLog).write("[bold red]License required for scanning[/]")
        elif btn_id == "stop-btn":
            if self._scanning:
                self._stop_scan()
        elif btn_id == "test-btn":
            if self._wallet_tester:
                self.push_screen(WalletSecurityPanel(tester=self._wallet_tester))
        elif btn_id == "export-btn":
            if self._licensed and self._can("export"):
                self._export()
            else:
                self.query_one("#found-panel", RichLog).write("[bold red]License required for export[/]")

    def _toggle_scan(self) -> None:
        if self._scanning:
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self) -> None:
        self._scanning = True
        self.query_one("#search-btn", Button).label = " SCANNING... "
        self.query_one("#stop-btn", Button).classes = "active"
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()

    def _stop_scan(self) -> None:
        self._scanning = False
        self._scan_engine.stop()
        self.query_one("#search-btn", Button).label = " SEARCH "
        self.query_one("#stop-btn", Button).classes = ""

    def _scan_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from app.implementations.bip39_solver import Bip39Solver
            solver = self._solver or Bip39Solver()
            while self._scanning:
                mnemonic = solver.generate_mnemonic()
                self.call_from_thread(self._add_checked, mnemonic)
                result = loop.run_until_complete(self._scan_engine.scan_single(mnemonic))
                if result.found:
                    for bal in result.balances:
                        chain = bal["chain"]
                        b = bal["balance"]
                        if b.confirmed > 0:
                            data = {
                                "chain": chain,
                                "address": bal["address"],
                                "balance": b.confirmed,
                                "usd": b.usd_value or 0,
                                "mnemonic": mnemonic,
                            }
                            self._found.append(data)
                            self.call_from_thread(self._add_found, chain, bal["address"], b.confirmed, b.usd_value or 0)
        finally:
            self._scanning = False

    def _add_checked(self, mnemonic: str) -> None:
        words = mnemonic.split()
        label = mnemonic if len(words) <= 5 else " ".join(words[:5]) + "..."
        self._checked_mnemonics.append(label)
        log = self.query_one("#checked-panel", RichLog)
        if len(self._checked_mnemonics) == 1:
            log.clear()
        log.write(f"[cyan]{len(self._checked_mnemonics):>2}. {label}[/]")
        self.scanned += 1

    def _add_found(self, chain: str, address: str, balance: float, usd: float) -> None:
        short_addr = f"{address[:6]}...{address[-4:]}" if len(address) > 12 else address
        label = f"{chain} {short_addr}  {balance:.8f}"
        if usd:
            label += f" (${usd:.2f})"
        log = self.query_one("#found-panel", RichLog)
        if self.found_count == 0:
            log.clear()
        log.write(f"[bold]{label}[/]")
        self.found_count += 1

    def _export(self) -> None:
        if not self._found:
            self.query_one("#found-panel", RichLog).write("[bold yellow]Nothing to export[/]")
            return
        path = self._export_handler.export_all(self._found)
        self.query_one("#found-panel", RichLog).write(f"[bold green]Exported to {path}[/]")
        self._event_bus.emit("EXPORT", {"path": path, "count": len(self._found)})

    def on_exit(self) -> None:
        self._scanning = False


def run_textual_ui(scan_engine: ScanEngine, event_bus: EventBus, config: ConfigManager, solver=None, wallet_tester=None):
    app = ProdigyTextualUI(
        scan_engine=scan_engine,
        event_bus=event_bus,
        config=config,
        solver=solver,
        wallet_tester=wallet_tester,
    )
    app.run()


if __name__ == "__main__":
    from app.implementations.bip39_solver import Bip39Solver
    _config = ConfigManager()
    _event_bus = EventBus()
    _solver = Bip39Solver()
    _scan_engine = ScanEngine(
        solver=_solver,
        key_store=None,
        node_client=None,
        webhook_client=None,
        event_bus=_event_bus,
        config=_config,
        chains=["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"],
    )
    run_textual_ui(_scan_engine, _event_bus, _config, _solver)
