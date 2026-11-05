from __future__ import annotations

import logging
from threading import Thread
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.widgets import Button, Checkbox, Input, Select, Static

from . import bios_guide, host_setup
from .gpu_setup import TROUBLESHOOTING, Session, card_text

log = logging.getLogger(__name__)

__all__ = ["GpuSetupMixin"]

_ALL_INPUTS = ("gp_gpu_select", "gp_vm_label", "gp_vmid", "gp_display_select", "gp_notice_title", "gp_notice",
               "gp_ack_cb", "gp_confirm_cb", "gp_stop_cb", "gp_qr")


class GpuSetupMixin:
    """The guided GPU passthrough panel. A Session does the work; this renders it step by step."""

    _gp: Session | None = None
    _gp_cards: list = []
    _gp_plan: dict | None = None
    _gp_vm_plan: Any = None
    _gp_branch_shown = False
    _gp_fix = ""

    # ── helpers ──────────────────────────────────────────────────────

    def _gp_session(self) -> Session:
        if self._gp is None:
            self._gp = Session()
        return self._gp

    def _gp_show(self, *ids: str) -> None:
        for wid in _ALL_INPUTS:
            self.query_one(f"#{wid}").set_class(wid not in ids, "hidden")  # type: ignore[attr-defined]

    def _gp_set(self, title: str, body: str, primary: str, secondary: str = "", back: bool = False,
                primary_disabled: bool = False) -> None:
        q = self.query_one  # type: ignore[attr-defined]
        s = self._gp_session()
        q("#gp_rail", Static).update(s.rail())
        q("#gp_title", Static).update(title)
        q("#gp_body", Static).update(body)
        prim = q("#gp_primary_btn", Button)
        prim.label, prim.disabled = primary, primary_disabled
        sec = q("#gp_secondary_btn", Button)
        sec.label = secondary
        sec.set_class(not secondary, "hidden")
        q("#gp_back_btn", Button).set_class(not back, "hidden")
        q("#gp_undo_btn", Button).set_class(not (s.state["host_changes"] or s.state["vm_records"]), "hidden")

    def _gp_result(self, text: str, fail: bool = False) -> None:
        box = self.query_one("#gp_result", Static)  # type: ignore[attr-defined]
        box.remove_class("hidden")
        (box.add_class if fail else box.remove_class)("nv_result_fail")
        box.update(text)

    def _gp_qr(self, lines: list[str]) -> None:
        qr = self.query_one("#gp_qr", Static)  # type: ignore[attr-defined]
        qr.update(Text("\n".join(lines), style="black on white") if lines else "")
        qr.set_class(not lines, "hidden")

    def _gp_async(self, work: Callable[[], Any], done: Callable[[Any], None], note: str = "Working...") -> None:
        self.query_one("#gp_result", Static).add_class("hidden")  # type: ignore[attr-defined]
        self.query_one("#gp_primary_btn", Button).disabled = True  # type: ignore[attr-defined]
        self.query_one("#gp_body", Static).update(note)  # type: ignore[attr-defined]

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                log.exception("GPU setup step failed")
                self.call_from_thread(self._gp_failed, str(exc))  # type: ignore[attr-defined]
                return
            self.call_from_thread(done, result)  # type: ignore[attr-defined]

        Thread(target=runner, daemon=True).start()

    def _gp_failed(self, message: str) -> None:
        self._gp_result("Something went wrong: " + message, fail=True)
        self.query_one("#gp_primary_btn", Button).disabled = False  # type: ignore[attr-defined]

    # ── entry ────────────────────────────────────────────────────────

    def _gp_open(self) -> None:
        s = self._gp_session()
        if s.state["finished"]:
            self._gp_set("Setup finished", s.summary(), "Start a new setup")
            self._gp_show()
            return
        self._gp_render()

    def _gp_render(self) -> None:
        step = self._gp_session().state["step"]
        {1: self._gp_scan, 2: self._gp_scan, 3: self._gp_bios, 4: self._gp_host, 5: self._gp_reboot,
         6: self._gp_verify, 7: self._gp_vm, 8: self._gp_first_boot}[step]()

    # ── 1/2 scan and choose ──────────────────────────────────────────

    def _gp_scan(self) -> None:
        s = self._gp_session()
        self._gp_show()
        self._gp_set("Step 1-2: scan and choose", "Looking for graphics cards...", "Choose", primary_disabled=True)
        self._gp_async(lambda: (s.summary_line(), s.scan()), self._gp_scan_done)

    def _gp_scan_done(self, result: Any) -> None:
        summary, cards = result
        self._gp_cards = cards
        usable = [c for c in cards if c.selectable]
        lines = [summary, ""] + [card_text(c) + ("" if c.selectable else "\n  Cannot be used: " + c.why_not) for c in cards]
        if not usable:
            lines.append("\nNo card here can run macOS graphics.")
        select = self.query_one("#gp_gpu_select", Select)  # type: ignore[attr-defined]
        select.set_options([(f"{c.name} ({c.bdf})", c.bdf) for c in usable])
        self._gp_set("Step 1-2: scan and choose", "\n".join(lines), "Choose this card", primary_disabled=not usable)
        self._gp_show("gp_gpu_select", "gp_vm_label", "gp_vmid", "gp_display_select")
        self._gp_validate()

    def _gp_selected_card(self):
        value = self.query_one("#gp_gpu_select", Select).value  # type: ignore[attr-defined]
        return next((c for c in self._gp_cards if c.bdf == value), None) if value is not Select.BLANK else None

    def _gp_validate(self) -> None:
        if self._gp_session().state["step"] > 2:
            return
        card = self._gp_selected_card()
        nvidia = card is not None and card.vendor == "nvidia"
        shown = ["gp_gpu_select", "gp_vm_label", "gp_vmid"] + ([] if nvidia else ["gp_display_select"])
        if nvidia:
            shown += ["gp_notice_title", "gp_notice", "gp_ack_cb"]
        self._gp_show(*shown)
        acked = self.query_one("#gp_ack_cb", Checkbox).value  # type: ignore[attr-defined]
        self.query_one("#gp_primary_btn", Button).disabled = card is None or (nvidia and not acked)  # type: ignore[attr-defined]

    def _gp_choose(self) -> None:
        s = self._gp_session()
        card = self._gp_selected_card()
        if card is None:
            return
        text = self.query_one("#gp_vmid", Input).value.strip()  # type: ignore[attr-defined]
        vmid = int(text) if text.isdigit() else None
        display = str(self.query_one("#gp_display_select", Select).value)  # type: ignore[attr-defined]
        ack = self.query_one("#gp_ack_cb", Checkbox).value  # type: ignore[attr-defined]

        def done(issues: list[str]) -> None:
            if issues:
                self._gp_result("\n".join("Refused: " + i for i in issues), fail=True)
                self._gp_validate()
                return
            self._gp_render()

        self._gp_async(lambda: s.choose(card.bdf, vmid, display, ack), done, "Checking the choices...")

    # ── 3 BIOS ───────────────────────────────────────────────────────

    def _gp_bios(self) -> None:
        s = self._gp_session()
        self._gp_show()
        self._gp_set("Step 3: BIOS", "Checking the BIOS settings...", "Re-check", primary_disabled=True)

        def work() -> Any:
            items = s.bios_items()
            return items, s.bios_card(items), s.running_vms()

        self._gp_async(work, self._gp_bios_done)

    def _gp_bios_done(self, result: Any) -> None:
        s = self._gp_session()
        items, card, running = result
        if card is None:
            s.complete(3, skipped=True)
            self._gp_render()
            return
        data = bios_guide.load_data()
        ticks = "\n".join(f"  [{'ok' if i['ok'] else '!!'}] {data['settings'][i['key']]['label']}" for i in items)
        extra = ""
        if running:
            extra = "\n\nRunning VMs that a reboot will stop: " + ", ".join(f"{i} ({n})" for i, n in running)
        self._gp_card = card
        self._gp_set("Step 3: BIOS (take a photo of this screen)", card["text"] + f"\n\nSaved to {card['path']}\n\n" + ticks + extra,
                     "I changed it, re-check", "Reboot into BIOS now" if card["firmware_reboot"] else "I'll do it later", back=True)
        self._gp_qr(card["qr"])
        self._gp_show("gp_qr")

    # ── 4 host ───────────────────────────────────────────────────────

    def _gp_host(self) -> None:
        s = self._gp_session()
        self._gp_show()
        self._gp_set("Step 4: host configuration", "Working out what this host needs...", "Apply", primary_disabled=True)
        confirmed = self.query_one("#gp_confirm_cb", Checkbox).value  # type: ignore[attr-defined]
        self._gp_async(lambda: s.host_plan(allow_no_console=confirmed), self._gp_host_done)

    def _gp_host_done(self, plan: dict) -> None:
        s = self._gp_session()
        self._gp_plan = plan
        lines = []
        for key, prefix in (("refusals", "Refused: "), ("hard_warnings", "HARD WARNING: "), ("warnings", "Warning: "),
                            ("notes", "Note: "), ("manual", "Manual step: "), ("skipped", "OK: ")):
            lines += [prefix + t for t in plan[key]]
        if not s.plan_needs_work(plan) and not plan["refusals"] and s.state.get("verify_retry"):
            s.state["verify_retry"] = False
            s.save()
            self._gp_set("Step 4: host configuration", "The verify step found a problem, but there is nothing left for this "
                         "tool to change. Fix the failing item in the verify list (see the docs).", "Check again", back=True)
            return
        if not s.plan_needs_work(plan) and not plan["refusals"]:
            s.complete(4, skipped=True)
            if 5 not in s.state["done"]:
                s.complete(5, skipped=True)
            self._gp_render()
            return
        if plan["changes"] or plan["commands"]:
            lines += ["", "This is exactly what will change (each file is backed up first):", host_setup.render_preview(plan)]
        need_confirm = bool(plan["hard_warnings"]) or any("only display adapter" in r for r in plan["refusals"])
        self._gp_set("Step 4: host configuration", "\n".join(lines), "Apply these changes", back=True,
                     primary_disabled=bool(plan["refusals"]) and not need_confirm)
        self._gp_show("gp_confirm_cb" if need_confirm else "gp_qr")
        self.query_one("#gp_qr").add_class("hidden")  # type: ignore[attr-defined]

    def _gp_apply_host(self) -> None:
        s = self._gp_session()
        plan = self._gp_plan
        if plan is None:
            return
        confirmed = self.query_one("#gp_confirm_cb", Checkbox).value  # type: ignore[attr-defined]
        if plan["refusals"] and confirmed:
            self._gp_host()
            return
        self._gp_async(lambda: s.host_apply(plan, confirmed), self._gp_apply_done, "Applying...")

    def _gp_apply_done(self, res: dict) -> None:
        if not res["ok"]:
            self._gp_result(f"Failed: {res['error']}" + (" The files were put back." if res["rolled_back"] else ""), fail=True)
            self.query_one("#gp_primary_btn", Button).disabled = False  # type: ignore[attr-defined]
            return
        self._gp_result(f"Done. Backups are in {res['backup_dir']}. Undo everything with the Undo button.")
        self._gp_render()

    # ── 5 reboot, 6 verify ───────────────────────────────────────────

    def _gp_reboot(self) -> None:
        s = self._gp_session()
        self._gp_show()
        if not s.state.get("reboot_needed"):
            s.complete(5, skipped=True)
            self._gp_render()
            return
        if not s.reboot_pending():
            self._gp_render()
            return
        s.pause()
        running = s.running_vms()
        body = ("The host must reboot so the new kernel arguments and the early card binding take effect.\n"
                + ("Running VMs that will stop: " + ", ".join(f"{i} ({n})" for i, n in running) + "\n" if running else "")
                + f"\nIf you reboot later, run: {s.state['resume_cmd']}")
        self._gp_set("Step 5: reboot", body, "Reboot now", "Later")

    def _gp_verify(self) -> None:
        s = self._gp_session()
        self._gp_show()
        self._gp_set("Step 6: verify", "Checking the host...", "Continue", primary_disabled=True)
        self._gp_async(s.verify, self._gp_verify_done)

    def _gp_verify_done(self, checks: list[dict]) -> None:
        s = self._gp_session()
        marks = {"ok": "ok", "fail": "!!", "warn": "??", "info": ".."}
        body = "\n".join(f"  [{marks[c['status']]}] {c['label']}" + (f" - {c['detail']}" if c["detail"] else "") for c in checks)
        fails = [c for c in checks if c["status"] == "fail"]
        if not fails:
            self._gp_render()
            return
        if any("Reboot the host" in c["detail"] for c in fails) and s.rebooted_since_apply():
            self._gp_set("Step 6: verify", body + "\n\nThe host has rebooted but the kernel arguments are still not active. "
                         "Check that the boot loader picked them up (GRUB: /boot/grub/grub.cfg, systemd-boot: "
                         "proxmox-boot-tool refresh).", "Check again")
            return
        if any("Reboot the host" in c["detail"] for c in fails):
            s.mark_reboot_needed()
            self._gp_render()
            self._gp_result("The change is saved but not active yet. Reboot first.")
            return
        self._gp_fix = fails[0]["fix"]
        label = {"bios": "Back to the BIOS step", "host": "Back to the host step"}.get(self._gp_fix, "Check again")
        self._gp_set("Step 6: verify", body, label)

    # ── 7 VM, 8 first boot ───────────────────────────────────────────

    def _gp_vm(self) -> None:
        s = self._gp_session()
        self._gp_show("gp_vmid", "gp_vm_label", "gp_stop_cb")
        if s.state["vm"] is not None:
            self.query_one("#gp_vmid", Input).value = str(s.state["vm"])  # type: ignore[attr-defined]
        self._gp_set("Step 7: attach the card to the VM",
                     "Enter the macOS VM that gets the card. It must have finished its install.", "Preview", back=True)

    def _gp_vm_preview(self) -> None:
        s = self._gp_session()
        text = self.query_one("#gp_vmid", Input).value.strip()  # type: ignore[attr-defined]
        if not text.isdigit():
            self._gp_result("Enter a VM ID first.", fail=True)
            return
        s.state["vm"] = int(text)
        stop = self.query_one("#gp_stop_cb", Checkbox).value  # type: ignore[attr-defined]

        def done(plan: Any) -> None:
            self._gp_vm_plan = plan
            lines = ["Refused: " + i for i in plan.issues] + plan.hints + ["Warning: " + w for w in plan.warnings]
            if plan.ok:
                lines += ["", f"This is what will change on VM {s.state['vm']} (a config snapshot is saved first):"]
                lines += [f"  {i:02d}. {st.title}" for i, st in enumerate(plan.steps, start=1)]
            self._gp_set("Step 7: attach the card to the VM", "\n".join(lines), "Attach the card now" if plan.ok else "Preview",
                         back=True)
            self._gp_show("gp_vmid", "gp_vm_label", "gp_stop_cb")

        self._gp_async(lambda: s.vm_plan(allow_stop=stop), done, "Checking the VM and the card...")

    def _gp_vm_apply(self) -> None:
        s = self._gp_session()
        plan = self._gp_vm_plan
        if plan is None or not plan.ok:
            self._gp_vm_preview()
            return

        def work() -> Any:
            from pathlib import Path
            from .defaults import DEFAULT_ISO_DIR
            from .executor import apply_plan
            from .oc_builder import ensure_amfipass
            from .rollback import create_snapshot
            if s.state["gpu"]["vendor"] == "nvidia":
                ensure_amfipass(Path(DEFAULT_ISO_DIR))
            create_snapshot(s.state["vm"])
            s.begin_attach()
            return apply_plan(plan.steps, execute=True)

        def done(result: Any) -> None:
            if not result.ok:
                self._gp_result(f"Attaching failed. Log: {result.log_path}", fail=True)
                return
            s.vm_applied()
            self._gp_vm_plan = None
            self._gp_render()

        self._gp_async(work, done, "Attaching the card...")

    def _gp_first_boot(self) -> None:
        s = self._gp_session()
        self._gp_branch_shown = False
        nvidia = s.state["gpu"]["vendor"] == "nvidia"
        self._gp_show("gp_qr")
        self._gp_qr(s.first_boot_qr())
        if nvidia:
            self._gp_set("Step 8: first boot", s.first_boot_text(), "Done")
        else:
            self._gp_set("Step 8: first boot", s.first_boot_text() + "\n\nDo you see the macOS desktop on your monitor?",
                         "Yes, I see the desktop", "No, help me")

    def _gp_finish(self) -> None:
        s = self._gp_session()
        s.finish()
        self._gp_show()
        self._gp_set("Setup finished", s.summary(), "Start a new setup")

    # ── buttons ──────────────────────────────────────────────────────

    def _gp_primary(self) -> None:
        s = self._gp_session()
        step = s.state["step"]
        if s.state["finished"]:
            s.restart()
            self._gp_open()
        elif step <= 2:
            self._gp_choose()
        elif step == 3:
            self._gp_bios()
        elif step == 4:
            self._gp_apply_host()
        elif step == 5:
            ok, out = s.reboot_host()
            self._gp_result("Rebooting..." if ok else "Could not start the reboot: " + out, fail=not ok)
        elif step == 6:
            target = {"bios": 3, "host": 4}.get(self._gp_fix)
            if target:
                s.send_back(target)
            self._gp_render()
        elif step == 7:
            self._gp_vm_apply() if self._gp_vm_plan is not None else self._gp_vm_preview()
        else:
            self._gp_finish()

    def _gp_secondary(self) -> None:
        s = self._gp_session()
        step = s.state["step"]
        if step == 3:
            card = getattr(self, "_gp_card", None)
            if card and card["firmware_reboot"]:
                ok, out = s.reboot_to_bios()
                self._gp_result("Rebooting into the BIOS..." if ok else "Could not start the reboot: " + out, fail=not ok)
            else:
                self._gp_result("When you are back in Proxmox, run: " + s.pause())
        elif step == 5:
            self._gp_result("Reboot when it suits you, then run: " + s.pause())
        elif step == 8:
            self._gp_set("Step 8: first boot - troubleshooting", "\n\n".join(TROUBLESHOOTING.values()),
                         "Yes, I see the desktop", "Back")

    def _gp_back(self) -> None:
        s = self._gp_session()
        target = max(1, s.state["step"] - 1)
        while target > 1 and target in s.state["skipped"]:
            target -= 1
        s.goto(target)
        self._gp_render()

    def _gp_undo(self) -> None:
        s = self._gp_session()

        def done(result: Any) -> None:
            ok, messages = result
            self._gp_result("\n".join(messages) or "Nothing to undo.", fail=not ok)
            if ok:
                self._gp = None
                self._gp_open()

        self._gp_async(lambda: s.undo(allow_stop=self.query_one("#gp_stop_cb", Checkbox).value), done, "Undoing...")  # type: ignore[attr-defined]
