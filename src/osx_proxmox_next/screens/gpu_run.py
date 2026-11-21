"""Moment 2 of the graphics card flow: the run view (timeline and box), the host setup and the one restart."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from .. import host_setup
from ..gpu_flow import needs_typed, plan_for_approval, plan_lines, short_card_name
from ..gpu_setup import CONFIRM_PHRASE
from ..services.unattended_service import read_driver_state
from .base import ConfirmDialog

TIMELINE_WIDTH = 38
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def clock(secs: float | None) -> str:
    return "" if secs is None else f"{int(secs) // 60}:{int(secs) % 60:02d}"


@dataclass
class Guidance:
    """A box in the run view: a title, its lines, one primary action and optionally a second button on its left."""
    title: str
    lines: list[str]
    label: str
    action: Callable[[], None]
    secondary: tuple[str, Callable[[], None]] | None = None
    tone: str = "fail"


class RunMixin:
    """The run view of GpuScreen: the steps that apply, a box for what needs the user, the host and restart steps."""

    # ── the timeline ─────────────────────────────────────────────────

    @property
    def durations(self) -> dict[str, float]:
        """How long each finished step took. They live in the saved state, so a flow that was reopened after the
        restart still shows the steps before it."""
        return self.s.state.setdefault("times", {})

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

    def _is_done(self, pid: str) -> bool:
        s = self.s
        done = set(s.state["done"]) | set(s.state["skipped"])
        return ((pid in ("backup", "host") and 4 in done) or (pid == "restart" and 5 in done)
                or (pid == "check" and 6 in done) or (pid == "attach" and 7 in done)
                or (pid == "start" and (8 in done or s.state["step"] >= 8)))

    def phase_status(self, pid: str) -> str:
        if self._is_done(pid):
            return "ok"
        first_open = next((p[0] for p in self.phases() if not self._is_done(p[0])), "")
        return "active" if pid == first_open else "pending"

    def timeline(self, first_boot: bool = False) -> Text:
        pal = self.app.get_css_variables()
        out = Text()
        phases = self.phases()
        for idx, (pid, active, past) in enumerate(phases):
            status = self.phase_status(pid)
            out.append("\n" if idx else "")
            if status == "ok":
                out.append("✓ ", style=pal["success"])
                title, tail, tone = past, clock(self.durations.get(pid)), pal["muted"]
            elif status == "active":
                waiting = pid == "restart" and self.waiting_for_restart
                out.append("◐ ", style=pal["accent"])
                title, tail, tone = active, "your turn" if waiting else clock(self.durations.get(pid)), "bold"
            else:
                out.append("· ", style=pal["muted"])
                title, tail, tone = active, "", pal["muted"]
            out.append(title, style=tone)
            out.append(" " * max(1, TIMELINE_WIDTH - 2 - len(title) - len(tail)) + tail,
                       style=pal["accent"] if tail == "your turn" else pal["muted"])
        if self.waiting_for_restart:
            ids = [p[0] for p in phases]
            later = len(ids) - ids.index("restart") - 1 if "restart" in ids else 0
            if later:
                out.append(f"\n\nThe last {WORDS.get(later, str(later))} run by themselves\nwhen you open osx-next "
                           "again.", style=pal["muted"])
        if first_boot and self.s.approval and self.s.approval.get("restart"):
            out.append("\n\nFinished without a question\nafter the restart.", style=pal["muted"])
        return out

    def start_phase(self, pid: str) -> None:
        self._phase_started[pid] = time.monotonic()

    def end_phase(self, pid: str, seconds: float | None = None) -> None:
        started = self._phase_started.pop(pid, None)
        if seconds is None and started is not None:
            seconds = time.monotonic() - started
        if seconds is not None:
            self.durations[pid] = seconds

    # ── the box ──────────────────────────────────────────────────────

    async def show_run(self, title: str, lines: list[str], buttons: list[tuple[str, str, str]],
                       tone: str = "") -> None:
        """The run view: the timeline on the left, a box on the right (secondary button left of the primary)."""
        pal = self.app.get_css_variables()
        self.box_title, self.box_lines, self.box_buttons = title, lines, buttons
        head = self._vm_name(self.s.state["vm"]) if self.s.state["vm"] is not None else ""
        name = short_card_name(self.s.state["gpu"]["name"]) if self.s.state["gpu"] else "the card"
        box_text = Text()
        for idx, line in enumerate(lines):
            box_text.append("\n" if idx else "")
            box_text.append(line, style=self._line_style(line, pal))
        widgets: list[Widget] = [
            Static(Text(f"Setting up the {name}" + (f" for {head}" if head else ""), style="bold"),
                   id="g-run-title"),
            Horizontal(
                Static(self.timeline(), id="g-timeline"),
                Vertical(Static(Text(title, style="bold"), id="g-box-title"),
                         Static(box_text, id="g-box-text"), Static("", id="g-result"),
                         Horizontal(*[Button(label, id=bid, classes=kind) for bid, label, kind in buttons],
                                    id="g-box-buttons"),
                         id="g-box", classes=tone),
                id="g-run-cols"),
            Static("", id="g-run-note"),
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

    @staticmethod
    def _line_style(line: str, pal: dict) -> str:
        if line.startswith(("Refused", "HARD", "Failed", "\u2717")):
            return pal["error"]
        if line.startswith(("Warning", "Careful", "!")):
            return pal["warning"]
        if line.startswith(("Note", "OK:", "\u2713", "\u00b7")):
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
        if self.waiting_for_restart:
            note.append("✓ ", style=pal["success"])
            note.append("Safe to leave here. ", style="bold")
            note.append("Choose Later and your Macs page shows a reminder to finish.", style=pal["muted"])
        try:
            self.query_one("#g-run-note", Static).update(note)
        except Exception:  # noqa: BLE001
            pass

    def set_actions(self, primary: Callable[[], None] | None, secondary: Callable[[], None] | None = None) -> None:
        self._primary_action, self._secondary_action = primary, secondary

    def _guidance(self, g: Guidance) -> None:
        """A box with one primary action (and optionally a second button on its left), in the run view."""
        buttons = [("g-primary-run", g.label, "primary")]
        if g.secondary:
            buttons.insert(0, ("g-secondary", g.secondary[0], "secondary"))
        self.run_worker(self.show_run(g.title, g.lines, buttons, g.tone), exclusive=True, group="gpu-mode")
        self.set_actions(g.action, g.secondary[1] if g.secondary else None)

    # ── stage 4: the host ────────────────────────────────────────────

    def step_host(self, force_ask: bool = False, typed_now: bool = False) -> None:
        s = self.s
        self.start_phase("backup")
        self.job(lambda: plan_for_approval(s, s.approval, typed_now),
                 lambda res: self._host_planned(res[0], res[1], force_ask),
                 "Working out what this host needs...")

    def _host_planned(self, plan: dict, same: bool, force_ask: bool) -> None:
        s = self.s
        self.end_phase("backup")
        if not s.plan_needs_work(plan) and not plan["refusals"] and s.state.get("verify_retry"):
            s.state["verify_retry"] = False
            s.save()
            self._guidance(Guidance(
                "The verify step found a problem, but there is nothing left for this tool to change.",
                ["Fix the failing item in the verify list (see the docs), then check again."],
                "Check again", lambda: (s.goto(6), self.drive()), tone="warn"))
            return
        if (plan.get("optional") or not s.plan_needs_work(plan)) and not plan["refusals"]:
            s.complete(4, skipped=True)
            if 5 not in s.state["done"]:
                s.complete(5, skipped=True)
            self.drive()
            return
        if same and not force_ask:
            self._apply_host(plan, bool((s.approval or {}).get("typed")))
            return
        lines = plan_lines(plan) + ["", "This is exactly what will change (each file is backed up first):",
                                    host_setup.render_preview(plan)]
        self._ask_go = self._ask_apply(plan)
        self.run_worker(self._ask_host(lines, needs_typed(plan)), exclusive=True, group="gpu-mode")

    def _ask_apply(self, plan: dict) -> Callable[[], None]:
        """What "Apply these changes" does for *plan*: ask for the phrase when the plan wants it."""
        typed_needed = needs_typed(plan)

        def go() -> None:
            if plan["refusals"] and not typed_needed:
                return
            if not typed_needed:
                self._apply_host(plan, False)
                return
            if self.query_one("#g-typed", Input).value.strip() != CONFIRM_PHRASE:
                self.say(f'Type "{CONFIRM_PHRASE}" first.', fail=True)
            elif plan["refusals"]:  # the phrase lifts the only-display-adapter stop: plan again with it
                self.s.approve({**(self.s.approval or {}), "allow_no_console": True, "typed": True})
                self.step_host(force_ask=True, typed_now=True)
            else:
                self._apply_host(plan, True)

        return go

    async def _ask_host(self, lines: list[str], typed: bool) -> None:
        await self.show_run("This differs from what you approved" if self.s.approval else "Apply these changes",
                            lines, [("g-ask", "Apply these changes", "primary")], "warn")
        if typed:
            box = self.query_one("#g-box", Vertical)
            await box.mount(Static(f'Type "{CONFIRM_PHRASE}" to continue'), Input(id="g-typed"),
                            before=self.query_one("#g-box-buttons"))
        self.set_actions(lambda: self._ask_go() if self._ask_go else None)

    def _apply_host(self, plan: dict, typed: bool) -> None:
        s = self.s
        self.start_phase("host")

        def done(res: dict) -> None:
            if not res["ok"]:
                self.say(f"Failed: {res['error']}" + (" The files were put back." if res["rolled_back"] else ""),
                         fail=True)
                self._guidance(Guidance("The host setup did not finish", [res["error"]], "Try again",
                                        lambda: self.step_host()))
                return
            self.end_phase("host")
            s.state["backup_dir"] = res["backup_dir"]
            s.save()
            self.drive()

        self.job(lambda: s.host_apply(plan, typed), done, "Applying...")

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
            s.state.setdefault("restart_at", time.time())
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
        # two lines, so the line break never falls inside "3 minutes"
        lines = ["The host needs it to hand the card over.", "About 3 minutes.", "", "What a restart means right now"]
        stuck = {vmid for vmid, _n, _m in installing}
        lines += [f"● {name} is shut down cleanly first" for vmid, name in running if vmid not in stuck]
        for _vmid, name, minutes in installing:
            lines += [f"! {name} is still installing, {minutes} min in", "  A restart now interrupts that install."]
        if not running:
            lines.append("No VM is running.")
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
