"""Build: one pipeline from "Check this host" to a ready Mac, with the VM's live screen beside it."""

from __future__ import annotations

import logging
import threading

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import RichLog, Static

from ..domain import VmConfig
from ..services import fetch_vm_info, get_proxmox_adapter
from ..services.pipeline import (
    ACTIVE,
    FAILED,
    OK,
    PENDING,
    SKIPPED,
    BuildPipeline,
    phase_title,
)
from ..services.unattended_service import SUBSTEPS, DriverState, driver_log_path, read_driver_state
from ..support import SUPPORT_LINE
from ..widgets.live_screen import LiveScreen
from .base import OsxScreen

log = logging.getLogger(__name__)

__all__ = ["BuildScreen", "LogScreen", "ZoomScreen", "format_clock", "timeline_text"]

BUILD_REFRESH_SECONDS = 2
TIMELINE_WIDTH = 36
POLL_EVERY = 3  # seconds between reads of the driver log and the VM status


def format_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def timeline_text(pipe: BuildPipeline, pal: dict, width: int = TIMELINE_WIDTH) -> Text:
    """The phases of a build, one per row with its duration on the right, install sub-steps under Install."""
    now = pipe.deps.clock()
    out = Text()
    steps = len(pipe.plan_steps)
    tones = {OK: (("✓", pal["success"])), ACTIVE: ("◐", pal["accent"]), FAILED: ("✗", pal["error"]),
             PENDING: ("·", pal["muted"])}
    first = True
    for phase in pipe.phases:
        if phase.status == SKIPPED:
            continue
        if phase.id == "ready" and phase.status == PENDING:
            glyph, tone = "·", pal["muted"]
        else:
            glyph, tone = tones[phase.status]
        title = phase_title(phase.id, phase.status, pipe.vmid, steps)
        dur = format_clock(phase.duration(now)) if phase.started else ""
        if not first:
            out.append("\n")
        first = False
        out.append(glyph + " ", style=tone)
        out.append(title, style="bold" if phase.status == ACTIVE else (pal["muted"] if phase.status == OK else ""))
        pad = max(1, width - 2 - len(title) - len(dur))
        out.append(" " * pad + dur, style=pal["muted"])
        if phase.id == "install" and phase.status != PENDING:
            out.append_text(_substeps(pipe.driver, phase.status, pal))
        if phase.id == "download" and phase.status == ACTIVE and pipe.download_text:
            out.append("\n  " + pipe.download_text, style=pal["muted"])
    return out


def _substeps(driver: DriverState, status: str, pal: dict) -> Text:
    """The install's sub-steps: everything before the current one is done, the current one runs (or failed)."""
    out = Text()
    ids = [pid for pid, _a, _d in SUBSTEPS]
    current = ids.index(driver.current) if driver.current in ids else -1
    for idx, (_pid, active_label, done_label) in enumerate(SUBSTEPS):
        finished = idx < current or (idx == current and (status == OK or idx == len(ids) - 1 and driver.finished))
        if finished:
            out.append(f"\n  \u2713 {done_label}", style=pal["muted"])
        elif idx == current and status == FAILED:
            out.append(f"\n  \u2717 {active_label}", style=pal["error"])
        elif idx == current:
            out.append(f"\n  \u25d0 {active_label}")
        else:
            out.append(f"\n  \u00b7 {active_label}", style=pal["muted"])
    return out


