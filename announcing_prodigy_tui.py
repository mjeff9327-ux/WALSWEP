import asyncio
import ctypes
import io
import json
import os
import sys
import threading
import time
from collections import deque

from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.console import Console, Group
from rich.columns import Columns
from rich import box

from app.implementations.bip39_solver import Bip39Solver
from app.implementations.live_node_client import LiveNodeClient

CHAINS = ["BTC", "ETH", "LTC", "SOL", "BNB", "XRP", "TRON", "POLYGON"]
CCOLORS = {"BTC":"yellow","ETH":"cyan","LTC":"white","SOL":"magenta","BNB":"yellow","XRP":"blue","TRON":"red","POLYGON":"magenta"}
ACTIONS = ["search","stop","export"]
NFO = len(CHAINS) + len(ACTIONS)


def _vt():
    if sys.platform == "win32":
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        m = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(m)):
            k.SetConsoleMode(h, m.value | 0x0004)


_buf = io.StringIO()
_con = Console(file=_buf, force_terminal=True, legacy_windows=False, color_system="truecolor", width=80)


def _rr(r):
    _buf.truncate(0); _buf.seek(0); _con.print(r)
    return _buf.getvalue()


def _trim(m, mw=5):
    w = m.split()
    return m if len(w) <= mw else " ".join(w[:mw]) + "..."


