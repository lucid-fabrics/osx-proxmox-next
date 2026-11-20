"""Graphics card (GPU passthrough): choose and approve once, restart once, confirm it works.

A Session (gpu_setup.py) does the work and keeps its eight stages. This screen shows three moments:
one screen to choose and approve (with a BIOS screen only when the BIOS must change), a run that
pauses at the one restart and finishes by itself after it, and the first boot question."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from .. import gpu, host_setup, nullmoth, nullmoth_cli, nullmoth_finish
from ..gpu_setup import (
    CONFIRM_PHRASE,
    DISPLAY_OPTIONS,
    INTEL_DISPLAY_NOTE,
    NVIDIA_DISPLAY_NOTE,
    TROUBLESHOOTING,
    CardChanged,
    GpuCard,
    Session,
    VmOption,
)
from ..nullmoth import ACK_LABEL, ACK_PHRASE, NOTICE_BODY, NOTICE_TITLE
from ..services import fetch_vm_info
from ..services.unattended_service import read_driver_state
from ..widgets.chrome import KeyBar, TopBar
from ..widgets.rows import ChoiceRow, FieldRow, Option
from .base import Choice, ChoiceDialog, ConfirmDialog, OsxScreen

log = logging.getLogger(__name__)

__all__ = ["GpuScreen", "ChangesDialog", "pack_fields", "qr_spans", "plan_fingerprint", "vm_fingerprint"]

LATER = "later"
REBOOT_SECONDS = "about 3 min"
UNDO_LINES = ["This puts back every file and VM setting this setup changed (anything you edited since is "
              "kept and reported)."]


def pack_fields(fields: list[str], width: int, sep: str = "   ") -> list[str]:
    """Pack *fields* into lines no wider than *width*, breaking only between fields."""
    lines: list[str] = []
    for field_ in fields:
        if lines and len(lines[-1]) + len(sep) + len(field_) <= width:
            lines[-1] += sep + field_
        else:
            lines.append(field_)
    return lines


def qr_spans(text: str, qr: list[str]) -> list[tuple[int, int, int]]:
    """(line index, start, end) of every QR row inside the card text, to draw them dark on light."""
    spans = []
    for idx, line in enumerate(text.split("\n")):
        for row in qr:
            at = line.find(row)
            if at >= 0 and row.strip():
                spans.append((idx, at, at + len(row)))
                break
    return spans


def plan_fingerprint(plan: dict) -> str:
    """A stable digest of what a host plan would write and run."""
    body = {"changes": [(c["path"], c["new"]) for c in plan["changes"]],
            "commands": [c["argv"] for c in plan["commands"]]}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()


def vm_fingerprint(bdf: str, vmid: int | None, display: str, steps: list) -> str:
    """A stable digest of the VM settings an attach would write. Stopping a running Mac is not part of
    it: whether the Mac is running changes between the approval and the attach."""
    body = {"bdf": bdf, "vm": vmid, "display": display,
            "steps": [s.argv for s in steps if not s.title.lower().startswith("stop")]}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()


def short_card_name(name: str) -> str:
    """`GeForce RTX 3060` out of `NVIDIA Corporation GA106 [GeForce RTX 3060]`: the marketing name, when there is one."""
    inner = [part.split("]")[0] for part in name.split("[")[1:]]
    for part in reversed(inner):
        if part and not part.startswith("AMD/ATI"):
            return part
    return name


def needs_typed(plan: dict) -> bool:
    return bool(plan["hard_warnings"]) or any("only display adapter" in r for r in plan["refusals"])


def plan_lines(plan: dict) -> list[str]:
    """The plan's own words, as the host step has always shown them."""
    lines = []
    for key, prefix in (("refusals", "Refused: "), ("hard_warnings", "HARD WARNING: "), ("warnings", "Warning: "),
                        ("notes", "Note: "), ("manual", "Manual step: "), ("skipped", "OK: ")):
        # Typing the phrase overrides the only-display-adapter stop, so that one is "Careful", not "Refused".
        lines += [("Careful: " if key == "refusals" and "only display adapter" in t else prefix) + t
                  for t in plan[key]]
    return lines


@dataclass
class Preview:
    """What choosing a card for a Mac would need, worked out without writing anything."""
    card: GpuCard | None = None
    vmid: int | None = None
    display: str = "console"
    items: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    vm_plan: Any = None
    error: str = ""
    typed_needed: bool = False   # a hard warning or the only-display-adapter stop wants the typed phrase

    @property
    def bios_failing(self) -> list:
        return [i for i in self.items if not i["ok"]]

    @property
    def host_work(self) -> bool:
        return bool(self.plan) and Session.plan_needs_work(self.plan) and not self.plan["optional"]

    @property
    def refusals(self) -> list[str]:
        """Reasons that cannot be typed away (the only-display-adapter stop can)."""
        out = [r for r in self.plan.get("refusals", []) if "only display adapter" not in r]
        if self.vm_plan is not None:
            out += list(self.vm_plan.issues)
        return out


