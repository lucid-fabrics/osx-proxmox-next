"""Graphics card of a Mac that already has one: the driver it runs, whether a newer one exists, and the update.

One approval, then the update runs by itself in a detached process (`osx-next-cli driver-update`) and this screen
reads its steps from the update state file, so a reopened terminal shows the same run. r undoes the driver update
(back to its snapshot). It acts on the driver only: undoing the card setup lives in the Graphics card flow and the
Mac's own view, because that undo takes the card off every Mac that has it."""

from __future__ import annotations

import copy
import dataclasses
import logging
import time
from dataclasses import dataclass, field

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Button, Static

from .. import driver_engine as engine, driver_spawn, igpu_engine, nullmoth_finish
from ..driver_core import DriverError, RunEnv
from ..driver_plan import DriverPlan, commands, prepare
from ..driver_run import put_back, remove_safety_snapshot, undo_target
from ..driver_targets import Choice
from ..gpu_flow import Before
from ..nullmoth import NOTICE_BODY, NOTICE_TITLE
from ..services import get_proxmox_adapter
from ..widgets.lines import Line, Lines
from ..widgets.rows import ChoiceRow, Option
from .base import COMMON_HELP, Choice as Pick, ChoiceDialog, ConfirmDialog, OsxScreen
from .gpu import ChangesDialog
from .gpu_before import BeforeDialog
from .gpu_boot import NO_SIGNAL_HELP, QUESTION, reflow_help
from .gpu_run import TIMELINE_WIDTH, clock
from .reading import ReadScroll

log = logging.getLogger(__name__)

__all__ = ["DriverScreen", "DriverView", "build_before", "load_view"]

MAX_RELEASES = 10
START_GRACE = 120      # seconds a spawned update gets to write its first state before it counts as failed
DRIVER_LABEL = 10
UNDO_LOSS_LINE = "Changes made in macOS since the update are lost. Enter keeps the Mac as it is."


@dataclass
class DriverView:
    """What the attached state shows: the plan for the latest release and the releases the pickers offer."""
    plan: DriverPlan
    releases: list[dict] = field(default_factory=list)   # newest first, at most MAX_RELEASES (NullMoth or WhateverXe)
    graphics: list[dict] = field(default_factory=list)   # Intel: the releases of the graphics package


def load_view(vmid: int, choice: Choice | None = None, releases: bool = True) -> DriverView:
    """Worker work: the plan (Proxmox, GitHub) and the picker's releases. A release list that cannot be read is empty."""
    plan = prepare(vmid, choice, reinstall=True)
    found: list[dict] = []
    graphics: list[dict] = []
    if releases and plan.kind in ("nvidia", "intel"):
        try:
            found = (nullmoth_finish.versions() if plan.kind == "nvidia"
                     else igpu_engine.list_releases("whateverxe"))[:MAX_RELEASES]
            if plan.kind == "intel":
                graphics = igpu_engine.list_releases("graphics")[:MAX_RELEASES]
        except Exception:  # noqa: BLE001 - the picker is a courtesy: the plan for the latest release stands
            log.debug("release list failed", exc_info=True)
    return DriverView(plan, found, graphics)


def build_before(plan: DriverPlan) -> Before:
    """The warnings, the facts and the NullMoth notice of one update, as "Before you continue" shows them."""
    out = Before(go_label="Reinstall" if plan.verdict == "current" else "Update driver", once=False)
    facts = [n for n in engine.before_facts(plan.kind, plan.snapshot, True, plan.older_snapshots)
             if plan.snapshot_ok or not n.startswith("A snapshot called")]
    out.notes = ["\n".join(facts)]      # one block: the dialog has to fit 80x24 with the notice and the phrase
    out.warnings = ["Warning: " + w for w in plan.warnings]
    if not plan.snapshot_ok:
        out.warnings.append(f"Warning: This Mac's storage cannot take snapshots ({plan.snapshot_reason}), so nothing "
                            "can put it back if the update goes wrong.")
        out.phrase, out.phrase_text = True, engine.NO_SNAPSHOT_PHRASE
    if plan.kind == "nvidia":
        out.nvidia, out.notice_title, out.notice_body = True, NOTICE_TITLE, NOTICE_BODY
    return out