class ProdigyTUI:
    def __init__(self):
        self._running = False
        self._on = {c: True for c in CHAINS}
        self._st = {"scanned": 0, "found": 0}
        self._found = []
        self._checked = deque(maxlen=20)
        self._scan = False
        self._th = None
        self._foc = 0
        self._solver = Bip39Solver()
        self._node = LiveNodeClient(timeout=15.0)

    def _banner(self):
        return Panel(
            Align.center(Text("Announcing Prodigy-TUI: Terminal User base interface Prodigy", style="bold cyan")),
            box=box.ASCII, border_style="cyan", padding=(0, 1),
        )

    def _chain_row(self):
        r = Text("  ")
        for i, c in enumerate(CHAINS):
            if i: r.append("  ")
            f = self._foc == i
            co = CCOLORS.get(c, "white")
            a = self._on.get(c, True)
            if f and a:
                r.append(f" >{c}< ", style=f"bold white on {co} reverse")
            elif f and not a:
                r.append(f" >{c}< ", style=f"dim white on grey23 reverse")
            elif a:
                r.append(f" [{i+1}] {c} ", style=f"bold white on {co}")
            else:
                r.append(f" [{i+1}] {c} ", style=f"dim white on grey23")
        r.append("  ")
        return Panel(Align.center(r), box=box.ASCII, border_style="bright_blue", padding=(0, 1))

    def _checked_panel(self):
        if not self._checked:
            return Panel(Text(" waiting...", style="dim"), box=box.ASCII, title="[bold cyan]Checked", border_style="cyan", padding=(1, 2))
        lines = [Text(f" {i:>2}. {_trim(m)}", style="cyan") for i, m in enumerate(reversed(self._checked), 1)]
        return Panel(Group(*lines), box=box.ASCII, title="[bold cyan]Checked", border_style="cyan", padding=(1, 2))

    def _found_panel(self):
        fc = self._st["found"]
        if not self._found:
            return Panel(
                Group(Text(f"\n Found: {fc}", style="bold green", justify="center"), Text(" (wallets will appear here)", style="dim green", justify="center")),
                box=box.ASCII, title="[bold green]Found Assets", border_style="green", padding=(1, 2),
            )
        lines = [Text(f" Found: {fc}", style="bold green", justify="center")]
        for w in list(reversed(self._found))[:12]:
            ch, ad, b = w["chain"], w["address"], w["balance"]
            usd = w.get("usd", 0)
            ad = f"{ad[:6]}...{ad[-4:]}" if len(ad) > 12 else ad
            label = f" {ch} {ad}  {b:.8f}"
            if usd:
                label += f" (${usd:.2f})"
            lines.append(Text(label, style=f"bold {CCOLORS.get(ch,'white')}"))
        if len(self._found) > 12:
            lines.append(Text(f" ... and {len(self._found)-12} more", style="dim"))
        return Panel(Group(*lines), box=box.ASCII, title="[bold green]Found Assets", border_style="green", padding=(1, 2))

    def _action_row(self):
        r = Text()
        for i, a in enumerate(ACTIONS):
            if i: r.append("  ")
            f = self._foc == len(CHAINS) + i
            k = {"search":"SPACE", "stop":"SPACE", "export":"E"}[a]
            if a == "search":
                s = "dim white on grey23" if self._scan else "bold white on grey"
                if f: s = "bold white on yellow reverse"
                r.append(f" [{k}] SEARCH ", style=s)
            elif a == "stop":
                s = "bold white on red" if self._scan else "dim white on grey23"
                if f: s = "bold white on red reverse" if self._scan else "bold white on grey23 reverse"
                r.append(f" [{k}] STOP ", style=s)
            else:
                s = "bold white on blue"
                if f: s = "bold white on blue reverse"
                r.append(f" [{k}] EXPORT ", style=s)
        return Panel(Align.center(r), box=box.ASCII, border_style="bright_blue", padding=(0, 1))

    def _screen(self):
        return [self._banner(), self._chain_row(), Columns([self._checked_panel(), self._found_panel()], equal=True, expand=True), self._action_row()]

    def _draw(self):
        o = "\033[2J\033[H"
        for s in self._screen(): o += _rr(s)
        sys.stdout.write(o); sys.stdout.flush()

    async def _check_one(self, mnemonic):
        derived = self._solver.solve(mnemonic)
        for addr_info in derived.addresses:
            chain = addr_info["chain"]
            if not self._on.get(chain, True):
                continue
            address = addr_info["address"]
            if not address:
                continue
            try:
                balance = await self._node.query_balance(address, chain)
                if balance.confirmed > 0:
                    self._found.append({
                        "chain": chain,
                        "address": address,
                        "balance": balance.confirmed,
                        "usd": balance.usd_value or 0,
                        "mnemonic": mnemonic,
                    })
                    self._st["found"] += 1
            except Exception as e:
                logger.error("Balance check failed for %s on %s: %s",
                             mnemonic[:20], chain, e)

    def _scan_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while self._scan:
                mnemonic = self._solver.generate_mnemonic()
                self._st["scanned"] += 1
                self._checked.append(mnemonic)
                loop.run_until_complete(self._check_one(mnemonic))
        finally:
            loop.run_until_complete(self._node.close())
            loop.close()

    def _toggle(self):
        if self._scan:
            self._scan = False
        else:
            self._scan = True
            self._th = threading.Thread(target=self._scan_loop, daemon=True)
            self._th.start()

    def _export(self):
        if not self._found: return
        os.makedirs("exports", exist_ok=True)
        p = f"exports/all_{time.strftime('%Y%m%d_%H%M%S')}.json"
        with open(p, "w") as f:
            json.dump({"exported_at": time.time(), "total": len(self._found), "wallets": self._found}, f, indent=2)

    def _click(self):
        i = self._foc
        if i < len(CHAINS):
            self._on[CHAINS[i]] = not self._on[CHAINS[i]]
        else:
            a = ACTIONS[i - len(CHAINS)]
            if a in ("search", "stop"):
                self._toggle()
            elif a == "export":
                self._export()

    def run(self):
        _vt()
        self._running = True
        import msvcrt
        while self._running:
            if msvcrt.kbhit():
                k = msvcrt.getch()
                try:
                    d = k.decode()
                except UnicodeDecodeError:
                    d = ""
                if d == "\t":
                    self._foc = (self._foc + 1) % NFO
                elif d in ("\r", " "):
                    self._click()
                elif d.lower() == "e":
                    self._export()
                elif d.lower() == "q":
                    self._running = False
                elif d in "12345678":
                    idx = ord(d) - 49
                    if 0 <= idx < len(CHAINS):
                        self._on[CHAINS[idx]] = not self._on[CHAINS[idx]]
                elif ord(k) == 27:
                    self._running = False
            self._draw()
            time.sleep(0.1)
        self._scan = False


if __name__ == "__main__":
    ProdigyTUI().run()
