"""A widget that shows what a VM's display shows right now.

Frames come from `qm monitor` screendump through unattended.QmConsole (the same
code path, including how the monitor session is started, as the install
driver) but into this widget's own temp file, so it never collides with the
driver's probe. Grabbing runs on a worker thread and only while the VM runs and
the widget is on screen."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from typing import ClassVar

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual.geometry import Size
from textual.strip import Strip
from textual.widget import Widget

from ..unattended import QmConsole
from .ppm import Frame, Pixels, fit_size, parse_ppm, sample

log = logging.getLogger(__name__)

__all__ = ["LiveScreen", "grab_frame", "render_cells"]

PLACEHOLDER_OFF = "powered off"
PLACEHOLDER_NO_SIGNAL = "no signal"
PLACEHOLDER_GPU = "screen is on the GPU"

_HALF = "▀"  # upper half block: foreground = top pixel, background = bottom pixel


def grab_frame(vmid: int, probe_path: str) -> bytes:
    """Raw PPM bytes of the VM's screen into *probe_path*; b"" when the grab failed."""
    return QmConsole(vmid, probe_path=probe_path).grab()


def render_cells(pixels: Pixels, cols: int, rows: int) -> list[Strip]:
    """Terminal rows for a pixel grid: two pixels per cell, letterboxed in black inside cols x rows."""
    black = (0, 0, 0)
    out_h = len(pixels)
    out_w = len(pixels[0]) if pixels else 0
    left = max(0, (cols - out_w) // 2)
    top_pad = max(0, (rows * 2 - out_h) // 2)
    cache: dict[tuple, Style] = {}

    def style(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> Style:
        key = (fg, bg)
        found = cache.get(key)
        if found is None:
            found = cache[key] = Style(color=Color.from_rgb(*fg), bgcolor=Color.from_rgb(*bg))
        return found

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        px, py = x - left, y - top_pad
        if 0 <= py < out_h and 0 <= px < out_w:
            return pixels[py][px]
        return black

    strips: list[Strip] = []
    for row in range(rows):
        segments = [Segment(_HALF, style(pixel(x, row * 2), pixel(x, row * 2 + 1))) for x in range(cols)]
        strips.append(Strip(segments, cols))
    return strips


class LiveScreen(Widget):
    """The display of one VM, redrawn every *interval* seconds while it runs."""

    DEFAULT_CSS = """
    LiveScreen { height: 1fr; min-height: 3; }
    """

    # Swapped by tests; production grabs through qm monitor.
    grabber: ClassVar[Callable[[int, str], bytes]] = staticmethod(grab_frame)

    def __init__(self, vmid: int, status: str = "stopped", interval: float = 5.0,
                 has_gpu: bool = False, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.vmid = vmid
        self.status = status
        self.interval = interval
        self.has_gpu = has_gpu
        self.signal = True      # False after a failed grab
        self._frame: Frame | None = None
        self._cells: list[Strip] = []
        self._cells_size = Size(0, 0)
        self._busy = False
        self.grabs = 0          # finished grabs, for tests and the status line
        self._probe = os.path.join(tempfile.gettempdir(), f"osx-next-view-{vmid}-{os.getpid()}-{id or 'w'}.ppm")

    # ── state ────────────────────────────────────────────────────────

    @property
    def placeholder(self) -> str:
        """The text shown instead of a picture ("" when a picture is shown)."""
        if self.has_gpu:
            return PLACEHOLDER_GPU
        if self.status != "running":
            return PLACEHOLDER_OFF
        if self._frame is None or not self.signal:
            return PLACEHOLDER_NO_SIGNAL
        return ""

    def set_state(self, status: str, has_gpu: bool) -> None:
        was_running = self.status == "running"
        self.status, self.has_gpu = status, has_gpu
        if status != "running" or has_gpu:
            self._frame = None
            self._cells = []
        elif not was_running:
            self.grab_now()
        self.refresh()

    def should_grab(self) -> bool:
        """Only a running VM with a virtual display, on a screen the user can see."""
        return self.status == "running" and not self.has_gpu and self.is_visible_on_screen()

    def is_visible_on_screen(self) -> bool:
        """Mounted, shown, on the active screen and inside the viewport of every scrolling parent."""
        if not self.is_attached or not self.display or not self.screen.is_active:
            return False
        if self.size.width <= 0 or self.size.height <= 0:
            return False
        node = self.parent
        while node is not None and node is not self.screen:
            if node.is_scrollable and not self.region.overlaps(node.content_region):
                return False
            node = node.parent
        return True

    # ── grabbing ─────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.set_interval(self.interval, self.grab_now)
        self.set_timer(0.05, self.grab_now)

    def on_unmount(self) -> None:
        try:
            os.unlink(self._probe)
        except OSError:
            pass

    def grab_now(self) -> None:
        if self._busy or not self.should_grab():
            return
        self._busy = True
        size = self.size
        self.run_worker(lambda: self._grab(size), thread=True, group=f"grab-{self.vmid}-{id(self)}",
                        exit_on_error=False)

    def _grab(self, size: Size) -> None:
        try:
            data = type(self).grabber(self.vmid, self._probe)
            frame = parse_ppm(data) if data else None
            cells = self._render(frame, size) if frame else []
        except Exception:  # noqa: BLE001 - a bad frame must never take the TUI down
            log.debug("grab failed for VM %s", self.vmid, exc_info=True)
            frame, cells = None, []
        try:
            self.app.call_from_thread(self._apply, frame, cells, size)
        except Exception:  # noqa: BLE001 - the app is closing
            self._busy = False

    @staticmethod
    def _render(frame: Frame, size: Size) -> list[Strip]:
        w, h = fit_size(frame.width, frame.height, size.width, size.height * 2)
        if w <= 0:
            return []
        return render_cells(sample(frame, w, h), size.width, size.height)

    def _apply(self, frame: Frame | None, cells: list[Strip], size: Size) -> None:
        self._busy = False
        self.grabs += 1
        self.signal = frame is not None
        if frame is not None:
            self._frame, self._cells, self._cells_size = frame, cells, size
        self.refresh()

    def on_resize(self) -> None:
        if self._frame is not None and self.size != self._cells_size and self.size.width and self.size.height:
            self._cells = self._render(self._frame, self.size)
            self._cells_size = self.size

    # ── drawing ──────────────────────────────────────────────────────

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        text = self.placeholder
        if text:
            return self._placeholder_line(y, width, text)
        if y < len(self._cells):
            return self._cells[y].crop(0, width) if self._cells[y].cell_length > width else self._cells[y]
        return Strip.blank(width)

    def _placeholder_line(self, y: int, width: int, text: str) -> Strip:
        style = self.rich_style
        if y != self.size.height // 2:
            return Strip([Segment(" " * width, style)], width)
        label = text[:width]
        left = max(0, (width - len(label)) // 2)
        muted = self.app.get_css_variables().get("muted", "#8b98a8")
        segs = [Segment(" " * left, style), Segment(label, style + Style(color=muted)),
                Segment(" " * max(0, width - left - len(label)), style)]
        return Strip(segs, width)