class DriverScreen(OsxScreen):
    """Modes: loading, attached, run, first_boot."""

    BINDINGS = [
        Binding("enter", "primary", "Update driver"),
        Binding("p", "changes", "See every change"),
        Binding("r", "undo_driver", "Undo driver update"),
        Binding("up", "row(-1)", "Move", show=False),
        Binding("down", "row(1)", "Move", show=False),
        Binding("y", "yes", "Yes, all good"),
        Binding("n", "no", "No, help me"),
        Binding("escape", "back", "Leave"),
    ]
    HELP_TITLE = "Graphics card"
    HELP_EXTRA = [("←  →  h  l", "Pick another release")]
    AUTO_FOCUS = ""      # focus is set by _focus_row only: Textual's auto focus on resume can land on a row a mode change is removing

    def __init__(self, vmid: int, name: str = "") -> None:
        super().__init__()
        self.vmid, self.mac = vmid, name
        self.mode = "loading"
        self.view: DriverView | None = None
        self.choice = Choice()
        self.touched = False                 # the user moved the version row
        self.working = False
        self.spawned_at = 0.0
        self.helping = False
        self.error = ""
        self._label = "Update driver"
        self.undo_state: dict = {}
        self.undo_error = ""
        self._was_failed = False        # the footer follows a run that stopped: it then offers Finish

    def help_entries(self) -> list[tuple[str, str]]:
        """The keys that work on the screen as it is now: y and n only at the first boot question, enter as what it does
        in this state, r only when there is a snapshot to go back to."""
        out: list[tuple[str, str]] = []
        if self.mode == "first_boot":
            out += [("enter", "Back")] if self.helping else [("y", "Yes, all good"), ("n", "No, help me")]
        elif self.mode == "undo":
            out += [("enter", "Back")] if self.undo_error else []
        else:
            if self.mode == "run" and self._failed(self._state()):
                out.append(("enter", "Finish"))
            elif self.mode == "attached" and self.view and self._can_go(self.plan.interrupted):
                out.append(("enter", self._label))
            if self.mode == "attached" and self.query("ChoiceRow"):
                out += list(self.HELP_EXTRA)
            if self.mode in ("attached", "run") and self.view and not self.view.plan.refusals:
                out.append(("p", "See every change"))
        if self.mode != "undo" and self._has_snapshot():
            out.append(("r", "Undo driver update"))
        out.append(("esc", "Leave"))
        shown = {k for k, _d in out}
        return out + [(k, d) for k, d in COMMON_HELP if k not in shown]

    # ── layout ───────────────────────────────────────────────────────

    def crumb(self) -> str:
        return f"{self.mac or 'VM %d' % self.vmid} › Graphics card"

    def compose_body(self) -> ComposeResult:
        yield Vertical(id="gpu")

    def on_mount(self) -> None:
        self.set_interval(1.0, self.tick)
        self.run_worker(self._show(Static("Looking at the driver...", id="g-loading")), exclusive=True, group="drv-mode")
        self.load()

    async def _show(self, *widgets: Widget, mode: str = "loading") -> None:
        self.mode = mode
        try:
            body = self.query_one("#gpu", Vertical)
        except NoMatches:
            return
        await body.remove_children()
        await body.mount(*widgets)
        self._heal_focus()
        self.refresh_footer()

    def _heal_focus(self) -> None:
        """Keys go to the focused widget, and only a widget that is still on the screen passes them to the screen's
        bindings. Focus left on a widget that a mode change removed (the version row, after a TUI-launched update)
        leaves every key dead while the mouse still works: it is taken off, so the keys reach the screen again."""
        focused = self.focused
        if focused is not None and (not focused.is_attached or focused.screen is not self):
            self.set_focus(None)

    def busy(self) -> bool:
        return self.working

    # ── data ─────────────────────────────────────────────────────────

    def load(self, keep_picker: bool = False) -> None:
        """Read the Mac and the releases off the UI thread, then show the attached state (or the run in progress)."""
        self.working = True
        choice = self.choice if keep_picker else Choice()

        def work() -> None:
            try:
                view = load_view(self.vmid, choice, releases=not keep_picker or self.view is None)
            except Exception as exc:  # noqa: BLE001
                log.exception("driver view failed")
                self.app.call_from_thread(self._load_failed, str(exc))
                return
            self.app.call_from_thread(self._loaded, view, keep_picker)

        self.run_worker(work, thread=True, exit_on_error=False, group="drv-load")

    def _load_failed(self, message: str) -> None:
        self.working = False
        self.error = message
        self.run_worker(self._show(Static(Text("Could not read this Mac: " + message,
                                                style=self.app.get_css_variables()["error"]), id="g-loading")),
                        exclusive=True, group="drv-mode")

    def _loaded(self, view: DriverView, keep_picker: bool) -> None:
        self.working = False
        if keep_picker and self.view is not None and not view.releases:
            view.releases = self.view.releases
        self.view = view
        self.mac = view.plan.name or self.mac
        if self.mode in ("run", "first_boot", "undo"):
            return
        state = engine.read_update(self.vmid)
        if state.get("phase") == "done":
            self.run_worker(self._show_first_boot(), exclusive=True, group="drv-mode")
        elif state and engine.lock_holder(self.vmid):
            self.run_worker(self._show_run(), exclusive=True, group="drv-mode")
        else:
            self.run_worker(self._show_attached(), exclusive=True, group="drv-mode")

    # ── the attached state ───────────────────────────────────────────

    @property
    def plan(self) -> DriverPlan:
        assert self.view is not None
        return self.view.plan

    def _driver_rows(self) -> list[Widget]:
        """The installed version, one row per component (a long pair would wrap): the Driver row never wraps."""
        plan = self.plan
        pal = self.app.get_css_variables()
        if not plan.installed_known:
            return [Static(self._row("Driver", Text("installed version unknown", style=pal["muted"])), id="d-driver")]
        parts = [f"{n} {plan.installed[n]}" for n in engine.COMPONENTS[plan.kind] if plan.installed.get(n)]
        rows: list[Widget] = [Static(self._row("Driver" if i == 0 else "", Text(part, style="bold")), id="d-driver" if i == 0 else f"d-driver-{i + 1}")
                              for i, part in enumerate(parts)]
        if plan.from_record:      # the disk of a running Mac is not read: say whose word this is
            rows.append(Static(self._row("", Text(engine.FROM_RECORD, style=pal["muted"])), id="d-source"))
        return rows

    def _row(self, label: str, value: Text) -> Text:
        """One line of the Driver block: the label, then the value, cut with an ellipsis when it would not fit."""
        out = Text(" " + label.ljust(DRIVER_LABEL), style=self.app.get_css_variables()["muted"], no_wrap=True, overflow="ellipsis")
        value.truncate(max(12, self.size.width - 52), overflow="ellipsis")      # the form is the screen less the side panel
        out.append_text(value)
        return out

    def _status_text(self) -> Text:
        """What an update would do, in a short form of its own row: `v2.9 available`, `✓ up to date`."""
        pal = self.app.get_css_variables()
        plan = self.plan
        rows = plan.judged()["rows"] if plan.target is not None else []
        if plan.interrupted:       # a stopped update has one state: stopped. Whether a newer driver exists is not its news
            return Text("update stopped", style=pal["warning"], no_wrap=True)
        if plan.verdict == "current":
            return Text("✓ up to date", style=pal["success"], no_wrap=True)
        if len(rows) != 1:      # a pair: the plan (p) lists each one
            return Text("update available", style=pal["accent"], no_wrap=True)
        name, version = rows[0][0], rows[0][2]
        return Text((version if plan.installed_known else f"{name} {version}") + " available", style=pal["accent"], no_wrap=True)

    def _pending_text(self) -> str:
        """What the screen says about an update that stopped: where the card is, and the two ways on."""
        card = "hostpci0" in self.plan.cfg
        ways = "enter finishes it" + (", r undoes the driver update." if self._has_snapshot() else ".")
        return ("The driver update stopped before it finished." + ("" if card else " The card is off this Mac.") + " " + ways)

    def _has_snapshot(self) -> bool:
        """A driver snapshot of this Mac exists (or the run that is shown made one): r can undo the update."""
        return bool(self.view and self.view.plan.older_snapshots) or bool(self._state().get("snapshot"))

    async def _show_attached(self) -> None:
        pal = self.app.get_css_variables()
        plan = self.plan
        title = Text.assemble((f"{plan.name or 'VM %d' % self.vmid} graphics card", "bold"),
                              ("  " + (engine.CARD_NAME.get(plan.kind, "") + " card" if plan.kind else ""), pal["muted"]))
        pending = plan.interrupted
        rows: list[Widget] = []
        if plan.refusals:
            rows.append(Static(Text("\n".join("! " + r for r in plan.refusals), style=pal["warning"]), id="g-reason"))
        else:
            rows += self._driver_rows()
            rows.append(Static(self._row("Status", self._status_text()), id="d-status"))
        picker = None if pending else self._picker(plan)
        if picker is not None:
            rows.append(picker)
            second = self._graphics_picker(plan)
            if second is not None:
                rows.append(second)
        elif plan.kind == "amd" and not pending and not plan.refusals:
            rows.append(Static(Text(" " + "Version".ljust(DRIVER_LABEL) + "the versions this release pins",
                                    style=pal["muted"]), id="d-pinned"))
        if pending:
            rows.append(Static(Text("\n! " + self._pending_text(), style=pal["warning"]), id="d-unfinished"))
        can_go = self._can_go(pending)
        # a screen shows only what applies now: with nothing to do there is no "What happens" panel and no new snapshot name
        side = Vertical(Static(Text("What Finish does" if pending else "What happens", style=pal["muted"]), classes="side-title"),
                        Lines(id="d-facts"), id="g-side") if can_go else None
        form = Vertical(*rows, Static(self._safe_text(can_go), id="g-safe"), id="g-form")
        self._label = self._enter_label(pending)
        action = Horizontal(Static(Text(self._note_text(can_go, picker is not None, pending), style=pal["muted"]), id="g-note"),
                            *([Button(self._label, id="d-go", classes="primary")] if can_go else []), id="g-action")
        await self._show(Static(title, id="g-title"), Horizontal(*([form, side] if side else [form]), id="g-cols"), Static("", id="g-result"),
                         action, mode="attached")
        self._facts()
        self._focus_row()

    def _safe_text(self, can_go: bool = True) -> Text:
        """The snapshot line: before any update it says one is saved first (only when an update can start); after one,
        what r does."""
        pal = self.app.get_css_variables()
        snaps = self.plan.older_snapshots if self.view else []
        if snaps:
            return Text.assemble(("✓ ", pal["success"]), f"Driver snapshot of {engine.snapshot_date(snaps[-1])}. ",
                                 ("r", "bold " + pal["accent"]), " undoes the driver update.")
        if self.plan.interrupted:
            return Text.assemble(("! ", pal["warning"]), "This update has no driver snapshot, so it cannot be undone.")
        if not can_go:
            return Text("")
        return Text.assemble(("✓ ", pal["success"]), "A snapshot is saved first, so the update can be undone.")

    def _note_text(self, can_go: bool, has_picker: bool, pending: bool = False) -> str:
        if pending:
            return "Finish goes on from the step that stopped."
        if can_go:
            return "Nothing changes until you press enter."
        if self.plan.refusals:
            return ""
        return "Nothing to update." + (" ←→ picks another release to install it again." if has_picker else "")

    def _picker(self, plan: DriverPlan) -> ChoiceRow | None:
        found = self.view.releases if self.view else []
        if not found or plan.kind not in ("nvidia", "intel") or plan.refusals:
            return None
        picked = (self.choice.nullmoth if plan.kind == "nvidia" else self.choice.wex)
        return self._release_row("Version", found, picked, "d-version")      # Intel: the WhateverXe release

    def _graphics_picker(self, plan: DriverPlan) -> ChoiceRow | None:
        """Intel: the graphics package has its own release, picked on a second row."""
        found = self.view.graphics if self.view else []
        if not found or plan.kind != "intel" or plan.refusals:
            return None
        return self._release_row("Package", found, self.choice.graphics, "d-graphics")

    @staticmethod
    def _release_row(label: str, found: list[dict], picked: str, row_id: str) -> ChoiceRow:
        newest = next((r["tag"] for r in found if not r["prerelease"]), found[0]["tag"])
        options = [Option(r["tag"], r["tag"] + (" (latest)" if r["tag"] == newest else "")
                          + (" (pre-release)" if r["prerelease"] else "")) for r in found]
        picked = picked or newest
        return ChoiceRow(label, options, picked if picked in [o.value for o in options] else newest,
                         label_width=DRIVER_LABEL, id=row_id, hint_lines=0)

    def _focus_row(self) -> None:
        rows = list(self.query(ChoiceRow))
        if rows:
            rows[0].focus()

    def action_row(self, delta: int) -> None:
        """Up and down move between the release rows (Intel has two)."""
        rows = [r for r in self.query(ChoiceRow) if r.display]
        if self.mode != "attached" or len(rows) < 2:
            return
        idx = rows.index(self.focused) if self.focused in rows else 0
        rows[max(0, min(len(rows) - 1, idx + delta))].focus()

    def _facts(self) -> None:
        pal = self.app.get_css_variables()
        plan = self.plan
        if plan.refusals or not self.query("#d-facts"):
            return
        if plan.interrupted:         # what Finish does: no new snapshot, no "saved first"
            facts = engine.finish_facts(plan.kind, self._state().get("snapshot", ""))
            rows = [Line(Text.assemble(("✓ ", pal["success"]), fact), wrap=True, hang=2) for fact in facts]
        else:
            facts = engine.before_facts(plan.kind, plan.snapshot, plan.snapshot_ok)
            rows = [Line(Text.assemble(("✓ " if plan.snapshot_ok else "! ", pal["success"] if plan.snapshot_ok else pal["warning"]), fact),
                         wrap=True, hang=2) for fact in facts]
        self.query_one("#d-facts", Lines).set_lines(rows)

    def on_choice_row_changed(self, event: ChoiceRow.Changed) -> None:
        self.touched = True
        c = self.choice
        if self.plan.kind == "nvidia":
            self.choice = Choice(nullmoth=event.value, macos=c.macos)
        elif event.row.id == "d-graphics":
            self.choice = Choice(wex=c.wex, graphics=event.value, macos=c.macos)
        else:
            self.choice = Choice(wex=event.value, graphics=c.graphics, macos=c.macos)
        self.load(keep_picker=True)

    # ── enter ────────────────────────────────────────────────────────

    def _can_go(self, pending: bool) -> bool:
        plan = self.plan if self.view else None
        if plan is None or plan.refusals:
            return False
        return pending or not plan.nothing_to_do or self.touched and bool(self.query("ChoiceRow"))

    def _enter_label(self, pending: bool) -> str:
        if pending:
            return "Finish"
        return "Reinstall" if self.plan.verdict == "current" else "Update driver"

    def footer_items(self) -> list[tuple]:
        undo = [("r", "Undo driver update", "screen.undo_driver")] if self._has_snapshot() else []
        if self.mode == "undo":
            return [("enter", "Back", "screen.primary"), ("esc", "Back", "screen.back")] if self.undo_error else [("?", "Help", "app.help")]
        if self.mode == "first_boot":
            if self.helping:
                return [("enter", "Back", "screen.primary"), ("esc", "Back", "screen.back")]
            return [("y", "Yes, all good", "screen.yes"), ("n", "No, help me", "screen.no"), *undo,
                    ("esc", "Your Macs", "screen.back")]
        items: list[tuple] = []
        if self.mode == "run" and self._failed(self._state()):
            items.append(("enter", "Finish", "screen.primary"))
        if self.mode == "attached" and self.view and self._can_go(self.plan.interrupted):
            items.append(("enter", self._label, "screen.primary"))
        if self.mode == "attached" and self.query("ChoiceRow"):
            items.append(("←→", "Version", None))
            if len(self.query("ChoiceRow")) > 1:
                items.append(("↑↓", "Row", None))
        if self.mode in ("attached", "run") and self.view and not self.view.plan.refusals:
            items.append(("p", "See every change", "screen.changes"))
        if self.mode in ("attached", "run"):
            items += undo
        items += [("esc", "Back", "screen.back"), ("?", "Help", "app.help")]
        return items

    def action_primary(self) -> None:
        if self.working:
            return
        if self.mode == "first_boot":
            if self.helping:
                self.run_worker(self._show_first_boot(), exclusive=True, group="drv-mode")
            return
        if self.mode == "undo":
            if self.undo_error:
                self._leave_undo()
            return
        if self.mode == "run":
            if self._failed(self._state()):       # the box says "enter finishes it": it does, whatever made it fail
                self._finish_run()
            return
        if self.mode != "attached" or self.view is None:
            return
        pending = self.plan.interrupted
        if not self._can_go(pending):
            return
        if pending:
            self._finish_run()
            return
        self.app.push_screen(BeforeDialog(build_before(self.plan)), self._approved)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "d-go":
            self.action_primary()
        else:
            self.on_button_pressed_boot(event.button.id or "")

    def _approved(self, result: dict | None) -> None:
        if result is None:
            return
        intel = self.plan.kind == "intel"
        self._spawn(Choice(self.choice.nullmoth if self.plan.kind == "nvidia" else "",
                           self.choice.wex if intel else "", self.choice.graphics if intel else "", self.choice.macos),
                    accept_risk=bool(result.get("ack")), no_snapshot=bool(result.get("typed")), snapshot=self.plan.snapshot)

    def _spawn(self, choice: Choice, finish: bool = False, accept_risk: bool = False, no_snapshot: bool = False,
               snapshot: str = "") -> None:
        """Start the run. *snapshot* is the name the plan and the confirmation showed: the run uses exactly that one."""
        self.error = ""
        try:
            driver_spawn.spawn_update(self.vmid, choice, finish, accept_risk, no_snapshot, snapshot)
        except (OSError, ValueError) as exc:
            self.app.notify(f"Could not start the update: {exc}", severity="error")
            return
        self.spawned_at = time.time()
        self.run_worker(self._show_run(), exclusive=True, group="drv-mode")

    def _finish_run(self) -> None:
        """Continue the run that stopped, from the step it stopped at, with the releases it started with. The NullMoth
        notice was approved when that run started, so Finish does not ask for it again."""
        state = engine.read_update(self.vmid)
        tags = state.get("choice", {})
        self._spawn(Choice(tags.get("NullMoth", ""), tags.get("WhateverXe", ""), tags.get("WhateverXe Graphics", "")),
                    finish=True, accept_risk=state.get("kind") == "nvidia", no_snapshot=bool(state.get("no_snapshot")))

    # ── p: see every change ──────────────────────────────────────────

    def action_changes(self) -> None:
        if self.view is None or self.view.plan.refusals:
            return
        plan = self.plan
        state = self._state()
        if self.mode == "run" and self._failed(state) and not plan.interrupted:       # the plan was made before the run stopped
            plan = dataclasses.replace(plan, interrupted=True, resume=state)
        plain = "\n".join(plan.lines())
        self.app.push_screen(ChangesDialog(plain, plain + "\n\nCommands:\n" + "\n".join("  " + c for c in commands(plan))))

    # ── the run ──────────────────────────────────────────────────────

    def _state(self) -> dict:
        return engine.read_update(self.vmid)

    def _shown(self, state: dict) -> dict:
        """The state the run screen draws. A Finish that was just started has not written its own state yet: the file is
        the stopped run's, whose clocks and failed row are not this run's, so it is drawn as a fresh list (what the earlier
        run finished stays done, with no time; the rest waits)."""
        if state and self._starting():
            state = copy.deepcopy(state)
            engine.reset_for_resume(state)
        return state

    def timeline(self, state: dict) -> Text:
        pal = self.app.get_css_variables()
        out = Text()
        now = time.time()
        for idx, row in enumerate(state.get("steps", [])):
            out.append("\n" if idx else "")
            title, tail = row.get("title", ""), ""
            if row["state"] == "done":
                out.append("✓ ", style=pal["success"])
                title, tone = row.get("done", title), pal["muted"]
                tail = "" if row.get("carried") else clock(row.get("ended", now) - row.get("started", now))
            elif row["state"] == "active":
                out.append("◐ ", style=pal["accent"])
                tone, tail = "bold", clock(now - row.get("started", now))
            elif row["state"] == "fail":
                out.append("✗ ", style=pal["error"])
                tone = pal["error"]
            else:
                out.append("· ", style=pal["muted"])
                tone = pal["muted"]
            out.append(title, style=tone)
            out.append(" " * max(1, TIMELINE_WIDTH - 2 - len(title) - len(tail)) + tail, style=pal["muted"])
        return out

    def _starting(self) -> bool:
        """A run was started a moment ago and has not written anything yet: the state file is what the last run left, and
        nobody holds the lock. The file's own time says whose it is."""
        if not self.spawned_at or time.time() - self.spawned_at >= START_GRACE or engine.lock_holder(self.vmid):
            return False
        try:
            return engine.update_path(self.vmid).stat().st_mtime < self.spawned_at
        except OSError:
            return True

    def _older_than_spawn(self) -> bool:
        try:
            return engine.update_path(self.vmid).stat().st_mtime < self.spawned_at
        except OSError:
            return True

    def _failed(self, state: dict) -> bool:
        return bool(self.error) or (state.get("phase") == "failed" and not self._starting())

    def _run_box(self, state: dict) -> tuple[str, list[str]]:
        if self._failed(state):
            ways = "enter finishes it." + (" r undoes the driver update." if self._has_snapshot() else "")
            return "The update did not finish", [self.error or state.get("error", ""), "", ways]
        if self._starting():
            return "Updating the driver", ["Starting..."]
        recent = [e[9:] for e in state.get("events", [])][-6:]
        return "Updating the driver", recent or ["Starting..."]

    def _run_note(self, state: dict) -> str:
        return "The update stopped. Nothing is running." if self._failed(state) else "Safe to leave: the update keeps running."

    def _run_head(self, state: dict) -> str:
        kind = state.get("kind") or (self.view.plan.kind if self.view else "")
        return " ".join(p for p in ("Updating the", engine.CARD_NAME.get(kind, ""), "driver of", self.mac or "the Mac") if p)

    async def _show_run(self) -> None:
        state = self._state()
        self._was_failed = self._failed(state)
        pal = self.app.get_css_variables()
        title, lines = self._run_box(state)
        box = Vertical(Static(Text(title, style="bold"), id="g-box-title"),
                       ReadScroll(Static(Text("\n".join(lines)), id="g-box-text"), id="g-box-scroll"),
                       id="g-box", classes="fail" if self._failed(state) else "")
        await self._show(Static(Text(self._run_head(state), style="bold"), id="g-run-title"),
                         Horizontal(Static(self.timeline(self._shown(state)), id="g-timeline"), box, id="g-run-cols"),
                         Static(Text(self._run_note(state), style=pal["muted"]), id="g-run-note"),
                         mode="run")

    def tick(self) -> None:
        """Once a second: the run view follows the state file; a finished run asks the first boot question."""
        self._heal_focus()
        if self.mode == "undo":
            self.paint_undo()
            return
        if self.mode != "run":
            return
        state = self._state()
        alive = bool(engine.lock_holder(self.vmid))
        if state.get("phase") == "done":
            self.run_worker(self._show_first_boot(), exclusive=True, group="drv-mode")
            return
        stale = bool(self.spawned_at) and time.time() - self.spawned_at > START_GRACE
        if alive:
            self.error = ""
        elif not state and stale:
            self.error = "The update did not start. See " + driver_spawn.log_path(self.vmid)
        elif state and stale and self._older_than_spawn():
            self.error = "The update did not start. See " + driver_spawn.log_path(self.vmid)
        elif state.get("phase") == "running" and not self._starting():
            self.error = "The update stopped before it finished."
        self.paint_run(state)

    def paint_run(self, state: dict) -> None:
        try:
            self.query_one("#g-timeline", Static).update(self.timeline(self._shown(state)))
            title, lines = self._run_box(state)
            self.query_one("#g-box-title", Static).update(Text(title, style="bold"))
            self.query_one("#g-box-text", Static).update(Text("\n".join(lines)))
            self.query_one("#g-box").set_class(self._failed(state), "fail")
            self.query_one("#g-run-note", Static).update(Text(self._run_note(state), style=self.app.get_css_variables()["muted"]))
        except NoMatches:
            return
        failed = self._failed(state)
        if failed != self._was_failed:
            self._was_failed = failed
            self.refresh_footer()

    # ── first boot ───────────────────────────────────────────────────

    async def _show_first_boot(self, helping: bool = False) -> None:
        state = self._state()
        pal = self.app.get_css_variables()
        self.helping = helping
        body = reflow_help(NO_SIGNAL_HELP) if helping else "The Mac is back on. " + QUESTION
        buttons = ([Button("Back", id="d-back", classes="primary")] if helping else
                   [Button("No, help me", id="d-no", classes="secondary"), Button("Yes, all good", id="d-yes", classes="primary")])
        box = Vertical(Static(Text("No signal? Try these" if helping else "Your Mac is ready", style="bold"), id="g-box-title"),
                       Static(Text(body), id="g-box-text"), Horizontal(*buttons, id="g-box-buttons"), id="g-box")
        await self._show(Static(Text(f"{self.mac or 'The Mac'} is starting with the new driver", style="bold"), id="g-run-title"),
                         Horizontal(Static(self.timeline(state), id="g-timeline"), box, id="g-run-cols"),
                         Static(Text("Not happy with it? r undoes the driver update." if self._has_snapshot() else "", style=pal["muted"]),
                                id="g-run-note"), mode="first_boot")

    def on_button_pressed_boot(self, bid: str) -> None:
        if bid == "d-yes":
            self.action_yes()
        elif bid == "d-no":
            self.action_no()
        elif bid == "d-back":
            self.action_primary()

    def action_yes(self) -> None:
        if self.mode != "first_boot" or self.helping:
            return
        snapshot = self._state().get("snapshot", "")
        engine.clear_update(self.vmid)
        if not snapshot:
            self.app.notify("The driver is updated.")
            super().action_back()
            return
        self.app.push_screen(ChoiceDialog("Remove the driver snapshot?", [
            f"{snapshot} can undo the driver update. It uses disk space.", "Keep it until you are sure everything works."],
            [Pick("keep", "Keep", "primary"), Pick("remove", "Remove it")], focus="keep"),
            lambda answer: self._snapshot_answer(answer, snapshot))

    def _snapshot_answer(self, answer: str | None, snapshot: str = "") -> None:
        if answer == "remove":
            try:
                name = remove_safety_snapshot(self.vmid, RunEnv(get_proxmox_adapter()), snapshot)
                self.app.notify(f"Removed the driver snapshot {name}.")
            except DriverError as exc:
                self.app.notify(str(exc), severity="error")
        else:
            self.app.notify("The driver is updated. The driver snapshot is kept.")
        super().action_back()

    def action_no(self) -> None:
        if self.mode == "first_boot" and not self.helping:
            self.run_worker(self._show_first_boot(True), exclusive=True, group="drv-mode")

    # ── r: undo the driver update, u: put the card setup back ────────

    def action_undo_driver(self) -> None:
        """Ask which snapshot (and whether the card setup still matches it) off the UI thread, then confirm."""
        if self.working:
            return
        if not self._has_snapshot():
            self.app.notify(engine.REFUSE_NOTHING_TO_UNDO, severity="warning")
            return
        self.working = True

        def work() -> None:
            try:
                name = undo_target(self.vmid, RunEnv(get_proxmox_adapter()))
            except DriverError as exc:
                self.app.call_from_thread(self._undo_refused, str(exc))
                return
            self.app.call_from_thread(self._confirm_undo, name)

        self.run_worker(work, thread=True, exit_on_error=False, group="drv-undo")

    def _undo_refused(self, text: str) -> None:
        self.working = False
        self.app.notify(text, severity="error")

    def _confirm_undo(self, name: str) -> None:
        self.working = False

        def answer(result: str | None) -> None:
            if result == "yes":
                self._put_back()

        # Cancel keeps the focus (enter cancels): the undo rolls the disk back, so a stray enter must not confirm it
        self.app.push_screen(ConfirmDialog("Undo the driver update?", [
            f"The Mac stops, its disks go back to the snapshot of {engine.snapshot_date(name)} and it starts again.",
            UNDO_LOSS_LINE], confirm="Undo driver update", danger=True, enter="Cancel", confirm_key="tab, then enter"),
            answer)

    def _put_back(self) -> None:
        """The undo runs in a worker; its screen (the steps, ✓ and ◐ like the update) is up from the first second."""
        self.working = True
        self.undo_error = ""
        self.undo_state = {"steps": [{"id": sid, "title": t, "done": d, "state": "pending"}
                                     for (sid, t), (_i, d) in zip(engine.undo_titles(), engine.undo_titles(done=True))]}

        def step(sid: str, state: str) -> None:
            self.app.call_from_thread(self._undo_step, sid, state)

        def work() -> None:
            try:
                name = put_back(self.vmid, RunEnv(get_proxmox_adapter(), on_step=step))
                self.app.call_from_thread(self._put_back_done, f"Undid the driver update: the Mac is back to {name}.")
            except Exception as exc:  # noqa: BLE001 - whatever ends the undo is shown on its error screen, never a spinner
                self.app.call_from_thread(self._undo_failed, str(exc))

        async def start() -> None:
            await self._show_undo()          # the steps are on screen before the first one starts
            self.run_worker(work, thread=True, exit_on_error=False, group="drv-undo")

        self.run_worker(start(), exclusive=True, group="drv-mode")

    def _undo_step(self, sid: str, state: str) -> None:
        for row in self.undo_state.get("steps", []):
            if row["id"] == sid:
                row["state"] = state
                if state == "active":
                    row["started"] = time.time()
                else:
                    row["ended"] = time.time()
        self.paint_undo()

    async def _show_undo(self) -> None:
        pal = self.app.get_css_variables()
        failed = bool(self.undo_error)
        title = "The undo did not finish" if failed else "Going back to the driver snapshot"
        lines = [self.undo_error, "", "enter goes back."] if failed else [
            "The Mac stops, its disks go back to the snapshot and it starts again.", "This takes a few minutes. Keep this screen open."]
        box = Vertical(Static(Text(title, style="bold"), id="g-box-title"),
                       ReadScroll(Static(Text("\n".join(lines)), id="g-box-text"), id="g-box-scroll"),
                       id="g-box", classes="fail" if failed else "")
        await self._show(Static(Text(f"Undoing the driver update of {self.mac or 'the Mac'}", style="bold"), id="g-run-title"),
                         Horizontal(Static(self.timeline(self.undo_state), id="g-timeline"), box, id="g-run-cols"),
                         Static(Text("The Mac stays as it is until you go back." if failed else "", style=pal["muted"]), id="g-run-note"),
                         mode="undo")

    def paint_undo(self) -> None:
        try:
            self.query_one("#g-timeline", Static).update(self.timeline(self.undo_state))
        except NoMatches:
            pass

    def _undo_failed(self, text: str) -> None:
        self.working = False
        self.undo_error = text
        self.run_worker(self._show_undo(), exclusive=True, group="drv-mode")

    def _leave_undo(self) -> None:
        self.undo_error = ""
        self.mode = "loading"
        self.load()

    def _put_back_done(self, text: str) -> None:
        self.working = False
        self.app.notify(text)
        self._leave_undo()

    def action_back(self) -> None:
        if self.mode == "first_boot" and self.helping:
            self.action_primary()
            return
        if self.mode == "undo" and self.undo_error:
            self._leave_undo()
            return
        if self.working:
            self.app.notify("Wait for this step to finish.")
            return
        super().action_back()