class BuildScreen(OsxScreen):
    CRUMB = "Building"
    BINDINGS = [
        Binding("l", "log", "Full log"),
        Binding("z", "zoom", "Zoom screen"),
        Binding("r", "retry", "Retry"),
        Binding("h", "host", "Open host check"),
        Binding("escape", "back", "Back to your Macs"),
    ]

    def __init__(self, config: VmConfig | None, unattended: bool = True, force_download: bool = False,
                 pipeline: BuildPipeline | None = None) -> None:
        super().__init__()
        self.config = config
        self.pipe = pipeline or BuildPipeline(config, unattended, force_download)
        self.vm_status = "stopped"
        self._worker_started = False
        self._polling = False
        self._tick_count = 0

    @classmethod
    def resume(cls, vmid: int, driver: DriverState) -> "BuildScreen":
        """The Build screen of a hands-off install that already runs, rebuilt from the driver's log."""
        screen = cls(None, True, pipeline=BuildPipeline.from_driver(vmid, driver))
        screen.vm_status = "running"
        return screen

    # ── layout ───────────────────────────────────────────────────────

    def crumb(self) -> str:
        return self.config.name if self.config else f"VM {self.pipe.vmid}"

    def compose_body(self) -> ComposeResult:
        with Vertical(id="build"):
            with Horizontal(id="build-head"):
                yield Static("", id="build-title")
                yield Static("", id="build-elapsed")
            with Horizontal(id="build-cols"):
                with Vertical(id="build-left"):
                    yield Static("", id="timeline")
                    yield Static("", id="latest")
                with Vertical(id="build-right"):
                    yield LiveScreen(self.pipe.vmid, "waiting", BUILD_REFRESH_SECONDS, fit=True, id="build-live")
                    yield Static("", id="build-caption")
            yield Static("", id="build-note")

    def on_mount(self) -> None:
        self.pipe.on_change = self._on_change
        self._redraw()
        self.set_interval(1, self._tick)
        if self.pipe.config is not None and not self._worker_started:
            self._worker_started = True
            self.run_worker(self._run_pipeline, thread=True, exit_on_error=False, group="pipeline")

    def _run_pipeline(self, start_at: str = "check") -> None:
        try:
            self.pipe.run(start_at)
        except Exception as exc:  # noqa: BLE001
            log.exception("build pipeline crashed")
            self.pipe.running = False
            failed = start_at if not self.pipe.failed else self.pipe.failed
            if not self.pipe.failed:
                self.pipe._fail(failed, f"Unexpected error: {exc}")
        self._on_change()

    def _on_change(self) -> None:
        try:
            if threading.get_ident() == self.app._thread_id:
                self._redraw()
            else:
                self.app.call_from_thread(self._redraw)
        except Exception:  # noqa: BLE001 - the app is closing
            pass

    # ── drawing ──────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._tick_count += 1
        self._paint_elapsed()
        self._paint_timeline()
        if self._tick_count % POLL_EVERY == 0:
            self._poll()

    def _redraw(self) -> None:
        pipe = self.pipe
        pal = self.app.get_css_variables()
        name = self.config.name if self.config else f"VM {pipe.vmid}"
        self.query_one("#build-title", Static).update(Text(f"Building {name}", style="bold"))
        self._paint_elapsed()
        self._paint_timeline()
        self._ensure_live()
        self._paint_latest(pal)
        self._paint_note(pal)
        self.refresh_footer()
        self.refresh_topbar()

    def _paint_elapsed(self) -> None:
        pal = self.app.get_css_variables()
        pipe = self.pipe
        text = Text(format_clock(pipe.elapsed()))
        text.append(" elapsed", style=pal["muted"])
        if self.pipe.unattended:
            text.append(" · usually 30 to 45 min", style=pal["muted"])
        self.query_one("#build-elapsed", Static).update(text)

    def _paint_timeline(self) -> None:
        self.query_one("#timeline", Static).update(timeline_text(self.pipe, self.app.get_css_variables()))

    def _vm_exists(self) -> bool:
        return self.pipe.phase("create").status == OK or self.pipe.config is None

    def _ensure_live(self) -> None:
        live = self.query_one("#build-live", LiveScreen)
        pal = self.app.get_css_variables()
        if self._vm_exists():
            if live.status == "waiting":
                live.set_state("running", False)
                self.vm_status = "running"
            narrow = self.size.width < 90
            caption = (f"live from VM {self.pipe.vmid} \u00b7 every {BUILD_REFRESH_SECONDS} s" if narrow else
                       f"live from VM {self.pipe.vmid} \u00b7 refreshes every {BUILD_REFRESH_SECONDS} s")
        else:
            caption = "The Mac's screen shows here once the VM exists."
        self.query_one("#build-caption", Static).update(Text(caption, style=pal["muted"]))

    def _paint_latest(self, pal: dict) -> None:
        pipe = self.pipe
        text = Text()
        if pipe.failed:
            text.append(f"✗ {pipe.failure}\n", style=pal["error"])
            tail = [line for line in pipe.log_lines if line != f"FAILED: {pipe.failure}"][-5:]  # the reason is above
            if tail:
                text.append("\nLog tail\n", style=pal["muted"])
                text.append("\n".join(line[:TIMELINE_WIDTH + 24] for line in tail) + "\n", style=pal["muted"])
            if pipe.rollback:
                text.append("\nWhat you can do\n", style=pal["muted"])
                text.append("\n".join(pipe.rollback))
        elif pipe.phase("install").status in (ACTIVE, OK) and pipe.driver.events:
            text.append("Latest\n", style=pal["muted"])
            for stamp, message in pipe.driver.events[-3:][::-1]:
                text.append(f"{stamp[:5]} {message}\n"[:TIMELINE_WIDTH + 30], style=pal["muted"])
        elif pipe.log_lines:
            text.append("Latest\n", style=pal["muted"])
            for line in pipe.log_lines[-2:][::-1]:
                text.append(line[:TIMELINE_WIDTH + 30] + "\n", style=pal["muted"])
        text.rstrip()
        self.query_one("#latest", Static).update(text)

    def _paint_note(self, pal: dict) -> None:
        pipe = self.pipe
        text = Text()
        if pipe.failed:
            tail = "The next keys: r tries again, l shows the full log." if self._can_retry() else \
                "Press Enter in the VM console to continue by hand, l shows the full log."
            text.append("✗ ", style=pal["error"])
            text.append("Stopped. ", style="bold")
            text.append(tail, style=pal["muted"])
            if pipe.host_failed:
                text.append(" h opens the host check.", style=pal["muted"])
        elif pipe.done and pipe.unattended:
            text.append("✓ ", style=pal["success"])
            text.append("Ready. ", style="bold")
            text.append(f"Finish Setup Assistant on the screen above, then run: "
                        f"osx-next-cli post-install --vmid {pipe.vmid} --execute", style=pal["muted"])
            text.append("\n" + SUPPORT_LINE, style=pal["muted"])
        elif pipe.done:
            text.append("✓ ", style=pal["success"])
            text.append("The VM is created. ", style="bold")
            text.append(f"Finish the install in the Proxmox console of VM {pipe.vmid}: press Enter on the macOS entry, "
                        f"and right after the installer's first restart run: "
                        f"osx-next-cli post-install --vmid {pipe.vmid} --execute", style=pal["muted"])
        elif pipe.safe_to_close:
            text.append("✓ ", style=pal["success"])
            text.append("Safe to close. ", style="bold")
            text.append("The install keeps running, and this view is back when you reopen osx-next.",
                        style=pal["muted"])
        elif pipe.running and pipe.phase("create").status == ACTIVE:
            text.append("Creating the VM. This takes about a minute.", style=pal["muted"])
        elif pipe.running:
            text.append("Nothing on the host changes until the rehearsal has passed.", style=pal["muted"])
        self.query_one("#build-note", Static).update(text)

    def _can_retry(self) -> bool:
        return bool(self.pipe.failed) and self.pipe.failed != "install" and self.pipe.config is not None

    def footer_items(self) -> list[tuple]:
        pipe = self.pipe
        items: list[tuple] = []
        if pipe.failed:
            if self._can_retry():
                items.append(("r", "Retry", "screen.retry"))
            if pipe.host_failed:
                items.append(("h", "Open host check", "screen.host"))
        items.append(("l", "Full log", "screen.log"))
        if self._vm_exists():
            items.append(("z", "Zoom screen", "screen.zoom"))
        items += [("esc", "Back to your Macs", "screen.back"), ("?", "Help", "app.help")]
        return items

    # ── polling the detached driver ──────────────────────────────────

    def _poll(self) -> None:
        if self._polling:
            return
        pipe = self.pipe
        install = pipe.phase("install").status == ACTIVE
        if not install and not self._vm_exists():
            return
        self._polling = True

        def work() -> None:
            state = read_driver_state(pipe.vmid) if install else None
            info = fetch_vm_info(pipe.vmid, adapter=get_proxmox_adapter()) if self._vm_exists() else None
            self.app.call_from_thread(self._polled, state, info)

        self.run_worker(work, thread=True, exit_on_error=False, group="build-poll")

    def _polled(self, state: DriverState | None, info) -> None:
        self._polling = False
        if info is not None:
            self.vm_status = info.status
            self.query_one("#build-live", LiveScreen).set_state(info.status, False)
        if state is not None:
            self.pipe.apply_driver_state(state)
        else:
            self._redraw()

    # ── keys ─────────────────────────────────────────────────────────

    def busy(self) -> bool:
        return self.pipe.running and not self.pipe.safe_to_close

    def action_back(self) -> None:
        if self.busy():
            self.app.notify("The build is running. It is safe to leave once the install starts.")
            return
        super().action_back()

    def action_retry(self) -> None:
        pipe = self.pipe
        if not self._can_retry() or pipe.running:
            return
        start = pipe.failed
        self.run_worker(lambda: self._run_pipeline(start), thread=True, exit_on_error=False, group="pipeline")

    def action_host(self) -> None:
        if self.pipe.host_failed:
            self.app.action_host_check()

    def action_log(self) -> None:
        self.app.push_screen(LogScreen(self.pipe))

    def action_zoom(self) -> None:
        if self._vm_exists():
            self.app.push_screen(ZoomScreen(self.pipe.vmid, self.vm_status))


