from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, RichLog, Static, Input, Header, Footer

from app.interfaces.tester import IWalletSecurityTester


class WalletSecurityPanel(Screen):

    def __init__(self, tester: IWalletSecurityTester, **kwargs):
        super().__init__(**kwargs)
        self._tester = tester

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"[bold]{self._tester.name()}[/]", id="test-title")
        yield Static(self._tester.description(), id="test-desc")
        yield Input(
            placeholder="Paste BIP39 seed phrase (12+ words)...",
            id="seed-input",
            password=True,
        )
        yield Static("", id="status-label")

        with Vertical(id="test-vector-buttons"):
            for vec in self._tester.available_vectors():
                label = vec.replace("_", " ").title()
                yield Button(f"  {label}  ", id=f"vec-{vec}")

        yield RichLog(id="test-output", highlight=True, markup=True)
        yield Button(" Back ", id="back-btn", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "back-btn":
            self.app.pop_screen()
            return

        if btn_id and btn_id.startswith("vec-"):
            vector = btn_id[4:]
            seed_input = self.query_one("#seed-input", Input)
            seed = seed_input.value.strip()

            if not seed:
                self.query_one("#status-label", Static).update(
                    "[bold red]Please enter a seed phrase first[/]"
                )
                return

            self.query_one("#status-label", Static).update(
                "[bold yellow]Deriving...[/]"
            )

            output = self.query_one("#test-output", RichLog)
            output.clear()

            result = self._tester.execute(vector, seed)

            if result.success:
                output.write(f"[bold green]SUCCESS — {result.chain} derivation complete[/]\n")
                output.write(f"[bold]Address:[/] {result.address}")
                output.write(f"[bold]Private Key (hex):[/] {result.private_key_hex}")
                output.write(f"[bold]Derivation Path:[/] {result.details.get('derivation_path', 'N/A')}")
                output.write(f"[bold]Chain:[/] {result.chain}")
                self.query_one("#status-label", Static).update(
                    "[bold green]Derivation successful[/]"
                )
            else:
                output.write(f"[bold red]FAILED[/] — {result.details.get('error', 'Unknown error')}")
                self.query_one("#status-label", Static).update(
                    "[bold red]Derivation failed[/]"
                )
