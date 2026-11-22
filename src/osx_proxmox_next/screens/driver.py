"""Graphics card of a Mac that already has one: the driver it runs, whether a newer one exists, and the update.

One approval, then the update runs by itself in a detached process (`osx-next-cli driver-update`) and this screen
reads its steps from the update state file, so a reopened terminal shows the same run. u puts the Mac back."""

from __future__ import annotations

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
from ..driver_run import put_back, remove_safety_snapshot
from ..driver_targets import Choice
from ..gpu_flow import Before
from ..nullmoth import NOTICE_BODY, NOTICE_TITLE
from ..services import get_proxmox_adapter
from ..widgets.lines import Line, Lines
from ..widgets.rows import ChoiceRow, Option
from .base import Choice as Pick, ChoiceDialog, ConfirmDialog, OsxScreen
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


@dataclass
class DriverView:
    """What the attached state shows: the plan for the latest release and the releases the picker offers."""
    plan: DriverPlan
    releases: list[dict] = field(default_factory=list)   # newest first, at most MAX_RELEASES


def load_view(vmid: int, choice: Choice | None = None, releases: bool = True) -> DriverView:
    """Worker work: the plan (Proxmox, GitHub) and the picker's releases. A release list that cannot be read is empty."""
    plan = prepare(vmid, choice, reinstall=True)
    found: list[dict] = []
    if releases and plan.kind in ("nvidia", "intel"):
        try:
            found = (nullmoth_finish.versions() if plan.kind == "nvidia"
                     else igpu_engine.list_releases("whateverxe"))[:MAX_RELEASES]
        except Exception:  # noqa: BLE001 - the picker is a courtesy: the plan for the latest release stands
            log.debug("release list failed", exc_info=True)
    return DriverView(plan, found)


