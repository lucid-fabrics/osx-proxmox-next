"""Graphics card (GPU passthrough): choose and approve once, restart once, confirm it works.

A Session (gpu_setup.py) does the work and keeps its eight stages. This screen shows three moments: one
screen to choose and approve (with a BIOS screen only when the BIOS must change), a run that pauses at the
one restart and finishes by itself after it, and the first boot question. Each moment lives in its own
module (gpu_choose, gpu_bios, gpu_run, gpu_attach_step, gpu_boot); this one holds what they share."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from .. import gpu
from ..gpu_flow import card_facts, changes_text
from ..gpu_setup import CardChanged, GpuCard, Session, VmOption
from ..services import fetch_vm_info
from ..widgets.chrome import KeyBar, TopBar
from .base import Choice, ChoiceDialog, ConfirmDialog, OsxScreen
from .gpu_attach_step import AttachMixin
from .gpu_bios import BiosMixin
from .gpu_boot import BootMixin
from .gpu_choose import ChooseMixin
from .gpu_restart import RestartMixin
from .gpu_run import Guidance, RunMixin

log = logging.getLogger(__name__)

__all__ = ["GpuScreen", "ChangesDialog"]

KEPT_LINE = "Anything you edited since is kept and reported."


def undo_progress_text(plan: list[str], finished: int) -> str:
    """The undo in progress: ✓ for each line done, ◐ for the one at work, the rest waiting."""
    lines = [l for l in plan if not l.startswith("The host needs")]
    rows = [("✓ " if i < finished else "◐ " if i == finished else "  ") + line for i, line in enumerate(lines)]
    return "\n".join(["Undoing..."] + ([""] + rows if rows else []))


class ChangesDialog(ModalScreen):
    """Every change of the plan in one scrolling dialog: files, commands, VM settings, backups. The VM steps are
    one plain line each; x shows the exact commands instead."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("enter", "close", "Close"),
                Binding("p", "close", "Close"), Binding("x", "exact", "Exact commands"),
                Binding("down", "scroll_by(1)", show=False), Binding("up", "scroll_by(-1)", show=False),
                Binding("pagedown", "scroll_page(1)", show=False), Binding("pageup", "scroll_page(-1)", show=False),
                Binding("home", "scroll_edge(0)", show=False), Binding("end", "scroll_edge(1)", show=False)]

    def __init__(self, text: str, exact_text: str | None = None) -> None:
        super().__init__()
        self.body_text = text
        self.exact_text = exact_text if exact_text is not None else text
        self.exact = False

    def compose(self) -> ComposeResult:
        with Vertical(id="changes-box", classes="dialog"):
            yield Static("Every change", classes="dialog-title")
            yield Static("", classes="blank")
            with VerticalScroll(id="changes-scroll"):
                yield Static(Text(self.body_text), id="changes-text")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", classes="secondary")
        yield KeyBar(self._keys(), id="changes-keys")

    def _keys(self) -> list[tuple]:
        return [("↑↓", "Scroll", None), ("x", "Plain summary" if self.exact else "Exact commands", "screen.exact"),
                ("esc", "Close", "screen.close")]

    def on_mount(self) -> None:
        self.query_one("#changes-scroll", VerticalScroll).focus()

    def _scroll_to(self, y: float) -> None:
        """Move at once, never animated: a key pressed again mid-glide used to restart the glide, and the text
        moved about one line in fourteen presses."""
        box = self.query_one("#changes-scroll", VerticalScroll)
        box.scroll_to(y=max(0, min(box.max_scroll_y, y)), animate=False)

    def action_scroll_by(self, lines: int) -> None:
        self._scroll_to(self.query_one("#changes-scroll", VerticalScroll).scroll_y + lines)

    def action_scroll_page(self, direction: int) -> None:
        box = self.query_one("#changes-scroll", VerticalScroll)
        self._scroll_to(box.scroll_y + direction * max(1, box.size.height - 1))

    def action_scroll_edge(self, end: int) -> None:
        self._scroll_to(self.query_one("#changes-scroll", VerticalScroll).max_scroll_y if end else 0)

    def action_exact(self) -> None:
        self.exact = not self.exact
        self.query_one("#changes-text", Static).update(Text(self.exact_text if self.exact else self.body_text))
        self.query_one("#changes-keys", KeyBar).set_items(self._keys())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class GpuScreen(ChooseMixin, BiosMixin, RunMixin, RestartMixin, AttachMixin, BootMixin, OsxScreen):
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
    HELP_EXTRA = [("←  →  h  l", "Change the choice on this row")]

    def __init__(self, vmid: int | None = None, undo: bool = False, session: Session | None = None,
                 width: int = 0) -> None:
        super().__init__()
        self.vmid = vmid
        self.start_undo = undo
        self.s: Session = session or Session()
        self.mode = "loading"
        self.cards: list[GpuCard] = []
        self.host_fields: list[str] = []
        self.vm_pool: dict[str, list[VmOption]] = {}
        self.preview: Any = None
        self.working = False          # a worker runs: leaving or quitting needs care
        self._phase_started: dict[str, float] = {}
        self._token = 0
        self.releases: list[dict] = []
        self.fix = ""                 # what verify said to do
        self.bios_view: dict | None = None
        self.result_text = ""
        self.result_fail = False
        self.box_title = ""
        self.box_lines: list[str] = []
        self.box_buttons: list[tuple[str, str, str]] = []  # (id, label, kind)
        self.fixed_vm = vmid is not None
        self.progress = ""
        self.picked_plan: Any = None
        self._primary_action: Callable[[], None] | None = None
        self._secondary_action: Callable[[], None] | None = None
        self._ask_go: Callable[[], None] | None = None
        self.waiting_for_restart = False   # the restart box is up and it is the user's turn
        self._names: dict[int, str] = {}   # VM names read from Proxmox, for a flow that was reopened
        self._mac_touched = False          # the user moved the Mac row themselves
        self._width = width
        self.failed: dict[str, str] = {}   # step id -> why it was refused (five words or fewer): it shows a cross
        self._apply_base: int | None = None   # host changes already recorded when the host apply began
        self._restart_armed = 0.0          # enter does nothing in the restart box before this moment
        self._restarting = False

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
        chosen = self.s.state["gpu"]
        if self.mode == "first_boot" and chosen and chosen["vendor"] != "nvidia":
            items = [("y", "Yes, all good", "screen.yes"), ("n", "No, help me", "screen.no")]
        elif self.mode == "bios":
            items = [("enter", self._primary_label(), "screen.primary"), ("r", "Check again", "screen.recheck")]
        else:
            items = [("enter", self._primary_label(), "screen.primary")] if self.box_buttons else []
        if self.mode == "run":
            items += [("p", "See every change", "screen.changes")]
        if self.s.has_undo():
            items.append(("u", "Put everything back", "screen.undo"))
        late = self.mode == "run" and self.waiting_for_restart
        items.append(("esc", "Your Macs" if self.mode in ("first_boot", "finished") else ("Later" if late else "Leave"),
                      "screen.back"))
        items.append(("?", "Help", "app.help"))
        return items

    def _primary_label(self) -> str:
        return self.box_buttons[-1][1] if self.box_buttons else "Go"

    def on_resize(self) -> None:
        narrow = self.size.width < 90
        self.set_class(narrow, "narrow")
        for lines in self.query("#g-need"):
            lines.set_stack(narrow)   # under 90 columns every value sits on its own row under its label

    def on_mount(self) -> None:
        self.s.state["resume_cmd"] = self.s.state.get("resume_cmd") or "osx-next-cli gpu-setup"
        if self.start_undo:
            self.call_after_refresh(self._open_undo)
        self.set_interval(1.0, self.tick)   # the running time of the step that works moves every second
        self.begin()

    def _open_undo(self, tries: int = 0) -> None:
        """Open the undo question from the Mac's view: wait for whatever the screen is loading, then ask."""
        if self.working and tries < 100:
            self.set_timer(0.1, lambda: self._open_undo(tries + 1))
            return
        self.action_undo()

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
        if vmid is None or vmid in self._names or s.state["finished"]:
            self._begin_flow()
            return

        def lookup() -> None:
            """The Mac's name, for the title. Not a job: reading it never blocks leaving, quitting or undo."""
            try:
                info = fetch_vm_info(vmid, adapter=s.pve)
            except Exception:  # noqa: BLE001 - the title falls back to "VM <id>"
                info = None
            self.app.call_from_thread(self._named, vmid, info.name if info else "")

        self.run_worker(lookup, thread=True, exit_on_error=False, group="gpu-name")

    def _named(self, vmid: int, name: str) -> None:
        if name:
            self._names[vmid] = name
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
        elif step == 8 and s.state.get("start_failed"):
            self.run_worker(self.step_in_run(self.start_failed, False), exclusive=True, group="gpu-mode")
        elif step == 8:
            self.run_worker(self._show_first_boot(), exclusive=True, group="gpu-mode")
        else:
            todo = {3: self.step_bios, 4: self.step_host, 5: self.step_restart, 6: self.step_verify,
                    7: self.step_attach}.get(step, self.step_attach)
            # the run screen is up before the step starts (the restart step only needs it when none is up yet)
            self.run_worker(self.step_in_run(todo, step != 5), exclusive=True, group="gpu-mode")

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
        if self.mode == "run":   # the step that was running is the one that failed: it shows a cross and a way on
            current = next((p[0] for p in self.phases() if not self._is_done(p[0])), "")
            self._guidance(Guidance("Something went wrong", [message], "Try again", self.drive,
                                    fail=(current, "Step did not finish") if current else None))
            return
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
        if box.has_class("when-text"):   # a row only while there is something to say (the BIOS screen needs the room)
            box.display = bool(text)

    async def set_mode(self, mode: str, widgets: list[Widget]) -> None:
        self.mode = mode
        try:
            body = self.query_one("#gpu", Vertical)
        except NoMatches:   # the screen was closed while a step was starting
            return
        await body.remove_children()
        await body.mount(*widgets)
        self.refresh_footer()
        for bar in self.query(TopBar):
            bar.set_crumb(self.crumb())

    # ── keys ─────────────────────────────────────────────────────────

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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_primary()

    def action_row(self, delta: int) -> None:
        widgets = [w for w in self.query("#g-form ChoiceRow, #g-form FieldRow Input") if w.display and
                   all(a.display for a in w.ancestors if a is not self.screen and hasattr(a, "display"))]
        if not widgets:
            return
        idx = widgets.index(self.focused) if self.focused in widgets else -1
        target = widgets[max(0, min(len(widgets) - 1, idx + delta))] if idx >= 0 else widgets[0]
        target.focus()

    # ── p: see every change ──────────────────────────────────────────

    def action_changes(self) -> None:
        if self.mode == "choose":
            prev = self.preview
            if prev is None or prev.error or prev.card is None:
                return
            card = prev.card
            head = card_facts(card, self.host_fields)
            self.app.push_screen(ChangesDialog(changes_text(prev.plan, prev.vm_plan, prev.vmid, head),
                                               changes_text(prev.plan, prev.vm_plan, prev.vmid, head, exact=True)))
        elif self.mode in ("run", "bios", "first_boot"):
            self.run_worker(self._changes_from_state, thread=True, exit_on_error=False, group="gpu-changes")

    def _changes_from_state(self) -> None:
        s = self.s
        chosen = s.state["gpu"]
        try:
            plan = s.host_plan(write_optional=bool((s.approval or {}).get("write_optional")),
                               allow_no_console=bool((s.approval or {}).get("allow_no_console")))
            vm_plan = s.vm_plan(allow_stop=True) if s.state["vm"] is not None else None
        except CardChanged as exc:
            text = exact = str(exc)
        else:
            head = f"Card: {chosen['name']}\nAddress: {chosen['bdf']}\n" if chosen else ""
            backups = s.state.get("backup_dir")
            if backups:
                head += f"Backups of this setup: {backups}\n"
            text = changes_text(plan, vm_plan, s.state["vm"], head)
            exact = changes_text(plan, vm_plan, s.state["vm"], head, exact=True)
        self.app.call_from_thread(lambda: self.app.push_screen(ChangesDialog(text, exact)))

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
        try:
            lines = s.undo_plan()
        except Exception:  # noqa: BLE001 - the list is a courtesy: the question still has to be asked
            lines = []
        lines += ["", KEPT_LINE]
        if running:
            lines.append("Running VMs that were changed are stopped first: " + ", ".join(str(i) for i in running) + ".")

        def answer(result: str | None) -> None:
            if result == "yes":
                self._undo(bool(running))

        self.app.push_screen(ConfirmDialog("Put everything back?", lines, confirm="Put everything back",
                                           danger=True), answer)

    def _undo_progress(self, plan: list[str], finished: int) -> None:
        self.progress = undo_progress_text(plan, finished)
        self.paint_run()

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

        try:
            plan = s.undo_plan()
        except Exception:  # noqa: BLE001 - the list only shows progress
            plan = []

        def paint(finished: int) -> None:
            self.app.call_from_thread(self._undo_progress, plan, finished)

        self.job(lambda: s.undo(allow_stop=allow_stop, progress=paint), done, undo_progress_text(plan, 0))

