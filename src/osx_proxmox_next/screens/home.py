"""Home: your Macs as cards with live screens."""

from __future__ import annotations

import logging
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.widget import Widget
from textual.widgets import Static

from .. import host_setup
from ..defaults import default_disk_gb, detect_cpu_cores, detect_memory_mb
from .. import driver_check
from ..services import VmRecord, list_macos_vm_records
from ..services.detection_service import detect_storage_free_gb
from ..services.unattended_service import DriverState, read_driver_state
from ..widgets.live_screen import LiveScreen
from .shutdown import still_running_line
from .vm_actions import VmActionScreen

log = logging.getLogger(__name__)

__all__ = ["HomeScreen", "VmCard", "NewStrip", "status_line"]

CARD_WIDTH = 30
CARD_CHROME = 5          # border, gap under the thumbnail, name line, status line
THUMB_MIN_ROWS = 5
THUMB_MAX_ROWS = 10
GRID_GAP = 2
COMPACT_BELOW = 64
HOME_REFRESH_SECONDS = 10
THUMB_REFRESH_SECONDS = 5
DRIVER_MARK = "\u25cf driver update"


def status_line(record: VmRecord, driver: DriverState | None, now: float | None = None,
                shutdown: str = "") -> tuple[str, str]:
    """(glyph, text) of a Mac's status: `● running`, `○ stopped`, `◐ installing · N min in`, `◐ shutting down`."""
    if shutdown and record.status == "running":
        return "◐", "shutting down"
    if driver is not None and driver.installing:
        started = driver.started_at or (now or time.time())
        minutes = max(0, int(((now or time.time()) - started) // 60))
        return "◐", f"installing · {minutes} min in"
    if record.status == "running":
        return "●", "running"
    return "○", record.status or "stopped"


class VmCard(Widget, can_focus=True):
    """One Mac: thumbnail, name, VM ID and a status line. The focused card is the selected one."""

    def __init__(self, record: VmRecord, driver: DriverState | None, compact: bool, name_width: int = 0,
                 update: bool = False) -> None:
        super().__init__(id=f"vm-{record.vmid}")
        self.record = record
        self.update = update
        self.driver = driver
        self.compact = compact
        self.name_width = name_width
        self.set_class(compact, "compact")

    def compose(self) -> ComposeResult:
        if self.compact:
            yield Static(self._compact_line(), classes="card-status")
            return
        facts = self.record.facts
        yield LiveScreen(self.record.vmid, self.record.status, THUMB_REFRESH_SECONDS,
                         has_gpu=facts.display_on_gpu, classes="thumb")
        yield Static(self._head(), classes="card-head")
        yield Static(self._status(), classes="card-status")

    def _head(self) -> Text:
        pal = self.app.get_css_variables()
        width = max(10, self.size.width - 4) if self.size.width else CARD_WIDTH - 4
        text = Text(self.record.name, style="bold")
        text.append(" " * max(1, width - len(self.record.name) - len(str(self.record.vmid))))
        text.append(str(self.record.vmid), style=pal["muted"])
        return text

    def _tone(self, glyph: str) -> str:
        pal = self.app.get_css_variables()
        return {"\u25cf": pal["success"], "\u25d0": pal["accent"], "\u25cb": pal["muted"]}[glyph]

    def _shutdown(self) -> str:
        return self.app.shutdowns.state(self.record)

    def _facts_tail(self, glyph: str) -> str:
        return "" if glyph == "\u25d0" else f" \u00b7 {self.record.cores} cores \u00b7 {self.record.memory_gb} GB"

    def _status(self) -> Text:
        pal = self.app.get_css_variables()
        glyph, label = status_line(self.record, self.driver, shutdown=self._shutdown())
        text = Text(glyph + " ", style=self._tone(glyph))
        text.append(label, style="" if glyph != "\u25cb" else pal["muted"])
        if self.update:   # the card is narrow: the marker takes the place of the cores and memory
            text.append(" \u00b7 ", style=pal["muted"])
            text.append(DRIVER_MARK, style=pal["accent"])
        else:
            text.append(self._facts_tail(glyph), style=pal["muted"])
        return text

    def _compact_line(self) -> Text:
        """`\u25cf macos-sequoia   101   running \u00b7 8 cores \u00b7 16 GB`: status glyph, name, VM ID, the rest."""
        pal = self.app.get_css_variables()
        glyph, label = status_line(self.record, self.driver, shutdown=self._shutdown())
        text = Text()
        text.append(glyph + " ", style=self._tone(glyph))   # not Text(style=...): the name keeps the text colour
        text.append(self.record.name.ljust(max(self.name_width, len(self.record.name))) + "   ",
                    style="bold" if self.has_focus else "")
        text.append(f"{self.record.vmid:<5}  ", style=pal["muted"])
        text.append(label, style=self._tone(glyph))
        text.append(self._facts_tail(glyph), style=pal["muted"])
        if self.update:
            text.append("   " + DRIVER_MARK, style=pal["accent"])
        return text

    def _repaint_compact(self) -> None:
        if not self.compact:
            return
        try:
            self.query_one(".card-status", Static).update(self._compact_line())
        except Exception:  # noqa: BLE001 - not composed yet
            pass

    def on_focus(self) -> None:
        self._repaint_compact()

    def on_blur(self) -> None:
        self._repaint_compact()

    def update_record(self, record: VmRecord, driver: DriverState | None, update: bool = False) -> None:
        self.record, self.driver, self.update = record, driver, update
        if not self.compact:
            facts = record.facts
            self.query_one(LiveScreen).set_state(record.status, facts.display_on_gpu)
        self.query_one(".card-status", Static).update(self._compact_line() if self.compact else self._status())

    def on_click(self, event) -> None:
        """One click selects the card, a double click opens it."""
        self.focus()
        if getattr(event, "chain", 1) >= 2:  # older Textual has no click chain: Enter opens
            self.screen.action_open()  # type: ignore[attr-defined]


class NewStrip(Widget, can_focus=True):
    """The dashed `+ New Mac` strip with the proposed defaults."""

    def __init__(self) -> None:
        super().__init__(id="new-strip")
        self.proposal = ""
        self.empty = False

    def set_proposal(self, text: str, empty: bool) -> None:
        self.proposal, self.empty = text, empty
        self.set_class(empty, "empty")
        self.refresh(layout=True)

    def render(self) -> Text:
        pal = self.app.get_css_variables()
        width = max(10, self.size.width - 2)
        left = Text("+ ", style=pal["accent"])
        left.append("New Mac", style="bold")
        left.append("  " + self.proposal, style=pal["muted"])
        out = Text()
        if self.empty:
            out.append("Welcome. This host has no Macs yet. Press enter to make your first one.\n", style="bold")
        out.append_text(left)
        out.append(" " * max(1, width - left.cell_len - 1))
        out.append("n", style="bold " + pal["accent"])
        return out

    def on_click(self) -> None:
        self.app.action_new_mac()


class HomeScreen(VmActionScreen):
    CRUMB = ""
    BINDINGS = [
        Binding("escape", "noop", "Stay here (esc never quits)", show=False),
        Binding("left", "move(-1, 0)", "Select", show=False),
        Binding("right,l", "move(1, 0)", "Select", show=False),
        Binding("up,k", "move(0, -1)", "Select", show=False),
        Binding("down,j", "move(0, 1)", "Select", show=False),
        Binding("enter", "open", "Open"),
        Binding("s", "toggle_power", "Start or Stop"),
        Binding("e", "edit", "Edit"),
        Binding("g", "gpu", "Graphics card"),
        Binding("d", "remove", "Remove"),
        Binding("n", "new_mac", "New Mac"),
        Binding("h", "host", "Host check"),
        Binding("r", "refresh", "Refresh"),
        Binding("v", "toggle_view", "Switch cards and list"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.records: list[VmRecord] = []
        self.drivers: dict[int, DriverState] = {}
        self.updates: set[int] = set()   # Macs with a newer driver known (from local files)
        self.compact_override: bool | None = None
        self.loaded = False
        self.resume_text = ""
        self._free: dict[str, int] = {}

    # ── layout ───────────────────────────────────────────────────────

    def compose_body(self) -> ComposeResult:
        with Vertical(id="home"):
            with Horizontal(id="home-head"):
                yield Static("Your Macs", id="home-title")
                yield Static("", id="home-sub")
            yield Vertical(id="cards")
            yield NewStrip()
            yield Static("", id="resume")
            yield Static("", id="stuck")
            yield Static("", id="selected-line")

    def on_mount(self) -> None:
        self.refresh_now()
        self.set_interval(HOME_REFRESH_SECONDS, self.refresh_now)
        self.run_worker(self._check_drivers, thread=True, group="driver-check", exit_on_error=False)

    def _check_drivers(self) -> None:
        """Once a day, in the background: ask which driver releases are newest. Never blocks the screen."""
        try:
            vmids = [r.vmid for r in list_macos_vm_records()]
            if driver_check.refresh_if_due(vmids):
                self.app.call_from_thread(self.refresh_now)
        except Exception:  # noqa: BLE001 - the marker is a courtesy
            log.debug("driver check failed", exc_info=True)

    def on_screen_resume(self) -> None:
        self.refresh_now()
        self.refresh_footer()

    # ── data ─────────────────────────────────────────────────────────

    def refresh_now(self) -> None:
        self.run_worker(self._load, thread=True, exclusive=True, group="home-load", exit_on_error=False)

    def _load(self) -> None:
        listed = True
        try:
            records = list_macos_vm_records()
        except Exception:  # noqa: BLE001
            log.debug("VM list failed", exc_info=True)
            records, listed = [], False
        drivers = {r.vmid: read_driver_state(r.vmid) for r in records}
        try:
            free = detect_storage_free_gb()
        except Exception:  # noqa: BLE001
            free = {}
        resume = self._resume_text(records)
        self.updates = {r.vmid for r in records if driver_check.has_update(r.vmid)}
        try:
            self.app.call_from_thread(self._show, records, drivers, free, resume, listed)
        except Exception:  # noqa: BLE001 - closing
            pass

    @staticmethod
    def _resume_text(records: list[VmRecord]) -> str:
        """The reminder of a graphics card setup that is mid-flow, named by the moment it waits at."""
        try:
            state = host_setup.load_state()
        except Exception:  # noqa: BLE001
            return ""
        if not state or state.get("finished") or not state.get("gpu"):
            return ""
        step = int(state.get("step", 1))
        name = next((r.name for r in records if r.vmid == state.get("vm")), "")
        who = f" for {name}" if name else ""
        moment = "waiting to continue" if step < 5 else ("waiting for the restart" if step == 5 else "ready to finish")
        if step >= 5 and host_setup.rebooted_since_apply(state):   # the host has restarted since the changes: say so
            return f"▸ Restart done. Graphics card setup{who} is ready to finish."
        return f"▸ Graphics card setup{who} is {moment}."

    async def _show(self, records: list[VmRecord], drivers: dict[int, DriverState], free: dict[str, int],
                    resume: str, listed: bool = True) -> None:
        self.app.vm_records = records  # type: ignore[attr-defined]
        if listed:                                         # a failed listing says nothing about which Macs exist
            self.app.shutdowns.keep_only({r.vmid for r in records})
        selected = self.current_record()
        keep = selected.vmid if selected else None
        same = [r.vmid for r in records] == [r.vmid for r in self.records] and self.loaded
        self.records, self.drivers, self._free, self.loaded = records, drivers, free, True
        self.resume_text = resume
        if same and self._compact() == self._built_compact():
            for card in self.query(VmCard):
                rec = next((r for r in records if r.vmid == card.record.vmid), None)
                if rec is not None:
                    card.update_record(rec, drivers.get(rec.vmid), rec.vmid in self.updates)
        else:
            await self._rebuild(keep)
        self._update_texts()

    async def _rebuild_job(self) -> None:
        record = self.current_record()
        await self._rebuild(record.vmid if record else None)

    def _built_compact(self) -> bool:
        cards = list(self.query(VmCard))
        return cards[0].compact if cards else self._compact()

    async def _rebuild(self, keep: int | None) -> None:
        grid = self.query_one("#cards", Vertical)
        await grid.remove_children()
        compact = self._compact()
        width = max((len(r.name) for r in self.records), default=0)
        cards = [VmCard(r, self.drivers.get(r.vmid), compact, width, r.vmid in self.updates) for r in self.records]
        grid.set_class(compact, "compact")
        if cards:
            await grid.mount(*cards)
        self._apply_columns()
        target = next((c for c in cards if c.record.vmid == keep), cards[0] if cards else None)
        if target is not None:
            target.focus()
        elif self.loaded:
            self.query_one(NewStrip).focus()

    def _update_texts(self) -> None:
        count = len(self.records)
        self.query_one("#home-sub", Static).update(
            f"{count} on this host · screens refresh every {THUMB_REFRESH_SECONDS} s" if count else "")
        strip = self.query_one(NewStrip)
        strip.set_proposal(self._proposal(), empty=count == 0)
        self.query_one("#cards").display = count > 0
        self._fit_rows()
        resume = self.query_one("#resume", Static)
        pal = self.app.get_css_variables()
        banner = Text(self.resume_text, style=pal["warning"])
        banner.append("  g", style="bold " + pal["accent"])
        banner.append(" continues", style=pal["muted"])
        resume.update(banner if self.resume_text else "")
        resume.display = bool(self.resume_text)
        stuck = self.query_one("#stuck", Static)
        held = self._stuck_record()
        stuck.update(Text("! " + still_running_line(held.name), style=pal["warning"]) if held else "")
        stuck.display = held is not None
        record = self.current_record()
        self.query_one("#selected-line", Static).update(
            Text(f"{record.name} selected", style=self.app.get_css_variables()["muted"]) if record else "")
        self.refresh_footer()

    def _stuck_record(self) -> VmRecord | None:
        """The selected Mac when it ignored a shutdown request for a minute, else the first such Mac."""
        stuck = [r for r in self.records if self.app.shutdowns.state(r) == "stuck"]
        selected = self.current_record()
        return next((r for r in stuck if selected and r.vmid == selected.vmid), stuck[0] if stuck else None)

    def _proposal(self) -> str:
        cores, mem_gb, disk = detect_cpu_cores(), detect_memory_mb() // 1024, default_disk_gb("sequoia")
        text = f"Sequoia 15 · {cores} cores · {mem_gb} GB"
        best = max(self._free.values(), default=None)
        if best is None:
            return text + f" · {disk} GB disk"
        return text + (f" · {disk} GB fits this host" if best >= disk else f" · {disk} GB disk, only {best} GB free")

    # ── selection ────────────────────────────────────────────────────

    def _compact(self) -> bool:
        if self.compact_override is not None:
            return self.compact_override
        return self.size.width < COMPACT_BELOW

    def columns(self) -> int:
        if self._compact():
            return 1
        width = max(1, self.query_one("#cards").size.width or self.size.width)
        return max(1, (width - 2 + GRID_GAP) // (CARD_WIDTH + GRID_GAP))

    def _apply_columns(self) -> None:
        grid = self.query_one("#cards")
        grid.styles.grid_size_columns = self.columns()
        self._fit_rows()

    def _room(self) -> int:
        """Rows left for the cards: the screen less the bars, the heading, the New Mac strip and the lines under it."""
        taken = 2 + 1 + 2 + 3 + 1 + 1 + (2 if self.resume_text else 0) + (1 if self._stuck_record() else 0)
        return max(6, self.size.height - taken)

    def thumb_rows(self) -> int:
        """Rows of one thumbnail: the picture's own shape at this width, so it has no black bars above or below.

        It is held to what lets one whole row of cards fit on the screen."""
        wanted = -(-int((CARD_WIDTH - 2) * LiveScreen.ratio) // 2)
        return max(THUMB_MIN_ROWS, min(wanted, THUMB_MAX_ROWS, self._room() - CARD_CHROME))

    def _fit_rows(self) -> None:
        """Whole rows of cards only: the grid is as tall as the rows that fit, and the rest is reached by scrolling."""
        grid = self.query_one("#cards")
        cards = [c for c in self.cards() if not c.compact]
        if self._compact() or not cards:
            grid.styles.height = "1fr"
            grid.styles.grid_rows = "1"  # one-row list entries, whatever the card rows were before
            return
        rows = self.thumb_rows()
        height = rows + CARD_CHROME
        for card in cards:
            card.styles.height = height
            for thumb in card.query(LiveScreen):
                thumb.styles.height = rows
        grid.styles.grid_rows = str(height)
        grid.styles.height = max(1, self._room() // height) * height

    def on_resize(self, event: Resize) -> None:
        if not self.loaded:
            return
        if self._compact() != self._built_compact():
            self.run_worker(self._rebuild_job, exclusive=True, group="home-rebuild")
        else:
            self._apply_columns()

    def cards(self) -> list[VmCard]:
        return list(self.query(VmCard))

    def current_record(self) -> VmRecord | None:
        focused = self.focused
        node = focused
        while node is not None and not isinstance(node, VmCard):
            node = node.parent
        if isinstance(node, VmCard):
            return node.record
        return self.records[0] if self.records else None

    def action_move(self, dx: int, dy: int) -> None:
        cards = self.cards()
        strip = self.query_one(NewStrip)
        focused = self.focused
        if focused is strip:
            if dy < 0 and cards:
                cards[-1].focus() if dx == 0 else None
            return
        if not cards:
            strip.focus()
            return
        idx = next((i for i, c in enumerate(cards) if c is focused), 0)
        cols = self.columns()
        new = idx + dx + dy * cols
        if dy > 0 and new >= len(cards):
            strip.focus()
            return
        new = max(0, min(len(cards) - 1, new))
        cards[new].focus()
        cards[new].scroll_visible()
        self._update_texts()

    def on_descendant_focus(self) -> None:
        if self.loaded:
            self._update_texts()

    # ── actions ──────────────────────────────────────────────────────

    def action_noop(self) -> None:
        """esc never quits the app."""

    def action_open(self) -> None:
        if self.focused is self.query_one(NewStrip):
            self.app.action_new_mac()
            return
        record = self.current_record()
        if record is not None:
            self.app.open_vm(record.vmid)

    def action_new_mac(self) -> None:
        self.app.action_new_mac()

    def action_host(self) -> None:
        self.app.action_host_check()

    def action_refresh(self) -> None:
        self.refresh_now()
        self.app.rerun_preflight()

    def action_toggle_view(self) -> None:
        self.compact_override = not self._compact()
        self.run_worker(self._rebuild_job, exclusive=True, group="home-rebuild")

    def after_change(self, removed: bool = False) -> None:
        self.refresh_now()

    def footer_items(self) -> list[tuple]:
        record = self.current_record() if self.loaded else None
        power = "Stop" if record and record.status == "running" else "Start"
        items: list[tuple] = []
        if record:
            items += [("←→", "Select", None), ("enter", "Open", "screen.open"), ("s", power, "screen.toggle_power"),
                      ("e", "Edit", "screen.edit"), ("g", "GPU", "screen.gpu"), ("d", "Remove", "screen.remove")]
        items += [("n", "New Mac", "screen.new_mac"), ("h", "Host", "screen.host"),
                  ("?", "Help", "app.help"), ("q", "Quit", "app.request_quit")]
        return items
