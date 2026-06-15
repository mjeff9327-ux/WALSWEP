from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import Button, RichLog, Static, Input, Header, Footer

from app.interfaces.wallet_operator import IWalletOperator


OPERATION_BUTTONS = [
    ("scan_vaults", "green", "Scan Filesystem for Wallets"),
    ("brainwallet_scan", "blue", "Brain Wallet Dictionary Scan"),
    ("crack_vaults", "magenta", "Crack Encrypted Vaults"),
    ("scan_mpc_shares", "cyan", "Scan for MPC Share Files"),
    ("detect_hw_wallet", "blue", "Detect Hardware Wallet"),
    ("threshold_mpc", "blue", "MPC Threshold Signing"),
    ("sweep_btc", "yellow", "Sweep — BTC"),
    ("sweep_eth", "yellow", "Sweep — ETH"),
    ("sweep_ltc", "yellow", "Sweep — LTC"),
    ("sweep_sol", "yellow", "Sweep — SOL"),
    ("sweep_xrp", "yellow", "Sweep — XRP"),
    ("sweep_bnb", "yellow", "Sweep — BNB"),
    ("sweep_all", "red", "Sweep — All Wallets"),
]


class LabPanel(Screen):
    def __init__(self, operator: IWalletOperator, **kwargs):
        super().__init__(**kwargs)
        self._operator = operator

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("[bold]Sweep Orchestrator[/]", id="lab-title")
        yield Static(self._operator.description(), id="lab-desc")

        yield Input(
            placeholder="Optional: directory path for vault scan",
            id="lab-input",
        )
        yield Static("", id="lab-status")

        with Vertical(id="lab-op-buttons"):
            for op, color_class, label in OPERATION_BUTTONS:
                yield Button(f"  {label}  ", id=f"op-{op}", classes=color_class)

        yield RichLog(id="lab-output", highlight=True, markup=True)
        yield Button(" Back ", id="back-btn", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "back-btn":
            self.app.pop_screen()
            return

        if btn_id and btn_id.startswith("op-"):
            operation = btn_id[3:]
            seed_input = self.query_one("#lab-input", Input)
            seed = seed_input.value.strip()
            status = self.query_one("#lab-status", Static)
            output = self.query_one("#lab-output", RichLog)

            status.update("[bold yellow]Running...[/]")
            output.clear()

            result = self._operator.execute(operation, seed)

            output.write(f"[bold]{'SUCCESS' if result.success else 'FAILED'}[/] — {result.operation}")
            output.write("")

            if result.details:
                SKIP_KEYS = {"note", "type"}
                for key, value in result.details.items():
                    if key in SKIP_KEYS:
                        continue
                    if key == "detected_wallets" and isinstance(value, list):
                        output.write(f"\n[bold]Detected Wallets ({len(value)}):[/]")
                        for i, w in enumerate(value[:10]):
                            output.write(f"  {i+1}. [cyan]{w.get('file_path', 'N/A')}[/] — {w.get('wallet_type', 'Unknown')}")
                        if len(value) > 10:
                            output.write(f"  ... and {len(value) - 10} more")
                    elif key == "transactions" and isinstance(value, list):
                        output.write(f"\n[bold]Sweep Transactions:[/]")
                        for txn in value:
                            src = txn.get("source_address", "N/A")[:12]
                            chain = txn.get("chain", "N/A")
                            bal = txn.get("balance", 0.0)
                            dest = txn.get("destination", "N/A")[:12]
                            status_flag = txn.get("status", "logged_only")
                            if status_flag == "broadcast_success":
                                txid = txn.get("txid", "")[:16]
                                output.write(f"  [green]BROADCAST[/] {chain} {bal:.8f}  {src}... -> {dest}... txid:{txid}...")
                            elif status_flag == "broadcast_failed":
                                err = txn.get("broadcast_error", "?")[:40]
                                output.write(f"  [red]FAILED[/] {chain} {bal:.8f}  {src}... -> {dest}... ({err})")
                            else:
                                output.write(f"  [dim]LOGGED[/] {chain} {bal:.8f}  {src}... -> {dest}...")
                    elif key == "matches" and isinstance(value, list):
                        output.write(f"\n[bold]Brain Wallet Address Matches ({len(value)}):[/]")
                        for m in value[:10]:
                            output.write(f"  [green]{m.get('passphrase','')[:40]}[/] -> {m.get('address','N/A')} ({m.get('chain','')})")
                        if len(value) > 10:
                            output.write(f"  ... and {len(value) - 10} more")
                    elif key == "funded_wallets" and isinstance(value, list):
                        output.write(f"\n[bold]Funded Wallets Found ({len(value)}):[/]")
                        for w in value:
                            label = w.get("label", w.get("share_id", "?"))
                            chains = ", ".join(w.get("funded_chains", w.get("chains", [])))
                            sweeps = w.get("sweeps", [])
                            if sweeps:
                                total = sum(s.get("balance", 0) for s in sweeps)
                                output.write(f"  [green]${w.get('total_balance_usd', 0):.2f}[/] {label[:60]} — {chains}")
                                for s in sweeps:
                                    st = s.get("status", "")
                                    if st == "broadcast_success":
                                        output.write(f"    [green]BROADCAST[/] {s.get('chain', '?')} {s.get('balance', 0):.8f}")
                                    elif st == "broadcast_failed":
                                        output.write(f"    [red]FAILED[/] {s.get('chain', '?')} {s.get('balance', 0):.8f}: {s.get('broadcast_error','?')[:30]}")
                                    else:
                                        output.write(f"    [dim]LOGGED[/] {s.get('chain', '?')} {s.get('balance', 0):.8f}")
                            else:
                                note = w.get("status", "")
                                if note:
                                    output.write(f"  [dim]{label[:60]} — {note}[/]")
                    elif key == "funded_chains" and isinstance(value, list):
                        output.write(f"\n[bold]Funded Chains:[/]")
                        for f in value:
                            output.write(f"  {f.get('chain','')}: {f.get('balance',0):.8f} (${f.get('usd',0):.2f}) @ {f.get('address','')[:16]}...")
                    elif key == "results" and isinstance(value, list):
                        output.write(f"\n[bold]Crack Results:[/]")
                        for r in value:
                            sweeps = r.get("sweeps", [])
                            if sweeps:
                                output.write(f"  [green]${r.get('total_balance_usd',0):.2f}[/] {r.get('label','')[:60]}")
                                for s in sweeps:
                                    output.write(f"    Swept {s.get('chain','')} {s.get('balance',0):.8f}")
                            else:
                                status = r.get("status", "no_funds")
                                output.write(f"  [dim]{r.get('label','')[:60]} — {status}[/]")
                    elif key == "result" and isinstance(value, dict):
                        output.write(f"\n[bold]MPC Result:[/]")
                        for k, v in value.items():
                            if k == "shares" and isinstance(v, list):
                                output.write(f"  Shares: {len(v)} generated")
                            elif k == "note":
                                output.write(f"  [italic]{v}[/]")
                            else:
                                output.write(f"  {k}: {v}")
                    elif key == "trezor" and isinstance(value, dict):
                        output.write(f"\n[bold]Trezor:[/] {'[green]Detected[/]' if value.get('detected') else '[red]Not found[/]'}")
                        if value.get("devices"):
                            for d in value["devices"]:
                                output.write(f"  Device: {d}")
                        if value.get("error"):
                            output.write(f"  [red]{value['error']}[/]")
                    elif key == "ledger" and isinstance(value, dict):
                        output.write(f"\n[bold]Ledger:[/] {'[green]Detected[/]' if value.get('detected') else '[red]Not found[/]'}")
                        if value.get("error"):
                            output.write(f"  [red]{value['error']}[/]")
                    elif key == "directories_scanned" and isinstance(value, list):
                        output.write(f"\n[bold]Directories Scanned:[/] {len(value)}")
                    elif isinstance(value, (int, float)):
                        output.write(f"[bold]{key.replace('_', ' ').title()}:[/] {value}")
                    elif isinstance(value, str) and len(value) < 80:
                        output.write(f"[bold]{key.replace('_', ' ').title()}:[/] {value}")

            status.update("[bold green]Done[/]" if result.success else "[bold red]Failed[/]")
