"""The steps after the restart: check the card is free, attach it to the Mac, start the Mac."""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Static

from .. import nullmoth, nullmoth_finish
from ..gpu_flow import attach_differs, replan_attach, stop_mac, vm_fingerprint
from ..gpu_setup import CardChanged, VmOption
from ..services import fetch_vm_info
from ..widgets.rows import ChoiceRow, Option
from .base import ConfirmDialog

MARKS = {"ok": "✓", "fail": "✗", "warn": "!", "info": "·"}


class AttachMixin:
    """Stage 6 (verify), stage 7 (attach) and the Mac pick that "decide later" ends with."""

    # ── stage 6: check the card is free ──────────────────────────────

    def step_verify(self) -> None:
        s = self.s
        restart_at = s.state.get("restart_at")
        if restart_at and "restart" not in self.durations:
            self.end_phase("restart", time.time() - float(restart_at))  # the restart box to the first check
        self.start_phase("check")
        self.job(s.verify, self._verify_done, "Checking the host...")

    def _verify_done(self, checks: list[dict]) -> None:
        s = self.s
        body = [f"{MARKS[c['status']]} {c['label']}" + (f" - {c['detail']}" if c["detail"] else "") for c in checks]
        fails = [c for c in checks if c["status"] == "fail"]
        if not fails:
            self.end_phase("check")
            self.drive()
            return
        needs_reboot = any("Reboot the host" in c["detail"] for c in fails)
        if needs_reboot and s.rebooted_since_apply():
            self._guidance("The card is not free yet", body + [
                "", "The host has rebooted but the kernel arguments are still not active. Check that the boot "
                "loader picked them up (GRUB: /boot/grub/grub.cfg, systemd-boot: proxmox-boot-tool refresh), "
                "then check again."], "Check again", self.step_verify)
            return
        if needs_reboot:
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
        if s.state["vm"] is None:
            self.job(lambda: s.vm_options(s.state["gpu"]["vendor"]), self._pick_mac, "Looking for your Macs...")
            return
        self.job(self._attach_probe, self._attach_probed, "Checking the Mac and the card...")

    def _attach_probe(self) -> Any:
        s = self.s
        info = fetch_vm_info(s.state["vm"], adapter=s.pve)
        return s.vm_plan(allow_stop=True), bool(info and info.status == "running")

    @staticmethod
    def _plan_lines(plan: Any) -> list[str]:
        return ["Refused: " + i for i in plan.issues] + plan.hints + ["Warning: " + w for w in plan.warnings]

    def _attach_probed(self, result: Any) -> None:
        s = self.s
        plan, running = result
        if not plan.ok:
            self._guidance("The Mac cannot take the card yet", self._plan_lines(plan), "Check again",
                           self.step_attach, ("Pick another Mac", self._pick_other))
            return
        fp = vm_fingerprint(s.state["gpu"]["bdf"], s.state["vm"], s.state["display"], plan.steps)
        if attach_differs(s.approval, s.state, plan):
            self._show_differs(plan, running, fp)
            return
        self._attach_go(running, fp)

    def _show_differs(self, plan: Any, running: bool, fp: str) -> None:
        lines = self._plan_lines(plan)
        lines += ["", f"This is what will change on VM {self.s.state['vm']} (a config snapshot is saved first):"]
        lines += [f"  {i:02d}. {st.title}" for i, st in enumerate(plan.steps, start=1)]
        self._guidance("This differs from what you approved", lines, "Attach the card now",
                       lambda: self._attach_go(running, fp), tone="warn")

    def _pick_other(self) -> None:
        self.s.state["vm"] = None
        self.s.save()
        self.step_attach()

    def _attach_go(self, running: bool, fp: str) -> None:
        """The attach, after the one thing that may still ask: a running Mac has to be stopped first."""
        if not running:
            self._attach_apply(fp, stop=False)
            return
        vmid = self.s.state["vm"]

        def answer(result: str | None) -> None:
            if result == "yes":
                self._attach_apply(fp, stop=True)

        self.app.push_screen(ConfirmDialog(
            f"Stop {self._vm_name(vmid)} first?",
            [f"VM {vmid} is running and has to be stopped before it can take the card.",
             "Whatever is open in the guest is closed without saving."], confirm="Stop it", danger=True), answer)

    def _attach_apply(self, fp: str, stop: bool) -> None:
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
            plan, same = replan_attach(s, fp, stop)  # what is about to be written is what was shown
            if not same:
                return "changed", plan, None
            s.begin_attach()  # the card must still be the chosen one: raises CardChanged before anything is written
            if nvidia:
                if stop:
                    stop_mac(s, s.state["vm"])  # finish_nvidia refuses a running Mac
                return "done", plan, s.finish_nvidia(release, progress)
            create_snapshot(s.state["vm"])
            result = apply_plan(plan.steps, execute=True)
            if result.ok:
                started = time.monotonic()
                s.pve.qm("start", str(s.state["vm"]))  # the Mac starts on the card
                self.durations["start"] = time.monotonic() - started
            return "done", plan, result

        self.job(work, lambda res: self._attach_done(nvidia, res), note)

    def _progress(self, text: str) -> None:
        self.progress = text
        self.paint_run()

    def _attach_done(self, nvidia: bool, res: Any) -> None:
        s = self.s
        kind, plan, result = res
        if kind == "changed":  # the Mac was edited after it was shown: look again and show what would be written now
            self.step_attach()
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

    # ── "decide later" ends here: pick the Mac and see what it gets ──

    def _pick_mac(self, options: list[VmOption]) -> None:
        usable = [o for o in options if o.eligible or o.status == "running"]
        self.start_phase("attach")
        self.run_worker(self._show_pick(options, usable), exclusive=True, group="gpu-mode")

    async def _show_pick(self, options: list[VmOption], usable: list[VmOption]) -> None:
        nvidia = self.s.state["gpu"]["vendor"] == "nvidia"
        pal = self.app.get_css_variables()
        choices = [Option(str(o.vmid), o.name or f"VM {o.vmid}", o.eligible or o.status == "running")
                   for o in options] or [Option("", "none")]
        rows: list[Widget] = [ChoiceRow("Mac", choices, str(usable[0].vmid) if usable else "", id="g-pick")]
        if nvidia:
            rows.append(ChoiceRow("Driver", [Option("latest", "Latest")], "latest", id="g-driver", hint_lines=0))
        widgets: list[Widget] = [
            Static(Text("Pick the Mac that gets the card", style="bold"), id="g-run-title"),
            Static(Text("The host is ready. The Mac must have finished its install.", style=pal["muted"])),
            VerticalScroll(*rows, Static("", id="g-pick-info"), id="g-form"),
            Static("", id="g-result"),
            Horizontal(Button("Attach it", id="g-pick-go", classes="primary", disabled=True), id="g-box-buttons"),
        ]
        self.box_buttons = [("g-pick-go", "Attach it", "primary")]
        await self.set_mode("run", widgets)
        self.picked_plan = None
        self.set_actions(self._pick_attach)
        self.query_one("#g-pick", ChoiceRow).focus()
        if nvidia:
            self._fill_releases()
        self._pick_changed()

    def _pick_changed(self) -> None:
        """Work out what the highlighted Mac would get and show every step of it."""
        value = self.query_one("#g-pick", ChoiceRow).value
        self.picked_plan = None
        self.query_one("#g-pick-go", Button).disabled = True
        if not value.isdigit():
            self.query_one("#g-pick-info", Static).update("")
            return
        vmid = int(value)
        self.job(lambda: self.s.vm_plan_for(vmid), lambda plan: self._pick_planned(vmid, plan))

    def _pick_planned(self, vmid: int, plan: Any) -> None:
        pal = self.app.get_css_variables()
        try:
            if self.query_one("#g-pick", ChoiceRow).value != str(vmid):
                return
            info = Text()
            for line in plan.issues:
                info.append("Refused: " + line + "\n", style=pal["error"])
            for line in plan.hints:
                info.append(line + "\n", style=pal["muted"])
            for line in plan.warnings:
                info.append("Warning: " + line + "\n", style=pal["warning"])
            if plan.steps:
                info.append(f"\nThis is what will change on VM {vmid} (a config snapshot is saved first):\n")
                info.append("\n".join(f"  {i:02d}. {st.title}" for i, st in enumerate(plan.steps, start=1)))
            self.query_one("#g-pick-info", Static).update(info)
            self.picked_plan = (vmid, plan)
            self.query_one("#g-pick-go", Button).disabled = not plan.ok
        except Exception:  # noqa: BLE001 - the mode changed under the worker
            return

    def _pick_attach(self) -> None:
        """"Attach it" approves the settings that are on screen: the real fingerprint, never a wildcard."""
        s = self.s
        picked = self.picked_plan
        if picked is None or not picked[1].ok:
            self.say("Pick a Mac first.", fail=True)
            return
        vmid, plan = picked
        s.state["vm"] = vmid
        s.save()
        record = {**(s.approval or {}), "vm": vmid,
                  "vm_fp": vm_fingerprint(s.state["gpu"]["bdf"], vmid, s.state["display"], plan.steps)}
        if s.state["gpu"]["vendor"] == "nvidia":
            record["release"] = self.query_one("#g-driver", ChoiceRow).value
        s.approve(record)
        self.step_attach()