def build_before(plan: DriverPlan) -> Before:
    """The warnings, the facts and the NullMoth notice of one update, as "Before you continue" shows them."""
    out = Before(go_label="Reinstall" if plan.verdict == "current" else "Update driver")
    facts = [n for n in engine.before_facts(plan.kind, plan.snapshot, True)
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
        Binding("u", "undo", "Put it back"),
        Binding("y", "yes", "Yes, all good"),
        Binding("n", "no", "No, help me"),
        Binding("escape", "back", "Leave"),
    ]
    HELP_TITLE = "Graphics card"
    HELP_EXTRA = [("←  →  h  l", "Pick another release")]

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

    # ── layout ───────────────────────────────────────────────────────

    def crumb(self) -> str:
        return f"{self.mac or 'VM %d' % self.vmid} › Graphics card BETA"

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
        self.refresh_footer()

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
        if self.mode in ("run", "first_boot"):
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

    def _driver_text(self) -> Text:
        pal = self.app.get_css_variables()
        plan = self.plan
        out = Text()
        if plan.installed_known:
            out.append(engine.driver_line(plan.kind, plan.installed), style="bold")
        else:
            out.append("installed version unknown", style=pal["muted"])
        verdict = plan.verdict
        if verdict == "update" and plan.installed_known:
            out.append("   " + "  ".join(f"{n} {v} available" for n, h, v, s in plan.judged()["rows"] if s == "update"),
                       style=pal["accent"])
        elif verdict == "current":
            out.append("   ✓ up to date", style=pal["success"])
        elif plan.target is not None:
            out.append("   " + engine.driver_line(plan.kind, plan.target.versions()) + " available", style=pal["accent"])
        return out

    async def _show_attached(self) -> None:
        pal = self.app.get_css_variables()
        plan = self.plan
        title = Text.assemble((f"{plan.name or 'VM %d' % self.vmid} graphics card", "bold"),
                              ("  " + (engine.CARD_NAME.get(plan.kind, "") + " card" if plan.kind else ""), pal["muted"]))
        rows: list[Widget] = []
        if plan.refusals:
            rows.append(Static(Text("\n".join("! " + r for r in plan.refusals), style=pal["warning"]), id="g-reason"))
        else:
            rows.append(Static(Text.assemble((" " + "Driver".ljust(DRIVER_LABEL), pal["muted"]), self._driver_text()),
                               id="d-driver"))
        picker = self._picker(plan)
        if picker is not None:
            rows.append(picker)
        elif plan.kind == "amd":
            rows.append(Static(Text(" " + "Version".ljust(DRIVER_LABEL) + "the versions this release pins",
                                    style=pal["muted"]), id="d-pinned"))
        pending = engine.unfinished(self.vmid)
        if pending:
            rows.append(Static(Text("! An earlier update was cut off. enter finishes it, u puts the Mac back.",
                                    style=pal["warning"]), id="d-unfinished"))
        safe = Text.assemble(("✓ ", pal["success"]), "A snapshot is saved first. ", ("u", "bold " + pal["accent"]),
                             " puts the Mac back.")
        side = Vertical(Static(Text("What happens", style=pal["muted"]), classes="side-title"), Lines(id="d-facts"),
                        id="g-side")
        form = Vertical(*rows, Static(safe, id="g-safe"), id="g-form")
        self._label = self._enter_label(bool(pending))
        action = Horizontal(Static(Text("Nothing changes until you press enter.", style=pal["muted"]), id="g-note"),
                            Button(self._label, id="d-go", classes="primary", disabled=not self._can_go(bool(pending))),
                            id="g-action")
        await self._show(Static(title, id="g-title"), Horizontal(form, side, id="g-cols"), Static("", id="g-result"),
                         action, mode="attached")
        self._facts()
        self._focus_row()

    def _picker(self, plan: DriverPlan) -> ChoiceRow | None:
        found = self.view.releases if self.view else []
        if not found or plan.kind not in ("nvidia", "intel") or plan.refusals:
            return None
        newest = next((r["tag"] for r in found if not r["prerelease"]), found[0]["tag"])
        options = [Option(r["tag"], r["tag"] + (" (latest)" if r["tag"] == newest else "")
                          + (" (pre-release)" if r["prerelease"] else "")) for r in found]
        picked = (self.choice.nullmoth if plan.kind == "nvidia" else self.choice.wex) or newest
        return ChoiceRow("Version", options, picked if picked in [o.value for o in options] else newest,
                         label_width=DRIVER_LABEL, id="d-version", hint_lines=0)

    def _focus_row(self) -> None:
        for row in self.query(ChoiceRow):
            row.focus()

    def _facts(self) -> None:
        pal = self.app.get_css_variables()
        plan = self.plan
        if plan.refusals:
            return
        rows = [Line(Text.assemble(("✓ " if plan.snapshot_ok else "! ", pal["success"] if plan.snapshot_ok else pal["warning"]),
                                   engine.before_facts(plan.kind, plan.snapshot, plan.snapshot_ok)[idx]), wrap=True, hang=2)
                for idx in range(len(engine.before_facts(plan.kind, plan.snapshot, plan.snapshot_ok)))]
        self.query_one("#d-facts", Lines).set_lines(rows)

    def on_choice_row_changed(self, event: ChoiceRow.Changed) -> None:
        self.touched = True
        if self.plan.kind == "nvidia":
            self.choice = Choice(nullmoth=event.value, macos=self.choice.macos)
        else:
            self.choice = Choice(wex=event.value, macos=self.choice.macos)
        self.load(keep_picker=True)

    # ── enter ────────────────────────────────────────────────────────

    def _can_go(self, pending: bool) -> bool:
        plan = self.plan if self.view else None
        if plan is None or plan.refusals:
            return bool(pending and plan is not None and not plan.refusals)
        return pending or not plan.nothing_to_do or self.touched and bool(self.query("ChoiceRow"))

    def _enter_label(self, pending: bool) -> str:
        if pending:
            return "Finish"
        return "Reinstall" if self.plan.verdict == "current" else "Update driver"

    def footer_items(self) -> list[tuple]:
        if self.mode == "first_boot":
            if self.helping:
                return [("enter", "Back", "screen.primary"), ("esc", "Back", "screen.back")]
            return [("y", "Yes, all good", "screen.yes"), ("n", "No, help me", "screen.no"), ("u", "Put it back", "screen.undo"),
                    ("esc", "Your Macs", "screen.back")]
        items: list[tuple] = []
        if self.mode == "attached" and self.view and self._can_go(bool(engine.unfinished(self.vmid))):
            items.append(("enter", self._label, "screen.primary"))
        if self.mode == "attached" and self.query("ChoiceRow"):
            items.append(("←→", "Version", None))
        if self.mode in ("attached", "run") and self.view and not self.view.plan.refusals:
            items.append(("p", "See every change", "screen.changes"))
        if self.mode in ("attached", "run", "first_boot"):
            items.append(("u", "Put it back", "screen.undo"))
        items += [("esc", "Back", "screen.back"), ("?", "Help", "app.help")]
        return items

    def action_primary(self) -> None:
        if self.working:
            return
        if self.mode == "first_boot":
            if self.helping:
                self.run_worker(self._show_first_boot(), exclusive=True, group="drv-mode")
            return
        if self.mode == "run":
            if self.error:
                self._finish_run()
            return
        if self.mode != "attached" or self.view is None:
            return
        pending = bool(engine.unfinished(self.vmid))
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
        self._spawn(Choice(self.choice.nullmoth if self.plan.kind == "nvidia" else "",
                           self.choice.wex if self.plan.kind == "intel" else "", "", self.choice.macos),
                    accept_risk=bool(result.get("ack")), no_snapshot=bool(result.get("typed")))

    def _spawn(self, choice: Choice, finish: bool = False, accept_risk: bool = False, no_snapshot: bool = False) -> None:
        self.error = ""
        try:
            driver_spawn.spawn_update(self.vmid, choice, finish, accept_risk, no_snapshot)
        except (OSError, ValueError) as exc:
            self.app.notify(f"Could not start the update: {exc}", severity="error")
            return
        self.spawned_at = time.time()
        self.run_worker(self._show_run(), exclusive=True, group="drv-mode")

    def _finish_run(self) -> None:
        state = engine.read_update(self.vmid)
        tags = state.get("choice", {})
        self._spawn(Choice(tags.get("NullMoth", ""), tags.get("WhateverXe", ""), tags.get("WhateverXe Graphics", "")),
                    finish=True, accept_risk=state.get("kind") == "nvidia", no_snapshot=not state.get("snapshot"))

    # ── p: see every change ──────────────────────────────────────────

    def action_changes(self) -> None:
        if self.view is None or self.view.plan.refusals:
            return
        plan = self.plan
        plain = "\n".join(plan.lines())
        self.app.push_screen(ChangesDialog(plain, plain + "\n\nCommands:\n" + "\n".join("  " + c for c in commands(plan))))

    # ── the run ──────────────────────────────────────────────────────

    def _state(self) -> dict:
        return engine.read_update(self.vmid)

    def timeline(self, state: dict) -> Text:
        pal = self.app.get_css_variables()
        out = Text()
        now = time.time()
        for idx, row in enumerate(state.get("steps", [])):
            out.append("\n" if idx else "")
            title, tail = row.get("title", ""), ""
            if row["state"] == "done":
                out.append("✓ ", style=pal["success"])
                title, tail, tone = row.get("done", title), clock(row.get("ended", now) - row.get("started", now)), pal["muted"]
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

    def _run_box(self, state: dict) -> tuple[str, list[str]]:
        phase = state.get("phase", "")
        if self.error or phase == "failed":
            return "The update did not finish", [self.error or state.get("error", ""), "",
                                                   "enter finishes it. u puts the Mac back to the snapshot."]
        recent = [e[9:] for e in state.get("events", [])][-6:]
        return "Updating the driver", recent or ["Starting..."]

    async def _show_run(self) -> None:
        state = self._state()
        pal = self.app.get_css_variables()
        title, lines = self._run_box(state)
        head = f"Updating the {engine.CARD_NAME.get(state.get('kind', ''), '')} driver of {self.mac or 'the Mac'}"
        box = Vertical(Static(Text(title, style="bold"), id="g-box-title"),
                       ReadScroll(Static(Text("\n".join(lines)), id="g-box-text"), id="g-box-scroll"),
                       id="g-box", classes="fail" if self.error or state.get("phase") == "failed" else "")
        await self._show(Static(Text(head, style="bold"), id="g-run-title"),
                         Horizontal(Static(self.timeline(state), id="g-timeline"), box, id="g-run-cols"),
                         Static(Text("Safe to leave: the update keeps running. u puts the Mac back.",
                                     style=pal["muted"]), id="g-run-note"), mode="run")

    def tick(self) -> None:
        """Once a second: the run view follows the state file; a finished run asks the first boot question."""
        if self.mode != "run":
            return
        state = self._state()
        alive = bool(engine.lock_holder(self.vmid))
        if state.get("phase") == "done":
            self.run_worker(self._show_first_boot(), exclusive=True, group="drv-mode")
            return
        if not state and not alive and self.spawned_at and time.time() - self.spawned_at > START_GRACE:
            self.error = "The update did not start. See " + driver_spawn.log_path(self.vmid)
        elif state and not alive and state.get("phase") == "running":
            self.error = "The update stopped before it finished."
        self.paint_run(state)

    def paint_run(self, state: dict) -> None:
        try:
            self.query_one("#g-timeline", Static).update(self.timeline(state))
            title, lines = self._run_box(state)
            self.query_one("#g-box-title", Static).update(Text(title, style="bold"))
            self.query_one("#g-box-text", Static).update(Text("\n".join(lines)))
        except NoMatches:
            pass

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
                         Static(Text("Not happy with it? u puts the Mac back to the snapshot.", style=pal["muted"]),
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
        self.app.push_screen(ChoiceDialog("Remove the safety snapshot?", [
            f"{snapshot} can put the Mac back. It uses disk space.", "Keep it until you are sure everything works."],
            [Pick("keep", "Keep", "primary"), Pick("remove", "Remove it")], focus="keep"), self._snapshot_answer)

    def _snapshot_answer(self, answer: str | None) -> None:
        if answer == "remove":
            try:
                name = remove_safety_snapshot(self.vmid, RunEnv(get_proxmox_adapter()))
                self.app.notify(f"Removed the safety snapshot {name}.")
            except DriverError as exc:
                self.app.notify(str(exc), severity="error")
        else:
            self.app.notify("The driver is updated. The safety snapshot is kept.")
        super().action_back()

    def action_no(self) -> None:
        if self.mode == "first_boot" and not self.helping:
            self.run_worker(self._show_first_boot(True), exclusive=True, group="drv-mode")

    # ── u: put it back ───────────────────────────────────────────────

    def action_undo(self) -> None:
        if self.working:
            return
        snaps = engine.tool_snapshots("\n".join(self.view.plan.older_snapshots)) if self.view else []
        state = self._state()
        name = state.get("snapshot") or (snaps[-1] if snaps else "")
        if not name:
            self.app.notify(engine.REFUSE_NOTHING_TO_UNDO, severity="warning")
            return

        def answer(result: str | None) -> None:
            if result == "yes":
                self._put_back()

        self.app.push_screen(ConfirmDialog("Put the Mac back?", [
            f"The Mac stops, goes back to {name} and starts again.", "Everything done since is lost."],
            confirm="Put it back", danger=True), answer)

    def _put_back(self) -> None:
        self.working = True

        def work() -> None:
            try:
                name = put_back(self.vmid, RunEnv(get_proxmox_adapter()))
                self.app.call_from_thread(self._put_back_done, f"Put the Mac back to {name}.", False)
            except DriverError as exc:
                self.app.call_from_thread(self._put_back_done, str(exc), True)

        self.run_worker(work, thread=True, exit_on_error=False, group="drv-undo")

    def _put_back_done(self, text: str, failed: bool) -> None:
        self.working = False
        self.app.notify(text, severity="error" if failed else "information")
        self.mode = "loading"
        self.load()

    def action_back(self) -> None:
        if self.mode == "first_boot" and self.helping:
            self.action_primary()
            return
        if self.working:
            self.app.notify("Wait for this step to finish.")
            return
        super().action_back()