class ChangesDialog(ModalScreen):
    """Every change of the plan in one scrolling dialog: files, commands, VM settings, backups."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("enter", "close", "Close"),
                Binding("p", "close", "Close")]

    def __init__(self, text: str) -> None:
        super().__init__()
        self.body_text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="changes-box", classes="dialog"):
            yield Static("Every change", classes="dialog-title")
            yield Static("", classes="blank")
            with VerticalScroll(id="changes-scroll"):
                yield Static(Text(self.body_text), id="changes-text")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", classes="secondary")
        yield KeyBar([("↑↓", "Scroll", None), ("esc", "Close", "screen.close")])

    def on_mount(self) -> None:
        self.query_one("#changes-scroll", VerticalScroll).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class GpuScreen(OsxScreen):
    """The graphics card flow. Modes: choose, bios, run, first boot, finished."""

    BINDINGS = [
        Binding("up", "row(-1)", "Move", show=False),
        Binding("down", "row(1)", "Move", show=False),
        Binding("enter", "primary", "Go"),
        Binding("p", "changes", "See every change"),
        Binding("u", "undo", "Put everything back"),
        Binding("r", "recheck", "Check again"),
        Binding("y", "yes", "Yes, all good"),
        Binding("n", "no", "No, help me"),
        Binding("escape", "back", "Leave"),
    ]
    HELP_TITLE = "Graphics card"
    HELP_EXTRA = [("\u2190  \u2192  h  l", "Change the choice on this row")]

    def __init__(self, vmid: int | None = None, undo: bool = False, session: Session | None = None,
                 width: int = 0) -> None:
        super().__init__()
        self.vmid = vmid
        self.start_undo = undo
        self.s: Session = session or Session()
        self.mode = "loading"
        self.cards: list[GpuCard] = []
        self.vm_pool: dict[str, list[VmOption]] = {}
        self.preview: Preview | None = None
        self.working = False          # a worker runs: leaving or quitting needs care
        self.durations: dict[str, float] = {}
        self._phase_started: dict[str, float] = {}
        self._token = 0
        self.releases: list[dict] = []
        self.fix = ""                 # what verify said to do
        self.bios_card: dict | None = None
        self.result_text = ""
        self.result_fail = False
        self.box_title = ""
        self.box_lines: list[str] = []
        self.box_buttons: list[tuple[str, str, str]] = []  # (id, label, kind)
        self.help_text = ""
        self.fixed_vm = vmid is not None
        self.progress = ""
        self._primary_action: Callable[[], None] | None = None
        self._secondary_action: Callable[[], None] | None = None
        self._ask_go: Callable[[], None] | None = None
        self.waiting_for_restart = False   # the restart box is up and it is the user's turn
        self.backup_note = ""              # where the backups went, shown under the timeline
        self._names: dict[int, str] = {}   # VM names read from Proxmox, for a flow that was reopened
        self._mac_touched = False          # the user moved the Mac row themselves
        self._typed = False                # whether "I understand" was right at the last plan
        self._width = width

    # ── layout ───────────────────────────────────────────────────────

    def crumb(self) -> str:
        label = ""
        vmid = self.s.state.get("vm") if self.s else None
        vmid = vmid if vmid is not None else self.vmid
        if vmid is not None:
            label = self._vm_name(vmid) + " › "
        return label + "Graphics card BETA"

    def _vm_name(self, vmid: int) -> str:
        for opt in (o for pool in self.vm_pool.values() for o in pool):
            if opt.vmid == vmid and opt.name:
                return opt.name
        return self._names.get(vmid) or f"VM {vmid}"

    def compose_body(self) -> ComposeResult:
        yield Vertical(id="gpu")

    def footer_items(self) -> list[tuple]:
        if self.mode == "choose":
            return [("↑↓", "Move", None), ("←→", "Change", None),
                    ("enter", "Set it up", "screen.primary"), ("p", "See every change", "screen.changes"),
                    ("esc", "Cancel", "screen.back"), ("?", "Help", "app.help")]
        if self.mode == "first_boot" and self.s.state["gpu"] and self.s.state["gpu"]["vendor"] != "nvidia":
            items = [("y", "Yes, all good", "screen.yes"), ("n", "No, help me", "screen.no")]
        elif self.mode == "bios":
            items = [("enter", self._primary_label(), "screen.primary"), ("r", "Check again", "screen.recheck")]
        else:
            items = [("enter", self._primary_label(), "screen.primary")] if self.box_buttons else []
        if self.mode == "run":
            items += [("p", "See every change", "screen.changes")]
        if self.s.has_undo():
            items.append(("u", "Put everything back", "screen.undo"))
        items.append(("esc", "Your Macs" if self.mode in ("first_boot", "finished") else "Leave", "screen.back"))
        items.append(("?", "Help", "app.help"))
        return items

    def _primary_label(self) -> str:
        return self.box_buttons[-1][1] if self.box_buttons else "Go"

    def on_resize(self) -> None:
        self.set_class(self.size.width < 90, "narrow")

    def on_mount(self) -> None:
        self.s.state["resume_cmd"] = self.s.state.get("resume_cmd") or "osx-next-cli gpu-setup"
        if self.start_undo:
            self.call_after_refresh(self.action_undo)
        self.begin()

    def busy(self) -> bool:
        return self.working

    def action_back(self) -> None:
        if self.working:
            self.app.notify("Wait for this step to finish. It is quick, and leaving now could stop it half done.")
            return
        super().action_back()

    # ── entry ────────────────────────────────────────────────────────

    def begin(self) -> None:
        s = self.s
        vmid = s.state.get("vm")
        if vmid is not None and vmid not in self._names and not s.state["finished"]:
            def work() -> str:
                info = fetch_vm_info(vmid, adapter=s.pve)
                return info.name if info else ""

            def done(name: str) -> None:
                if name:
                    self._names[vmid] = name
                self._begin_flow()

            self.job(work, done)
            return
        self._begin_flow()

    def _begin_flow(self) -> None:
        s = self.s
        if s.state["finished"]:
            self.run_worker(self._show_finished(), exclusive=True, group="gpu-mode")
        elif s.state["gpu"] is not None and s.state["step"] >= 3:
            self.drive()
        else:
            self.run_worker(self._show_choose(), exclusive=True, group="gpu-mode")

    def drive(self) -> None:
        """Do what the saved step says is next."""
        s = self.s
        self.waiting_for_restart = False
        step = s.state["step"]
        if s.state["finished"]:
            self.run_worker(self._show_finished(), exclusive=True, group="gpu-mode")
        elif step <= 2:
            self.run_worker(self._show_choose(), exclusive=True, group="gpu-mode")
        elif step == 3:
            self.step_bios()
        elif step == 4:
            self.step_host()
        elif step == 5:
            self.step_restart()
        elif step == 6:
            self.step_verify()
        elif step == 7:
            self.step_attach()
        else:
            self.run_worker(self._show_first_boot(), exclusive=True, group="gpu-mode")

    # ── worker plumbing ──────────────────────────────────────────────

    def job(self, work: Callable[[], Any], done: Callable[[Any], None], note: str = "") -> None:
        """Run *work* off the UI thread; hand its result to *done* on the UI thread."""
        self.working = True
        if note:
            self.progress = note
            self.paint_run()

        def runner() -> None:
            try:
                result = work()
            except CardChanged as exc:
                self.app.call_from_thread(self._job_card_changed, exc)
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("GPU setup step failed")
                self.app.call_from_thread(self._job_failed, str(exc))
                return
            self.app.call_from_thread(self._job_done, done, result)

        self.run_worker(runner, thread=True, exit_on_error=False, group="gpu-job")

    def _job_done(self, done: Callable[[Any], None], result: Any) -> None:
        self.working = False
        self.progress = ""
        done(result)

    def _job_failed(self, message: str) -> None:
        self.working = False
        self.progress = ""
        self.say("Something went wrong: " + message, fail=True)
        self.refresh_buttons()

    def _job_card_changed(self, exc: Exception) -> None:
        """The chosen card moved or changed: say so plainly and send the user back to the choice."""
        self.working = False
        self.progress = ""
        message = self.s.card_changed(exc)
        self.s.clear_approval()
        self.run_worker(self._show_choose(message), exclusive=True, group="gpu-mode")

    def say(self, text: str, fail: bool = False) -> None:
        self.result_text, self.result_fail = text, fail
        try:
            box = self.query_one("#g-result", Static)
        except Exception:  # noqa: BLE001 - mode without a result line
            return
        box.update(Text(text, style=self.app.get_css_variables()["error" if fail else "foreground"]))

    async def set_mode(self, mode: str, widgets: list[Widget]) -> None:
        self.mode = mode
        body = self.query_one("#gpu", Vertical)
        await body.remove_children()
        await body.mount(*widgets)
        self.refresh_footer()
        for bar in self.query(TopBar):
            bar.set_crumb(self.crumb())

    # ── moment 1: choose and approve ─────────────────────────────────

    async def _show_choose(self, notice: str = "") -> None:
        s = self.s
        self.preview = None
        self.working = True
        loading = [Static("Looking for graphics cards...", id="g-loading")]
        await self.set_mode("choose", loading)

        def work() -> Any:
            cards = s.scan()
            vendors = {c.vendor for c in cards if c.selectable}
            return s.summary_fields(), cards, {v: s.vm_options(v) for v in vendors}

        def done(result: Any) -> None:
            self.run_worker(self._choose_ready(result, notice), exclusive=True, group="gpu-mode")

        self.job(work, done)

    async def _choose_ready(self, result: Any, notice: str) -> None:
        fields, cards, pool = result
        self.cards = cards
        self.vm_pool = {"amd": [], "nvidia": [], "intel": [], **pool}
        usable = [c for c in cards if c.selectable]
        pal = self.app.get_css_variables()
        widgets: list[Widget] = []
        if notice:
            widgets.append(Static(Text(notice, style=pal["error"]), classes="g-notice"))
        host_line = "   ".join(fields)
        head_name = self._vm_name(self.vmid) if self.vmid is not None else ""
        title = f"Give {head_name} a graphics card" if head_name else "Choose a graphics card"
        sub = ("one card here works with macOS" if len(usable) == 1 else
               ("choose the card to hand over" if usable else "no card here can run macOS graphics"))
        widgets.append(Static(Text.assemble((title, "bold"), ("  " + sub, pal["muted"])), id="g-title"))
        widgets.append(Static(Text(host_line, style=pal["muted"]), id="g-host"))
        options = [Option(c.bdf, short_card_name(c.name), c.selectable) for c in cards]
        card_row = ChoiceRow("Card", options, usable[0].bdf if usable else (cards[0].bdf if cards else ""),
                             id="g-card")
        mac_row = ChoiceRow("Mac", [Option(LATER, "Decide later")], LATER, id="g-mac")
        display_row = ChoiceRow("Display", [Option(v, label) for v, label, _d in DISPLAY_OPTIONS], "gpu",
                                id="g-display")
        driver_row = ChoiceRow("Driver", [Option("latest", "Latest")], "latest", id="g-driver")
        opt_row = ChoiceRow("Early binding", [Option("off", "Skip"), Option("on", "Write it")], "off",
                            id="g-optional")
        form = VerticalScroll(
            card_row, mac_row, display_row, driver_row, opt_row,
            Static("", id="g-before"),
            FieldRow("", "", id="g-confirm", placeholder="type the phrase here"),
            FieldRow("", "", id="g-ack", placeholder="type the phrase here"),
            Static("", id="g-reason"),
            id="g-form")
        side = Vertical(
            Static(Text("What it takes on this host", style=pal["muted"]), classes="side-title"),
            Static("", id="g-need"),
            Static(Text("Your part", style=pal["muted"]), classes="side-title", id="g-part-title"),
            Static("", id="g-part"),
            id="g-side")
        widgets.append(Horizontal(form, side, id="g-cols"))
        widgets.append(Static("", id="g-result"))
        widgets.append(Horizontal(
            Static(Text("Nothing changes until you press enter.", style=pal["muted"]), id="g-note"),
            Button("Set it up", id="g-primary", classes="primary", disabled=True), id="g-action"))
        await self.set_mode("choose", widgets)
        self.working = False
        self._fill_mac_row()
        self.query_one("#g-confirm", FieldRow).display = False
        self.query_one("#g-ack", FieldRow).display = False
        self.query_one("#g-display").display = False
        self.query_one("#g-driver").display = False
        self.query_one("#g-optional").display = False
        if not usable:
            self.query_one("#g-need", Static).update(Text(
                "No card here can run macOS graphics. See the docs for cards that work.", style=pal["error"]))
        self._card_changed_ui()
        self.query_one("#g-card", ChoiceRow).focus()

    def _selected_card(self) -> GpuCard | None:
        try:
            value = self.query_one("#g-card", ChoiceRow).value
        except Exception:  # noqa: BLE001
            return None
        return next((c for c in self.cards if c.bdf == value and c.selectable), None)

    def _fill_mac_row(self) -> None:
        row = self.query_one("#g-mac", ChoiceRow)
        card = self._selected_card()
        pool = self.vm_pool.get(card.vendor, []) if card else []
        if self.fixed_vm:
            opt = next((o for o in pool if o.vmid == self.vmid), None)
            label = opt.name if opt and opt.name else f"VM {self.vmid}"
            row.set_options([Option(str(self.vmid), label)], str(self.vmid))
            row.set_text(f"VM {self.vmid}" + (f" · {opt.status}" if opt else ""),
                         "" if opt is None or opt.eligible or opt.status == "running" else opt.why)
            return
        options = [Option(str(o.vmid), o.name or f"VM {o.vmid}", o.eligible or o.status == "running") for o in pool]
        options.append(Option(LATER, "Decide later"))
        keep = row.value if self._mac_touched and any(o.value == row.value for o in options) else ""
        first = next((o.value for o in options if o.enabled and o.value != LATER), LATER)
        row.set_options(options, keep or first)
        self._mac_detail()

    def _mac_detail(self) -> None:
        row = self.query_one("#g-mac", ChoiceRow)
        if row.value == LATER:
            row.set_text("", "Set up the host now, pick a Mac later")
            return
        pool = [o for p in self.vm_pool.values() for o in p]
        opt = next((o for o in pool if str(o.vmid) == row.value), None)
        if opt:
            row.set_text(f"VM {opt.vmid}" + (f" · macOS {opt.macos}" if opt.macos else "") + f" · {opt.status}",
                         "" if opt.eligible or opt.status == "running" else opt.why)

    def _card_changed_ui(self) -> None:
        """The card row changed: refresh its hint, the rows that depend on the vendor, and the preview."""
        card = self._selected_card()
        pal = self.app.get_css_variables()
        row = self.query_one("#g-card", ChoiceRow)
        hint = Text()
        if card is not None:
            row.set_text(card.bdf, "")
            hint.append(card.headline, style=pal["muted"])
            if card.recommended:
                hint.append("\n" + card.detected_line + " Nothing changes before you press enter.", style=pal["success"])
            for note in card.notes:
                hint.append("\n" + note, style=pal["warning"])
        for other in self.cards:
            if not other.selectable:
                hint.append(f"\n{short_card_name(other.name)} ({other.bdf}): {other.why_not}", style=pal["muted"] + " dim")
        row.hint = hint
        row.set_text(card.bdf if card else "", hint)
        vendor = card.vendor if card else ""
        self.query_one("#g-display").display = vendor == "amd"
        nvidia = vendor == "nvidia"
        self.query_one("#g-driver").display = nvidia
        if nvidia:
            self._fill_releases()
        self.query_one("#g-ack", FieldRow).display = nvidia
        if nvidia:
            ack = self.query_one("#g-ack", FieldRow)
            ack.query_one(".field-label", Static).update("Notice")
        self._fill_mac_row()
        self.recompute()

    def _fill_releases(self) -> None:
        row = self.query_one("#g-driver", ChoiceRow)
        if self.releases:
            return

        def work() -> list[dict]:
            return nullmoth_finish.versions()

        def fill(found: list[dict] | None) -> None:
            found = found or []
            lines = nullmoth_cli.version_lines(found)
            options = [Option(r["tag"], line) for line, r in zip(lines, found)] or [Option("latest", "Latest")]
            self.releases = found or [{"tag": "latest", "prerelease": False}]
            row.set_options(options, next((r["tag"] for r in found if not r["prerelease"]), options[0].value))
            row.set_text("", "newest stable release is preselected")

        def runner() -> None:
            try:
                found = work()
            except Exception:  # noqa: BLE001 - no network: the install resolves "latest" itself and reports the error
                found = None
            try:
                self.app.call_from_thread(fill, found)
            except Exception:  # noqa: BLE001
                pass

        self.run_worker(runner, thread=True, exit_on_error=False, group="gpu-releases")

    def on_choice_row_changed(self, event: ChoiceRow.Changed) -> None:
        rid = event.row.id
        if rid == "g-card":
            self._card_changed_ui()
        elif rid == "g-mac":
            self._mac_touched = True
            self._mac_detail()
            self.recompute()
        elif rid in ("g-display", "g-optional"):
            self.recompute()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.mode != "choose":
            return
        # the typed "I understand" changes the plan that will run (the only-display-adapter override): plan again
        typed = self.query_one("#g-confirm", FieldRow).value == CONFIRM_PHRASE
        if typed != self._typed:
            self._typed = typed
            self.recompute()
        self.refresh_buttons()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_primary()

    def choice_values(self) -> tuple[GpuCard | None, int | None, str]:
        card = self._selected_card()
        mac = self.query_one("#g-mac", ChoiceRow).value
        vmid = int(mac) if mac.isdigit() else None
        if card is None:
            return None, vmid, "console"
        display = {"amd": self.query_one("#g-display", ChoiceRow).value, "intel": "gpu"}.get(card.vendor, "console")
        return card, vmid, display

    def recompute(self) -> None:
        """Work out the preview of the current choices off the UI thread; a newer call wins."""
        card, vmid, display = self.choice_values()
        self._token += 1
        token = self._token
        if card is None:
            self.preview = None
            self.paint_preview()
            return
        s = self.s
        no_console = self.query_one("#g-confirm", FieldRow).value == CONFIRM_PHRASE
        optional = self.query_one("#g-optional", ChoiceRow).value == "on"

        def work() -> Preview:
            try:
                prev = s.preview(card, allow_no_console=False, write_optional=optional)
                wants = needs_typed(prev["plan"])
                if wants and no_console:  # the phrase is typed: show the plan as it will run
                    prev = s.preview(card, allow_no_console=True, write_optional=optional)
            except CardChanged as exc:
                return Preview(card, vmid, display, error=str(exc))
            # the NVIDIA notice is gated by its typed phrase (can_approve), so the plan is previewed as accepted
            vm_plan = s.preview_vm(card, vmid, display, True) if vmid is not None else None
            return Preview(card, vmid, display, prev["items"], prev["plan"], vm_plan, typed_needed=wants)

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                log.exception("GPU preview failed")
                result = Preview(card, vmid, display, error=f"Could not work this out: {exc}")
            try:
                self.app.call_from_thread(self._preview_ready, token, result)
            except Exception:  # noqa: BLE001
                pass

        self.preview = None
        self.refresh_buttons()
        self.run_worker(runner, thread=True, exit_on_error=False, group="gpu-preview")

    def _preview_ready(self, token: int, result: Preview) -> None:
        if token != self._token or self.mode != "choose":
            return
        self.preview = result
        self.paint_preview()

    def paint_preview(self) -> None:
        """The right panel, the Before you continue section, the one-line reason and the primary button."""
        pal = self.app.get_css_variables()
        prev = self.preview
        need, part, before = Text(), Text(), Text()
        reason = ""
        if prev is not None and not prev.error:
            lines: list[tuple[str, str, str]] = []
            if prev.items:
                failing = prev.bios_failing
                lines.append(("✓" if not failing else "●", "BIOS settings",
                              "already right" if not failing else f"{len(failing)} to change"))
            host = prev.host_work
            files = len(prev.plan.get("changes", []))
            if prev.plan and not prev.plan["refusals"]:
                lines.append(("●" if host else "✓", "Host setup",
                              (f"{files} file{'s' if files != 1 else ''} change" if files else "a few commands")
                              if host else "already right"))
            if prev.plan.get("reboot_required"):
                lines.append(("●", "One restart", REBOOT_SECONDS))
            if prev.vmid is not None:
                lines.append(("●", "Attach to the Mac", "automatic"))
            for glyph, name, tail in lines:
                need.append(glyph + " ", style=pal["success"] if glyph == "✓" else pal["accent"])
                need.append(name, style=pal["muted"] if glyph == "✓" else "")
                need.append("  " + tail + "\n", style=pal["muted"])
            part = self._part(prev)
            before = self._before(prev, pal)
            refusals = prev.refusals
            reason = ("Refused: " + refusals[0]) if refusals else ""
        elif prev is not None:
            reason = prev.error
        elif not any(c.selectable for c in self.cards) and self.mode == "choose" and self.cards is not None \
                and self.query("#g-need"):
            need = Text("No card here can run macOS graphics. See the docs for cards that work.",
                        style=pal["error"])
        try:
            self.query_one("#g-need", Static).update(need)
            self.query_one("#g-part", Static).update(part)
            self.query_one("#g-before", Static).update(before)
            self.query_one("#g-reason", Static).update(Text(reason, style=pal["error"]))
        except Exception:  # noqa: BLE001 - mode changed under us
            return
        self._sync_phrase_fields(prev)
        self.refresh_buttons()

    def _part(self, prev: Preview) -> Text:
        pal = self.app.get_css_variables()
        steps: list[tuple[str, str]] = []
        if prev.bios_failing:
            steps.append(("Change the BIOS settings", "we show exactly how"))
        steps.append(("Press enter", ""))
        if prev.plan.get("reboot_required"):
            steps += [("Restart when asked", ""), ("Open osx-next again", "it finishes by itself")]
        elif prev.vmid is None:
            steps.append(("Pick a Mac afterwards", ""))
        out = Text()
        for idx, (head, tail) in enumerate(steps[:3], start=1):
            out.append(f"{idx} ", style=pal["muted"])
            out.append(head + "\n")
            if tail:
                out.append("  " + tail + "\n", style=pal["muted"])
        return out

    def _before(self, prev: Preview, pal: dict) -> Text:
        """Today's warnings, refusals, notes and the NVIDIA notice, under one heading."""
        parts: list[tuple[str, str]] = []
        card = prev.card
        for line in plan_lines(prev.plan) if prev.plan else []:
            if line.startswith("OK: "):
                continue
            tone = pal["error"] if line.startswith(("Refused", "HARD")) else pal["warning"]
            parts.append((line, tone))
        if prev.vm_plan is not None:
            parts += [("Refused: " + i, pal["error"]) for i in prev.vm_plan.issues]
            parts += [(h, pal["muted"]) for h in prev.vm_plan.hints]
            parts += [("Warning: " + w, pal["warning"]) for w in prev.vm_plan.warnings]
        if card is not None and card.vendor == "nvidia":
            parts += [(NVIDIA_DISPLAY_NOTE, pal["muted"]), (NOTICE_TITLE, "bold"), (NOTICE_BODY, pal["muted"])]
        if card is not None and card.vendor == "intel":
            parts.append((INTEL_DISPLAY_NOTE, pal["muted"]))
        if not parts:
            return Text()
        out = Text("Before you continue\n", style="bold")
        for line, tone in parts:
            out.append(line + "\n", style=tone)
        return out

    def _sync_phrase_fields(self, prev: Preview | None) -> None:
        confirm = self.query_one("#g-confirm", FieldRow)
        ack = self.query_one("#g-ack", FieldRow)
        want = bool(prev and prev.typed_needed)
        if want != confirm.display:
            confirm.display = want
            if not want:
                confirm.input.value = ""
        confirm.query_one(".field-label", Static).update("")
        confirm.set_suffix(f'Type "{CONFIRM_PHRASE}" to continue')
        nvidia = bool(prev and prev.card and prev.card.vendor == "nvidia")
        ack.display = nvidia
        ack.set_suffix(f"{ACK_LABEL} Type '{ACK_PHRASE}' to confirm")

    def refresh_buttons(self) -> None:
        if self.mode != "choose":
            return
        try:
            button = self.query_one("#g-primary", Button)
        except Exception:  # noqa: BLE001
            return
        button.disabled = not self.can_approve()

    def can_approve(self) -> bool:
        prev = self.preview
        if prev is None or prev.error or prev.card is None:
            return False
        if prev.refusals:
            return False
        if prev.typed_needed and self.query_one("#g-confirm", FieldRow).value != CONFIRM_PHRASE:
            return False
        if prev.card.vendor == "nvidia" and self.query_one("#g-ack", FieldRow).value != ACK_PHRASE:
            return False
        return True

    def approve_record(self, prev: Preview, typed: bool) -> dict:
        release = self.query_one("#g-driver", ChoiceRow).value if prev.card and prev.card.vendor == "nvidia" else ""
        steps = prev.vm_plan.steps if prev.vm_plan is not None else []
        return {
            "host_fp": plan_fingerprint(prev.plan) if prev.plan else "",
            "vm_fp": vm_fingerprint(prev.card.bdf, prev.vmid, prev.display, steps) if prev.card else "",
            "host_files": len(prev.plan.get("changes", [])) if prev.plan else 0,
            "host_work": prev.host_work,
            "restart": bool(prev.plan.get("reboot_required")) if prev.plan else False,
            "vm": prev.vmid, "release": release, "typed": typed,
            "write_optional": self.query_one("#g-optional", ChoiceRow).value == "on",
            "allow_no_console": typed and prev.typed_needed,
        }

    def action_primary(self) -> None:
        if self.working:
            return
        if self.mode == "choose":
            self.approve()
        elif self._primary_action is not None:
            self._primary_action()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """The primary button runs the primary action of the screen, any other button the secondary one."""
        event.stop()
        button = event.button
        if button.id == "g-primary":
            self.approve()
            return
        action = self._primary_action if button.has_class("primary") else self._secondary_action
        if action is not None and not self.working:
            action()

    # ── approving ────────────────────────────────────────────────────

    def approve(self) -> None:
        if not self.can_approve() or self.working:
            return
        prev = self.preview
        assert prev is not None and prev.card is not None
        card = prev.card
        if card.stopped_users:
            lines = list(card.notes) + ["Use this card anyway?"]

            def answer(result: str | None) -> None:
                if result == "yes":
                    self._choose(prev, shared=True)

            self.app.push_screen(ConfirmDialog("This card is listed by other VMs", lines, confirm="Use it anyway"),
                                 answer)
            return
        self._choose(prev, shared=False)

    def _choose(self, prev: Preview, shared: bool) -> None:
        s, card = self.s, prev.card
        assert card is not None
        ack = self.query_one("#g-ack", FieldRow).value == ACK_PHRASE
        typed = self.query_one("#g-confirm", FieldRow).value == CONFIRM_PHRASE
        record = self.approve_record(prev, typed)

        def work() -> list[str]:
            return s.choose(card.bdf, prev.vmid, prev.display, ack, shared, confirmed=card.stopped_users)

        def done(issues: list[str]) -> None:
            if issues:
                self.say("\n".join("Refused: " + i for i in issues), fail=True)
                self.recompute()
                return
            s.approve(record)
            self.durations.clear()
            self.drive()

        self.job(work, done)

    # ── p: see every change ──────────────────────────────────────────

    def action_changes(self) -> None:
        if self.mode in ("choose",):
            prev = self.preview
            if prev is None or prev.error:
                return
            self.app.push_screen(ChangesDialog(self._changes_text(prev.plan, prev.vm_plan, prev.card, prev.vmid)))
        elif self.mode in ("run", "bios", "first_boot"):
            self.run_worker(self._changes_from_state, thread=True, exit_on_error=False, group="gpu-changes")

    def _changes_from_state(self) -> None:
        s = self.s
        try:
            plan = s.host_plan(write_optional=bool((s.approval or {}).get("write_optional")),
                               allow_no_console=bool((s.approval or {}).get("allow_no_console")))
            vm_plan = s.vm_plan(allow_stop=True) if s.state["vm"] is not None else None
        except CardChanged as exc:
            plan, vm_plan = None, None
            text = str(exc)
        else:
            text = self._changes_text(plan, vm_plan, None, s.state["vm"])
        self.app.call_from_thread(lambda: self.app.push_screen(ChangesDialog(text)))

    @staticmethod
    def _changes_text(plan: dict | None, vm_plan: Any, card: GpuCard | None, vmid: int | None) -> str:
        lines: list[str] = []
        if plan is not None:
            lines.append("Host files and commands (each file is backed up first, in "
                         f"{host_setup.BACKUP_ROOT}):")
            preview = host_setup.render_preview(plan)
            lines.append(preview if preview else "  nothing to change on the host")
        if vm_plan is not None:
            lines += ["", f"VM {vmid} settings (a config snapshot is saved first):"]
            lines += [f"  {i:02d}. {st.title}\n      {st.command}" for i, st in enumerate(vm_plan.steps, start=1)]
            lines += ["Refused: " + i for i in vm_plan.issues] + vm_plan.hints + ["Warning: " + w for w in vm_plan.warnings]
        elif vmid is None:
            lines += ["", "No Mac is chosen yet: the VM settings are shown when you pick one."]
        return "\n".join(lines)

    # ── the run view (timeline and box) ──────────────────────────────

    def phases(self) -> list[tuple[str, str, str]]:
        """(id, title while it runs, title once done) for the steps that apply to this run."""
        s = self.s
        approval = s.approval or {}
        vmid = s.state["vm"]
        name = self._vm_name(vmid) if vmid is not None else "the Mac"
        host = bool(approval.get("host_work")) or bool(s.state["host_changes"]) or 4 in s.state["done"]
        phases: list[tuple[str, str, str]] = []
        if host:
            files = approval.get("host_files") or len([c for c in s.state["host_changes"] if not c.get("reverted")])
            phases.append(("backup", "Back up files", f"Backed up {files} file{'s' if files != 1 else ''}"))
            phases.append(("host", "Set up the host", "Set up the host"))
        if approval.get("restart") or s.state.get("reboot_needed") or 5 in s.state["done"]:
            phases.append(("restart", "Restart this host", "Restarted this host"))
        phases.append(("check", "Check the card is free", "Checked the card is free"))
        if vmid is not None:
            phases.append(("attach", f"Attach it to {name}", f"Attached it to {name}"))
            phases.append(("start", "Start the Mac", "Started the Mac"))
        return phases

    def phase_status(self, pid: str) -> str:
        s = self.s
        done_steps = set(s.state["done"]) | set(s.state["skipped"])
        step = s.state["step"]
        finished = pid in {"backup": "", "host": ""} and 4 in done_steps or \
            (pid == "restart" and 5 in done_steps) or (pid == "check" and 6 in done_steps) or \
            (pid == "attach" and 7 in done_steps) or (pid == "start" and (8 in done_steps or step >= 8))
        if finished:
            return "ok"
        order = [p[0] for p in self.phases()]
        first_open = next((p for p in order if self._open(p)), "")
        return "active" if pid == first_open else "pending"

    def _open(self, pid: str) -> bool:
        s = self.s
        done_steps = set(s.state["done"]) | set(s.state["skipped"])
        return not ((pid in ("backup", "host") and 4 in done_steps) or (pid == "restart" and 5 in done_steps)
                    or (pid == "check" and 6 in done_steps) or (pid == "attach" and 7 in done_steps)
                    or (pid == "start" and (8 in done_steps or s.state["step"] >= 8)))

    def timeline(self, first_boot: bool = False) -> Text:
        pal = self.app.get_css_variables()
        out = Text()
        for idx, (pid, active, past) in enumerate(self.phases()):
            status = self.phase_status(pid)
            if idx:
                out.append("\n")
            if status == "ok":
                out.append("✓ ", style=pal["success"])
                title, tail = past, self._clock(pid)
                out.append(title, style=pal["muted"])
            elif status == "active":
                waiting = pid == "restart" and self.waiting_for_restart
                out.append("◐ ", style=pal["accent"])
                title, tail = active, "your turn" if waiting else self._clock(pid)
                out.append(title, style="bold")
            else:
                out.append("· ", style=pal["muted"])
                title, tail = active, ""
                out.append(title, style=pal["muted"])
            out.append(" " * max(1, 36 - len(title) - len(tail)) + tail, style=pal["accent"] if tail == "your turn" else pal["muted"])
        if first_boot and self.s.approval and self.s.approval.get("restart"):
            out.append("\n\nFinished without a question\nafter the restart.", style=pal["muted"])
        return out

    def _clock(self, pid: str) -> str:
        secs = self.durations.get(pid)
        return "" if secs is None else f"{int(secs) // 60}:{int(secs) % 60:02d}"

    def start_phase(self, pid: str) -> None:
        self._phase_started[pid] = time.monotonic()

    def end_phase(self, pid: str) -> None:
        started = self._phase_started.pop(pid, None)
        if started is not None:
            self.durations[pid] = time.monotonic() - started

    async def show_run(self, title: str, lines: list[str], buttons: list[tuple[str, str, str]],
                       tone: str = "") -> None:
        """The run view: the timeline on the left, a box on the right."""
        pal = self.app.get_css_variables()
        self.box_title, self.box_lines, self.box_buttons = title, lines, buttons
        head = self._vm_name(self.s.state["vm"]) if self.s.state["vm"] is not None else ""
        name = short_card_name(self.s.state["gpu"]["name"]) if self.s.state["gpu"] else "the card"
        text = f"Setting up the {name}" + (f" for {head}" if head else "")
        box_text = Text()
        for idx, line in enumerate(lines):
            if idx:
                box_text.append("\n")
            box_text.append(line, style=self._line_style(line, pal))
        widgets: list[Widget] = [
            Static(Text(text, style="bold"), id="g-run-title"),
            Horizontal(
                Static(self.timeline(), id="g-timeline"),
                Vertical(
                    Static(Text(title, style="bold"), id="g-box-title"),
                    Static(box_text, id="g-box-text"),
                    Static("", id="g-result"),
                    Horizontal(*[Button(label, id=bid, classes=kind) for bid, label, kind in buttons],
                               id="g-box-buttons"),
                    id="g-box", classes=tone),
                id="g-run-cols"),
            Static(Text("", style=pal["muted"]), id="g-run-note"),
        ]
        await self.set_mode("run", widgets)
        self.paint_note()
        if self.result_text:
            self.say(self.result_text, self.result_fail)
        if buttons:
            try:
                self.query_one(f"#{buttons[-1][0]}", Button).focus()
            except Exception:  # noqa: BLE001
                pass

    def _line_style(self, line: str, pal: dict) -> str:
        if line.startswith(("Refused", "[!!]", "HARD", "Failed")):
            return pal["error"]
        if line.startswith(("Warning", "Careful", "!")):
            return pal["warning"]
        if line.startswith(("[ok]", "Note", "OK:")):
            return pal["muted"]
        return ""

    def paint_run(self) -> None:
        try:
            self.query_one("#g-timeline", Static).update(self.timeline())
            if self.progress:
                self.query_one("#g-box-text", Static).update(Text(self.progress))
        except Exception:  # noqa: BLE001 - not in the run view yet
            pass

    def paint_note(self) -> None:
        pal = self.app.get_css_variables()
        note = Text()
        if any(self.phase_status(p[0]) == "active" and p[0] == "restart" for p in self.phases()):
            note.append("✓ ", style=pal["success"])
            note.append("Safe to leave here. ", style="bold")
            note.append("Choose Later and your Macs page shows a reminder to finish.", style=pal["muted"])
        elif self.s.state["vm"] is not None and self.s.state["step"] >= 3:
            note.append("The last steps run by themselves.", style=pal["muted"])
        if self.backup_note:
            note.append(("\n" if note.plain else "") + self.backup_note, style=pal["muted"])
        try:
            self.query_one("#g-run-note", Static).update(note)
        except Exception:  # noqa: BLE001
            pass

    def set_actions(self, primary: Callable[[], None] | None, secondary: Callable[[], None] | None = None) -> None:
        self._primary_action, self._secondary_action = primary, secondary

    # ── BIOS (only when the check says a setting must change) ────────

    def step_bios(self) -> None:
        s = self.s
        width = max(60, min((self.size.width or self._width or 100) - 4, 120))

        def work() -> Any:
            items = s.bios_items()
            return items, s.bios_card(items, max_width=width), s.running_vms(), s.checked_fields(items)

        self.job(work, self._bios_done, "Checking the BIOS settings...")

    def _bios_done(self, result: Any) -> None:
        s = self.s
        items, card, running, checked = result
        if card is None:
            s.complete(3)  # checked and fine: done, not skipped
            self.drive()
            return
        self.bios_card = card
        text = Text("\n".join(pack_fields(checked, max(60, (self.size.width or 100) - 6))) + "\n\n")
        body = Text(card["text"])
        for line_idx, start, end in qr_spans(card["text"], card["qr"]):
            offset = sum(len(ln) + 1 for ln in card["text"].split("\n")[:line_idx])
            body.stylize("black on white", offset + start, offset + end)
        text.append_text(body)
        text.append(f"\n\nSaved to {card['path']}")
        if running:
            text.append("\n\nRunning VMs that a restart will stop: " + ", ".join(f"{i} ({n})" for i, n in running))
        self.run_worker(self._show_bios(text, card["firmware_reboot"]), exclusive=True, group="gpu-mode")

    async def _show_bios(self, text: Text, firmware: bool) -> None:
        widgets: list[Widget] = [
            Static(Text("One thing only you can do", style="bold"), id="g-run-title"),
            Static(Text("Take a photo of this screen, then change these settings in the BIOS.",
                        style=self.app.get_css_variables()["muted"]), id="g-bios-sub"),
            VerticalScroll(Static(text, id="g-bios-text"), id="g-bios-scroll"),
            Static("", id="g-result"),
            Horizontal(
                *([Button("Restart into BIOS", id="g-primary-bios", classes="primary")] if firmware else []),
                Button("Check again", id="g-recheck", classes="secondary" if firmware else "primary"),
                id="g-box-buttons"),
        ]
        self.box_buttons = [("g-recheck", "Restart into BIOS" if firmware else "Check again", "primary")]
        await self.set_mode("bios", widgets)
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

    # ── stage 4: the host ────────────────────────────────────────────

    def step_host(self, force_ask: bool = False) -> None:
        s = self.s
        approval = s.approval or {}
        self.start_phase("backup")

        def work() -> dict:
            return s.host_plan(allow_no_console=bool(approval.get("allow_no_console")),
                               write_optional=bool(approval.get("write_optional")))

        self.job(work, lambda plan: self._host_planned(plan, force_ask), "Working out what this host needs...")

    def _host_planned(self, plan: dict, force_ask: bool) -> None:
        s = self.s
        approval = s.approval or {}
        if not s.plan_needs_work(plan) and not plan["refusals"] and s.state.get("verify_retry"):
            s.state["verify_retry"] = False
            s.save()
            self._guidance("The verify step found a problem, but there is nothing left for this tool to change.",
                           ["Fix the failing item in the verify list (see the docs), then check again."],
                           "Check again", lambda: (s.goto(6), self.drive()))
            return
        if (plan.get("optional") or not s.plan_needs_work(plan)) and not plan["refusals"]:
            s.complete(4, skipped=True)
            if 5 not in s.state["done"]:
                s.complete(5, skipped=True)
            self.drive()
            return
        same = approval.get("host_fp") == plan_fingerprint(plan) and not plan["refusals"]
        if same and not force_ask:
            self._apply_host(plan, bool(approval.get("typed")))
            return
        lines = plan_lines(plan) + ["", "This is exactly what will change (each file is backed up first):",
                                    host_setup.render_preview(plan)]
        typed_needed = needs_typed(plan)

        def go() -> None:
            if plan["refusals"] and not typed_needed:
                return
            if typed_needed:
                typed = self.query_one("#g-typed", Input).value.strip() == CONFIRM_PHRASE
                if not typed:
                    self.say(f'Type "{CONFIRM_PHRASE}" first.', fail=True)
                    return
                if plan["refusals"]:
                    s.approve({**approval, "allow_no_console": True, "typed": True})
                    self.step_host(force_ask=True)
                    return
                self._apply_host(plan, True)
            else:
                self._apply_host(plan, False)

        self._ask_go = go
        self.run_worker(self._ask_host(lines, typed_needed), exclusive=True, group="gpu-mode")

    async def _ask_host(self, lines: list[str], typed: bool) -> None:
        await self.show_run("This differs from what you approved" if self.s.approval else "Apply these changes",
                            lines, [("g-ask", "Apply these changes", "primary")])
        if typed:
            box = self.query_one("#g-box", Vertical)
            await box.mount(Static(f'Type "{CONFIRM_PHRASE}" to continue'), Input(id="g-typed"),
                            before=self.query_one("#g-box-buttons"))
        self.set_actions(lambda: self._ask_go() if self._ask_go else None)

    def _apply_host(self, plan: dict, typed: bool) -> None:
        s = self.s
        self.start_phase("backup")

        def done(res: dict) -> None:
            if not res["ok"]:
                self.say(f"Failed: {res['error']}" + (" The files were put back." if res["rolled_back"] else ""),
                         fail=True)
                self._guidance("The host setup did not finish", [res["error"]], "Try again", lambda: self.step_host())
                return
            self.end_phase("backup")
            self.durations.setdefault("host", self.durations.get("backup", 0.0))
            self.backup_note = f"Backups are in {res['backup_dir']}. Undo everything with u."
            self.drive()

        self.job(lambda: s.host_apply(plan, typed), done, "Applying...")

    def _guidance(self, title: str, lines: list[str], label: str, action: Callable[[], None],
                  secondary: tuple[str, Callable[[], None]] | None = None) -> None:
        """A box with one primary action (and optionally a second button), in the run view."""
        buttons = [("g-primary-run", label, "primary")]
        if secondary:
            buttons.insert(0, ("g-secondary", secondary[0], "secondary"))
        self.run_worker(self.show_run(title, lines, buttons), exclusive=True, group="gpu-mode")
        self.set_actions(action, secondary[1] if secondary else None)

    # ── stage 5: the one restart ─────────────────────────────────────

    def step_restart(self) -> None:
        s = self.s
        if not s.state.get("reboot_needed"):
            s.complete(5, skipped=True)
            self.drive()
            return

        def work() -> Any:
            if not s.reboot_pending():
                return None
            s.pause()
            running = s.running_vms()
            installing = []
            for vmid, name in running:
                state = read_driver_state(vmid)
                if state.installing:
                    minutes = int(max(0, time.time() - state.started_at) // 60) if state.started_at else 0
                    installing.append((vmid, name, minutes))
            return running, installing

        self.job(work, self._restart_ready)

    def _restart_ready(self, result: Any) -> None:
        if result is None:
            self.drive()
            return
        running, installing = result
        lines = ["The host needs it to hand the card over. " + REBOOT_SECONDS.capitalize() + ".", "",
                 "What a restart means right now"]
        stuck = {vmid for vmid, _n, _m in installing}
        for vmid, name in running:
            if vmid not in stuck:
                lines.append(f"● {name} is shut down cleanly first")
        for vmid, name, minutes in installing:
            lines.append(f"! {name} is still installing, {minutes} min in")
            lines.append("  A restart now interrupts that install.")
        if not running:
            lines.append("No VM is running.")
        lines.append(f"If you restart later, run: {self.s.state['resume_cmd']}")
        self.start_phase("restart")
        self.waiting_for_restart = True
        self.run_worker(self.show_run("Time for the one restart", lines,
                                      [("g-later", "Later", "secondary"), ("g-restart", "Restart now", "primary")]),
                        exclusive=True, group="gpu-mode")
        self.set_actions(lambda: self._restart_confirm(running, installing), self._restart_later)

    def _restart_later(self) -> None:
        self.s.pause()
        self.app.notify("Saved. Your Macs page shows a reminder to finish.")
        self.app.pop_screen()

    def _restart_confirm(self, running: list, installing: list) -> None:
        names = ", ".join(f"{i} ({n})" for i, n in running)
        lines = ["The host restarts now."]
        lines.append(f"These VMs will be shut down: {names}." if running else "No VM is running.")
        if installing:
            lines.append("An install in progress will be interrupted.")

        def answer(result: str | None) -> None:
            if result != "yes":
                return
            ok, out = self.s.reboot_host()
            self.say("Restarting..." if ok else "Could not start the restart: " + out, fail=not ok)

        self.app.push_screen(ConfirmDialog("Restart this host now?", lines, confirm="Restart now", danger=True),
                             answer)

    # ── stage 6: check the card is free ──────────────────────────────

    def step_verify(self) -> None:
        s = self.s
        self.start_phase("check")
        self.job(s.verify, self._verify_done, "Checking the host...")

    def _verify_done(self, checks: list[dict]) -> None:
        s = self.s
        marks = {"ok": "ok", "fail": "!!", "warn": "??", "info": ".."}
        body = [f"[{marks[c['status']]}] {c['label']}" + (f" - {c['detail']}" if c["detail"] else "") for c in checks]
        fails = [c for c in checks if c["status"] == "fail"]
        if not fails:
            self.end_phase("check")
            self.drive()
            return
        if any("Reboot the host" in c["detail"] for c in fails) and s.rebooted_since_apply():
            self._guidance("The card is not free yet", body + [
                "", "The host has rebooted but the kernel arguments are still not active. Check that the boot "
                "loader picked them up (GRUB: /boot/grub/grub.cfg, systemd-boot: proxmox-boot-tool refresh), "
                "then check again."], "Check again", self.step_verify)
            return
        if any("Reboot the host" in c["detail"] for c in fails):
            s.mark_reboot_needed()
            self.say("The change is saved but not active yet. Restart first.", fail=True)
            self.drive()
            return
        self.fix = fails[0]["fix"]
        if self.fix == "scan":
            self._job_card_changed(CardChanged(fails[0]["detail"]))
            return
        label = {"bios": "Back to the BIOS step", "host": "Back to host setup"}.get(self.fix, "Check again")

        def action() -> None:
            target = {"bios": 3, "host": 4}.get(self.fix)
            if target:
                s.send_back(target)
            self.drive()

        self._guidance("The card is not free yet", body, label, action)

    # ── stage 7: attach ──────────────────────────────────────────────

    def step_attach(self) -> None:
        s = self.s
        vendor = s.state["gpu"]["vendor"]
        if s.state["vm"] is None:
            self.job(lambda: s.vm_options(vendor), self._pick_mac, "Looking for your Macs...")
            return
        self.job(lambda: self._attach_probe(), self._attach_probed, "Checking the Mac and the card...")

    def _attach_probe(self) -> Any:
        s = self.s
        vmid = s.state["vm"]
        info = fetch_vm_info(vmid, adapter=s.pve)
        running = bool(info and info.status == "running")
        plan = s.vm_plan(allow_stop=True)
        return plan, running

    def _attach_probed(self, result: Any) -> None:
        s = self.s
        plan, running = result
        approval = s.approval or {}
        if not plan.ok:
            lines = ["Refused: " + i for i in plan.issues] + plan.hints + ["Warning: " + w for w in plan.warnings]
            self._guidance("The Mac cannot take the card yet", lines, "Check again", self.step_attach,
                           ("Pick another Mac", self._pick_other))
            return
        fp = vm_fingerprint(s.state["gpu"]["bdf"], s.state["vm"], s.state["display"], plan.steps)
        if approval.get("vm_fp") not in ("*", fp):
            lines = ["Refused: " + i for i in plan.issues] + plan.hints + ["Warning: " + w for w in plan.warnings]
            lines += ["", f"This is what will change on VM {s.state['vm']} (a config snapshot is saved first):"]
            lines += [f"  {i:02d}. {st.title}" for i, st in enumerate(plan.steps, start=1)]
            self._guidance("This differs from what you approved", lines, "Attach the card now",
                           lambda: self._attach_go(plan, running))
            return
        self._attach_go(plan, running)

    def _pick_other(self) -> None:
        self.s.state["vm"] = None
        self.s.save()
        self.step_attach()

    def _attach_go(self, plan: Any, running: bool) -> None:
        s = self.s
        if running:
            vmid = s.state["vm"]

            def answer(result: str | None) -> None:
                if result == "yes":
                    self._attach_apply(stop=True)

            self.app.push_screen(ConfirmDialog(
                f"Stop {self._vm_name(vmid)} first?",
                [f"VM {vmid} is running and has to be stopped before it can take the card.",
                 "Whatever is open in the guest is closed without saving."], confirm="Stop it", danger=True), answer)
            return
        self._attach_apply(stop=False)

    def _pick_mac(self, options: list[VmOption]) -> None:
        """Decide later ended here: the user picks the Mac and approves its settings now."""
        s = self.s
        usable = [o for o in options if o.eligible or o.status == "running"]
        self.start_phase("attach")
        self.run_worker(self._show_pick(options, usable), exclusive=True, group="gpu-mode")

    async def _show_pick(self, options: list[VmOption], usable: list[VmOption]) -> None:
        s = self.s
        nvidia = s.state["gpu"]["vendor"] == "nvidia"
        pal = self.app.get_css_variables()
        rows: list[Widget] = [ChoiceRow("Mac", [Option(str(o.vmid), o.name or f"VM {o.vmid}", o.eligible or o.status == "running")
                                                for o in options] or [Option("", "none")],
                                        str(usable[0].vmid) if usable else "", id="g-pick")]
        if nvidia:
            rows.append(ChoiceRow("Driver", [Option("latest", "Latest")], "latest", id="g-driver"))
        widgets: list[Widget] = [
            Static(Text("Pick the Mac that gets the card", style="bold"), id="g-run-title"),
            Static(Text("The host is ready. The Mac must have finished its install.", style=pal["muted"])),
            VerticalScroll(*rows, Static("", id="g-pick-info"), id="g-form"),
            Static("", id="g-result"),
            Horizontal(Button("Attach it", id="g-pick-go", classes="primary", disabled=not usable), id="g-box-buttons"),
        ]
        self.box_buttons = [("g-pick-go", "Attach it", "primary")]
        await self.set_mode("run", widgets)
        self.set_actions(self._pick_attach)
        self.query_one("#g-pick", ChoiceRow).focus()
        if nvidia:
            self._fill_releases()

    def _pick_attach(self) -> None:
        s = self.s
        value = self.query_one("#g-pick", ChoiceRow).value
        if not value.isdigit():
            self.say("Pick a Mac first.", fail=True)
            return
        s.state["vm"] = int(value)
        s.save()
        # pressing Attach on this screen is the approval of the VM settings ("*"): the Mac was not known before
        record = {**(s.approval or {}), "vm": int(value), "vm_fp": "*"}
        if s.state["gpu"]["vendor"] == "nvidia":
            record["release"] = self.query_one("#g-driver", ChoiceRow).value
        s.approve(record)
        self.step_attach()

    def _attach_apply(self, stop: bool) -> None:
        s = self.s
        nvidia = s.state["gpu"]["vendor"] == "nvidia"
        release = (s.approval or {}).get("release") or "latest"
        note = "Setting up the NVIDIA card (about 15 minutes)..." if nvidia else "Attaching the card..."
        self.start_phase("attach")

        def progress(message: str) -> None:
            try:
                self.app.call_from_thread(self._progress, note + "\n\n" + message)
            except Exception:  # noqa: BLE001
                pass

        def work() -> Any:
            from ..executor import apply_plan
            from ..rollback import create_snapshot
            plan = s.vm_plan(allow_stop=stop)
            if not plan.ok:
                return plan, None
            s.begin_attach()  # the card must still be the chosen one: raises CardChanged before anything is written
            if nvidia:
                return plan, s.finish_nvidia(release, progress)
            create_snapshot(s.state["vm"])
            result = apply_plan(plan.steps, execute=True)
            if result.ok:
                s.pve.qm("start", str(s.state["vm"]))  # the Mac starts on the card
            return plan, result

        self.job(work, lambda res: self._attach_done(nvidia, res), note)

    def _progress(self, text: str) -> None:
        self.progress = text
        self.paint_run()

    def _attach_done(self, nvidia: bool, res: Any) -> None:
        s = self.s
        plan, result = res
        if result is None:
            self._guidance("The Mac cannot take the card yet", ["Refused: " + i for i in plan.issues],
                           "Check again", self.step_attach)
            return
        if nvidia:
            self._nvidia_done(result)
            return
        if not result.ok:
            self._guidance("Attaching failed", [f"Log: {result.log_path}"], "Try again", self.step_attach)
            return
        self.end_phase("attach")
        s.vm_applied()
        self.drive()

    def _nvidia_done(self, result: nullmoth_finish.FinishResult) -> None:
        s = self.s
        notes = ["Note: " + n for n in result.notes]
        if not result.verified:
            reason = result.detail or result.result or "the guest reported nothing"
            self._guidance("The driver is not confirmed",
                           notes + [f"The NullMoth driver is not confirmed: {reason}. " + nullmoth.FAILED_NOTE],
                           "Try again", self.step_attach)
            return
        lines = notes + [f"NullMoth {result.version} is installed and loaded ({result.kexts} extensions)."]
        if result.ip:
            lines += [f"ssh <user>@{result.ip}", f"vnc://{result.ip}"]
        if result.display != "yes":
            lines.append(nullmoth.DISPLAY_LINE)
        self.end_phase("attach")
        s.vm_applied()
        self.result_text, self.result_fail = "\n".join(lines), False
        self.drive()

    # ── moment 3: first boot ─────────────────────────────────────────

    async def _show_first_boot(self, troubleshooting: bool = False) -> None:
        s = self.s
        pal = self.app.get_css_variables()
        chosen = s.state["gpu"]
        nvidia = chosen["vendor"] == "nvidia"
        vmid = s.state["vm"]
        name = self._vm_name(vmid) if vmid is not None else "the Mac"
        title = f"{name} is starting on the {short_card_name(chosen['name'])}"
        body = Text(s.first_boot_text())
        qr = s.first_boot_qr()
        if qr:
            body.append("\n\n")
            body.append("\n".join(qr), style="black on white")
        if troubleshooting:
            body = Text("\n\n".join(TROUBLESHOOTING.values()))
        extra = self.result_text if self.result_text and not self.result_fail else ""
        question = "" if nvidia or troubleshooting else "\n\nDo you see the macOS desktop?"
        if extra:
            body.append("\n\n" + extra)
        body.append(question)
        buttons: list[Widget]
        if nvidia:
            buttons = [Button("Done", id="g-done", classes="primary")]
        elif troubleshooting:
            buttons = [Button("Back", id="g-back", classes="secondary"),
                       Button("Yes, all good", id="g-yes", classes="primary")]
        else:
            buttons = [Button("No, help me", id="g-no", classes="secondary"),
                       Button("Yes, all good", id="g-yes", classes="primary")]
        widgets: list[Widget] = [
            Static(Text(title, style="bold"), id="g-run-title"),
            Horizontal(Static(self.timeline(True), id="g-timeline"),
                       Vertical(Static(Text("Look at the monitor plugged into the card" if not nvidia and not
                                            troubleshooting else ("No signal? Try these" if troubleshooting
                                                                  else "Your Mac is ready"), style="bold"),
                                       id="g-box-title"),
                                VerticalScroll(Static(body, id="g-box-text"), id="g-box-scroll"),
                                Static("", id="g-result"),
                                Horizontal(*buttons, id="g-box-buttons"), id="g-box"),
                       id="g-run-cols"),
            Static(Text("Not happy with it? u puts the host and the Mac back exactly as they were.",
                        style=pal["muted"]), id="g-run-note"),
        ]
        self.box_buttons = [("g-yes", "Done" if nvidia else "Yes, all good", "primary")]
        await self.set_mode("first_boot", widgets)
        self.set_actions(self.action_yes, (lambda: self.run_worker(self._show_first_boot(), exclusive=True,
                                                                    group="gpu-mode")) if troubleshooting
                         else self.action_no)
        self.query_one(f"#{buttons[-1].id}", Button).focus()

    def action_yes(self) -> None:
        if self.mode != "first_boot" or self.working:
            return
        self.s.finish()
        self.run_worker(self._show_finished(), exclusive=True, group="gpu-mode")

    def action_no(self) -> None:
        s = self.s
        if self.mode != "first_boot" or s.state["gpu"]["vendor"] == "nvidia":
            return
        self.run_worker(self._show_first_boot(troubleshooting=True), exclusive=True, group="gpu-mode")

    async def _show_finished(self) -> None:
        s = self.s
        widgets: list[Widget] = [
            Static(Text("Setup finished", style="bold"), id="g-run-title"),
            VerticalScroll(Static(Text(s.summary()), id="g-box-text"), id="g-box-scroll"),
            Static("", id="g-result"),
            Horizontal(Button("Start a new setup", id="g-new", classes="primary"), id="g-box-buttons"),
        ]
        self.box_buttons = [("g-new", "Start a new setup", "primary")]
        await self.set_mode("finished", widgets)
        self.set_actions(self._new_setup)

    def _new_setup(self) -> None:
        self.s.restart()
        self.fixed_vm = False
        self.vmid = None
        self.run_worker(self._show_choose(), exclusive=True, group="gpu-mode")

    # ── u: put everything back ───────────────────────────────────────

    def action_undo(self) -> None:
        s = self.s
        if self.working or not s.has_undo():
            return
        try:
            recorded = set(s.state["vm_records"]) | set(gpu.record_vmids())
            running = [i for i, n in s.running_vms() if i in recorded]
        except Exception:  # noqa: BLE001
            running = []
        lines = list(UNDO_LINES)
        if running:
            lines.append("Running VMs that were changed are stopped first: " + ", ".join(str(i) for i in running) + ".")

        def answer(result: str | None) -> None:
            if result == "yes":
                self._undo(bool(running))

        self.app.push_screen(ConfirmDialog("Put everything back?", lines, confirm="Put everything back",
                                           danger=True), answer)

    def _undo(self, allow_stop: bool) -> None:
        s = self.s

        def done(result: Any) -> None:
            ok, messages = result
            text = "\n".join(messages) or "Nothing to undo."
            if ok:
                self.s = Session()
                self.fixed_vm = False
                self.vmid = None
                self.app.push_screen(ChoiceDialog("Everything is back", text.split("\n"),
                                                  [Choice("ok", "Close", "primary")]),
                                     lambda _r: self.run_worker(self._show_choose(), exclusive=True,
                                                                group="gpu-mode"))
            else:
                self.say(text, fail=True)

        self.job(lambda: s.undo(allow_stop=allow_stop), done, "Undoing...")

    # ── keys shared by the run view ──────────────────────────────────

    def action_row(self, delta: int) -> None:
        widgets = [w for w in self.query("#g-form ChoiceRow, #g-form FieldRow Input") if w.display and
                   all(a.display for a in w.ancestors if a is not self.screen and hasattr(a, "display"))]
        if not widgets:
            return
        idx = widgets.index(self.focused) if self.focused in widgets else -1
        target = widgets[max(0, min(len(widgets) - 1, idx + delta))] if idx >= 0 else widgets[0]
        target.focus()