class LogScreen(OsxScreen):
    """The complete log: this run's lines, then the hands-off driver's log file."""

    CRUMB = "Log"
    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, pipe: BuildPipeline) -> None:
        super().__init__()
        self.pipe = pipe

    def compose_body(self) -> ComposeResult:
        yield RichLog(id="full-log", wrap=False, highlight=False, markup=False, auto_scroll=False)

    def footer_items(self) -> list[tuple]:
        return [("↑↓", "Scroll", None), ("pgup pgdn", "Page", None), ("esc", "Back", "screen.back"),
                ("?", "Help", "app.help")]

    def on_mount(self) -> None:
        log_widget = self.query_one(RichLog)
        for line in self.pipe.log_lines:
            log_widget.write(line)
        path = self.pipe.driver_log or driver_log_path(self.pipe.vmid)
        try:
            with open(path, errors="replace") as handle:
                log_widget.write(f"--- {path}")
                for line in handle.read().splitlines():
                    log_widget.write(line)
        except OSError:
            pass
        log_widget.focus()


class ZoomScreen(OsxScreen):
    """The VM's screen on the whole terminal."""

    CRUMB = "Screen"
    BINDINGS = [Binding("escape", "back", "Back"), Binding("z", "back", "Back")]

    def __init__(self, vmid: int, status: str) -> None:
        super().__init__()
        self.vmid = vmid
        self.status = status

    def compose_body(self) -> ComposeResult:
        yield LiveScreen(self.vmid, self.status, BUILD_REFRESH_SECONDS, id="zoom-live")

    def footer_items(self) -> list[tuple]:
        return [("esc", "Back", "screen.back"), ("?", "Help", "app.help")]

