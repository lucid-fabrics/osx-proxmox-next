"""The BIOS screen: only when the check says a setting must change. Settings, three steps and a QR code."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Static

from ..widgets.lines import Line, Lines
from .base import ConfirmDialog

QR_GAP = 4          # the border and padding of the QR column
SEARCH_LINE = "Cannot find it? The BIOS search finds settings by name."
MIN_BESIDE = 44     # columns the steps need before the QR code may sit beside them
CHROME_ROWS = 7     # the rows around the columns: bars, title, the hint, the buttons
QR_TOP = 2          # the "Take it with you" label and the gap under it
QR_CAPTION = 3      # the gap and the two caption lines under the code


def bios_lines(view: dict, pal: dict) -> list[Line]:
    """The settings to change, the ones already right and the three steps, as rows."""
    rows: list[Line] = [Line(Text(f"Change in the BIOS · {view['board']}", style=pal["muted"])), Line(Text(""))]
    for item in view["rows"]:
        left = Text.assemble(("✗ ", pal["error"]), (item["label"], "bold"))
        rows.append(Line(left, Text.assemble(("set to ", ""), (item["value"], "bold")), hang=2))
        for text in item["where"] + [item["why"]]:
            rows.append(Line(Text(text, style=pal["muted"]), indent=2, wrap=True))
        rows.append(Line(Text("")))
    right = len(view["ok_labels"])
    if right:
        word = "setting is" if right == 1 else "settings are"
        other = "other " if view["rows"] else ""
        rows.append(Line(Text.assemble(("✓ ", pal["success"]), (f"{right} {other}{word} already right",
                                                                  pal["muted"]))))
        rows.append(Line(Text(" · ".join(view["ok_labels"]), style=pal["muted"]), indent=2, wrap=True))
        rows.append(Line(Text("")))
    return rows + _how(view, pal)


def _how(view: dict, pal: dict) -> list[Line]:
    plural = len(view["rows"]) != 1
    first = (("Restart into the BIOS ", "(enter does it for you)") if view["firmware_reboot"] else
             (f"Restart the host and press {view['enter'] or 'the setup key'} ", "to open the BIOS"))
    steps = [first, (f"Change the setting{'s' if plural else ''}, then save and exit ", "(often F10)"),
             ("Open osx-next again", "")]
    rows = [Line(Text("How", style=pal["muted"])), Line(Text(""))]
    for idx, (head, tail) in enumerate(steps, start=1):
        rows.append(Line(Text.assemble((f"{idx} ", pal["muted"]), head, (tail, pal["muted"])), wrap=True, hang=2))
    rows.append(Line(Text("it checks and carries on by itself", style=pal["muted"]), indent=2))
    if view["order"]:
        rows += [Line(Text("")), Line(Text(view["order"], style=pal["muted"]), wrap=True)]
    return rows


class BiosMixin:
    """Stage 3 of GpuScreen: the BIOS screen, "Restart into BIOS" and "Check again"."""

    def step_bios(self) -> None:
        s = self.s

        def work() -> Any:
            items = s.bios_items()
            return s.bios_view(items)

        self.job(work, self._bios_done, "Checking the BIOS settings...")

    def _bios_done(self, view: dict | None) -> None:
        if view is None:
            self.s.complete(3)  # checked and fine: done, not skipped
            self.drive()
            return
        self.bios_view = view
        self.run_worker(self._show_bios(view), exclusive=True, group="gpu-mode")

    def _qr_beside(self, view: dict) -> bool:
        width = max((len(row) for row in view["qr"]), default=0)
        fits_wide = (self.size.width or 100) - 4 - width - QR_GAP - 3 >= MIN_BESIDE
        # a code cut off at the bottom cannot be scanned: without the rows it needs, the link goes in the text
        return bool(width) and fits_wide and self._qr_rows_left(view) >= 0

    def _qr_rows_left(self, view: dict) -> int:
        """Rows left under the code and its label (negative when it does not fit)."""
        return (self.size.height or 30) - CHROME_ROWS - len(view["qr"]) - QR_TOP

    def _qr_column(self, view: dict, pal: dict) -> Vertical:
        qr = Text("\n".join(view["qr"]), style="black on white", no_wrap=True)
        caption = "Scan to keep these steps on your phone while the host is in the BIOS."
        parts: list[Widget] = [Static(Text("Take it with you", style=pal["muted"])), Static(""),
                               Static(qr, id="g-bios-code")]
        if self._qr_rows_left(view) >= QR_CAPTION:   # the caption only when there is room for it whole
            parts += [Static(""), Static(Text(caption, style=pal["muted"]), id="g-bios-caption")]
        column = Vertical(*parts, id="g-bios-qr")
        column.styles.width = max(len(row) for row in view["qr"]) + QR_GAP
        return column

    async def _show_bios(self, view: dict) -> None:
        pal = self.app.get_css_variables()
        firmware = view["firmware_reboot"]
        plural = len(view["rows"]) != 1
        sub = "BIOS settings on this host block the card" if plural else "a BIOS setting on this host blocks the card"
        lines = Lines(id="g-bios-rows")
        left: list[Widget] = [lines]
        beside = self._qr_beside(view)
        if not beside and view["url"]:
            left.append(Static(Text(f"\nSame steps with pictures on your phone:\n{view['url']}", style=pal["muted"])))
        cols: list[Widget] = [VerticalScroll(*left, id="g-bios-left")]
        if beside:
            cols.append(self._qr_column(view, pal))
        buttons = [Button("Check again", id="g-recheck", classes="secondary" if firmware else "primary")]
        if firmware:
            buttons.append(Button("Restart into BIOS", id="g-primary-bios", classes="primary"))
        widgets: list[Widget] = [
            Static(Text.assemble(("One thing only you can do", "bold"), ("  " + sub, pal["muted"])), id="g-run-title"),
            Horizontal(*cols, id="g-bios-cols"), Static("", id="g-result", classes="when-text"),
            Static(Text(SEARCH_LINE, style=pal["muted"]), id="g-note", classes="bios-note"),   # whole, on its own row above the buttons
            Horizontal(*buttons, id="g-bios-foot")]
        self.box_buttons = [("g-recheck", "Restart into BIOS" if firmware else "Check again", "primary")]
        await self.set_mode("bios", widgets)
        lines.set_lines(bios_lines(view, pal))
        self.set_actions(self._bios_restart if firmware else self.step_bios, self.step_bios)
        self.query_one("#g-primary-bios" if firmware else "#g-recheck", Button).focus()

    def _bios_restart(self) -> None:
        s = self.s
        running = s.running_vms()
        lines = ["The host restarts into the BIOS setup."]
        lines.append("These VMs will be shut down: " + ", ".join(f"{i} ({n})" for i, n in running) + "."
                     if running else "No VM is running.")

        def answer(result: str | None) -> None:
            if result != "yes":
                return
            ok, out = s.reboot_to_bios()
            self.say("Restarting into the BIOS..." if ok else "Could not start the restart: " + out, fail=not ok)

        self.app.push_screen(ConfirmDialog("Restart into the BIOS?", lines, confirm="Restart into BIOS", danger=True),
                             answer)

    def action_recheck(self) -> None:
        if self.working:
            return
        if self.mode == "bios":
            self.step_bios()
        elif self.mode == "run" and self.s.state["step"] == 6:
            self.step_verify()
