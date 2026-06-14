import os
from rich.panel import Panel
from rich.text import Text


class SeedInputPanel:
    def __init__(self):
        self._seeds: list[str] = []
        self._active = False

    @property
    def has_seeds(self) -> bool:
        return len(self._seeds) > 0

    @property
    def seeds(self) -> list[str]:
        return list(self._seeds)

    def load_from_text(self, text: str) -> int:
        lines = text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and len(line.split()) >= 12:
                self._seeds.append(line)
        return len(self._seeds)

    def load_from_file(self, path: str) -> int:
        if not os.path.exists(path):
            return 0
        with open(path) as f:
            return self.load_from_text(f.read())

    def toggle_mode(self) -> None:
        self._active = not self._active

    def clear(self) -> None:
        self._seeds.clear()

    def render(self) -> Panel:
        if self._active:
            count = len(self._seeds)
            return Panel(
                Text(f"Bulk mode active\n{count} seed phrases loaded\n\nPaste seeds into seeds.txt\nor use 'L' to load file", style="bold yellow"),
                title="[bold yellow]Seed Input",
                border_style="yellow",
            )
        return Panel(
            Text("Auto-generate mode\n(random BIP39 mnemonics)", style="dim"),
            title="[bold]Seed Input",
            border_style="blue",
        )
