from __future__ import annotations

import logging
from threading import Thread
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.widgets import Button, Checkbox, Input, OptionList, RadioButton, RadioSet, Select, Static
from textual.widgets.option_list import Option

from . import bios_guide, host_setup
from .gpu_setup import DISPLAY_OPTIONS, TROUBLESHOOTING, CardChanged, Session, VmOption, card_text
from . import nullmoth
from .nullmoth import ACK_PHRASE
from .screens.gpu_panel import CONFIRM_PHRASE, display_description

log = logging.getLogger(__name__)

__all__ = ["GpuSetupMixin", "pack_fields", "qr_spans"]

_ALL_INPUTS = ("gp_gpu_select", "gp_vm_label", "gp_vm_list", "gp_display_label", "gp_display_radio",
               "gp_notice_title", "gp_notice", "gp_ack_label", "gp_ack_in", "gp_confirm_label", "gp_confirm_in",
               "gp_stop_cb")
_LATER, _CREATE = "later", "create"


def pack_fields(fields: list[str], width: int, sep: str = "   ") -> list[str]:
    """Pack *fields* into lines no wider than *width*, breaking only between fields."""
    lines: list[str] = []
    for field in fields:
        if lines and len(lines[-1]) + len(sep) + len(field) <= width:
            lines[-1] += sep + field
        else:
            lines.append(field)
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


class GpuSetupMixin:
    """The guided GPU passthrough panel. A Session does the work; this renders it step by step."""

    _gp: Session | None = None
    _gp_cards: list = []
    _gp_plan: dict | None = None
    _gp_vm_plan: Any = None
    _gp_vm_opts: list[VmOption] = []
    _gp_armed: str | None = None
    _gp_fix = ""

    # ── helpers ──────────────────────────────────────────────────────

    def _gp_session(self) -> Session:
        if self._gp is None:
            self._gp = Session()
        return self._gp

    def _gp_width(self) -> int:
        width = self.query_one("#gp_body").size.width or 100  # type: ignore[attr-defined]
        return max(60, min(width - 1, 140))

    def _gp_show(self, *ids: str) -> None:
        for wid in _ALL_INPUTS:
            self.query_one(f"#{wid}").set_class(wid not in ids, "hidden")  # type: ignore[attr-defined]
        self.query_one("#gp_stop_btn").add_class("hidden")  # type: ignore[attr-defined]

    def _gp_reset_confirmations(self) -> None:
        """Typed confirmations never carry over: every screen that needs one starts with them empty."""
        for wid in ("gp_ack_in", "gp_confirm_in"):
            self.query_one(f"#{wid}", Input).value = ""  # type: ignore[attr-defined]
        self._gp_armed = None

    def _gp_set(self, title: str, body: Any, primary: str, secondary: str = "", back: bool = False,
                primary_disabled: bool = False, tertiary: str = "") -> None:
        q = self.query_one  # type: ignore[attr-defined]
        s = self._gp_session()
        self._gp_armed = None
        q("#gp_rail", Static).update(s.rail())
        q("#gp_title", Static).update(title)
        q("#gp_body", Static).update(body)
        prim = q("#gp_primary_btn", Button)
        prim.label, prim.disabled = primary, primary_disabled
        for wid, label in (("gp_secondary_btn", secondary), ("gp_tertiary_btn", tertiary)):
            btn = q(f"#{wid}", Button)
            btn.label = label
            btn.set_class(not label, "hidden")
        q("#gp_back_btn", Button).set_class(not back, "hidden")
        q("#gp_undo_btn", Button).set_class(not s.has_undo(), "hidden")

    def _gp_result(self, text: str, fail: bool = False) -> None:
        box = self.query_one("#gp_result", Static)  # type: ignore[attr-defined]
        box.remove_class("hidden")
        (box.add_class if fail else box.remove_class)("nv_result_fail")
        box.update(text)

    def _gp_async(self, work: Callable[[], Any], done: Callable[[Any], None], note: str = "Working...") -> None:
        self.query_one("#gp_result", Static).add_class("hidden")  # type: ignore[attr-defined]
        self.query_one("#gp_primary_btn", Button).disabled = True  # type: ignore[attr-defined]
        self.query_one("#gp_body", Static).update(note)  # type: ignore[attr-defined]

        def runner() -> None:
            try:
                result = work()
            except CardChanged as exc:
                self.call_from_thread(self._gp_card_changed, exc)  # type: ignore[attr-defined]
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("GPU setup step failed")
                self.call_from_thread(self._gp_failed, str(exc))  # type: ignore[attr-defined]
                return
            self.call_from_thread(done, result)  # type: ignore[attr-defined]

        Thread(target=runner, daemon=True).start()

    def _gp_failed(self, message: str) -> None:
        self._gp_result("Something went wrong: " + message, fail=True)
        self.query_one("#gp_primary_btn", Button).disabled = False  # type: ignore[attr-defined]

    def _gp_card_changed(self, exc: Exception) -> None:
        """The chosen card moved or changed: say so plainly and send the user back to the scan."""
        message = self._gp_session().card_changed(exc)
        self._gp_render()
        self._gp_result(message, fail=True)

    # ── entry ────────────────────────────────────────────────────────

    def _gp_open(self) -> None:
        s = self._gp_session()
        self._gp_reset_confirmations()
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
        self._gp_reset_confirmations()
        self._gp_set("Step 1-2: scan and choose", "Looking for graphics cards...", "Choose this card", primary_disabled=True)
        self._gp_vm_list_vendor = ""

        def work() -> Any:
            cards = s.scan()
            vendors = {c.vendor for c in cards if c.selectable}
            return s.summary_fields(), cards, {v: s.vm_options(v) for v in vendors}

        self._gp_async(work, self._gp_scan_done)

    def _gp_cards_renderable(self, cards: list) -> Text:
        """Every card at the same full width. Usable cards get a green accent; the rest are dimmed."""
        width = self._gp_width()
        out = Text()
        for idx, card in enumerate(cards):
            if idx:
                out.append("\n\n")
            for line in card_text(card, width).split("\n"):
                if card.selectable:
                    out.append(line[:2], style="bold #2ec27e")
                    out.append(line[2:] + "\n", style="#d6e4f0" if "=" not in line[:6] else "#2ec27e")
                else:
                    out.append(line + "\n", style="dim")
            out.rstrip()
        return out

    def _gp_scan_done(self, result: Any) -> None:
        fields, cards, pool = result
        self._gp_cards = cards
        self._gp_vm_pool = {"amd": [], "nvidia": [], **pool}
        usable = [c for c in cards if c.selectable]
        body = Text("\n".join(pack_fields(fields, self._gp_width())) + "\n\n")
        body.append_text(self._gp_cards_renderable(cards))
        if not usable:
            body.append("\n\nNo card here can run macOS graphics. See the docs for cards that work.")
        select = self.query_one("#gp_gpu_select", Select)  # type: ignore[attr-defined]
        select.set_options([(f"{c.name} ({c.bdf})", c.bdf) for c in usable])
        if usable:
            select.value = usable[0].bdf
        self._gp_set("Step 1-2: scan and choose", body, "Choose this card", primary_disabled=not usable)
        self._gp_show("gp_gpu_select", "gp_vm_label", "gp_vm_list", "gp_display_label", "gp_display_radio")
        self._gp_validate()

    def _gp_selected_card(self):
        value = self.query_one("#gp_gpu_select", Select).value  # type: ignore[attr-defined]
        return next((c for c in self._gp_cards if c.bdf == value), None) if value is not Select.BLANK else None

    def _gp_fill_vm_list(self, options: list[VmOption], extras: bool) -> None:
        """The VM picker: this host's macOS VMs, ineligible ones listed but disabled with the reason."""
        olist = self.query_one("#gp_vm_list", OptionList)  # type: ignore[attr-defined]
        previous = self._gp_vm_choice()
        self._gp_vm_opts = options
        olist.clear_options()
        for opt in options:
            running = opt.status == "running"
            olist.add_option(Option(opt.label, id=str(opt.vmid), disabled=not opt.eligible and not running))
        if extras:
            olist.add_option(Option("Set up the host now, pick a VM later", id=_LATER))
            olist.add_option(Option("Create a new macOS VM first", id=_CREATE))
        ids = [str(o.vmid) for o in options] + ([_LATER, _CREATE] if extras else [])
        pick = previous if previous in ids else next((str(o.vmid) for o in options if o.eligible), _LATER if extras else None)
        if pick is not None:
            olist.highlighted = ids.index(pick)

    def _gp_vm_choice(self) -> str | None:
        olist = self.query_one("#gp_vm_list", OptionList)  # type: ignore[attr-defined]
        idx = olist.highlighted
        if idx is None or idx >= olist.option_count:
            return None
        return olist.get_option_at_index(idx).id

    def _gp_validate(self) -> None:
        s = self._gp_session()
        if s.state["step"] <= 2:
            card = self._gp_selected_card()
            nvidia = card is not None and card.vendor == "nvidia"
            if card is not None and self._gp_vm_list_vendor != card.vendor:
                self._gp_fill_vm_list(self._gp_vm_pool[card.vendor], extras=True)
                self._gp_vm_list_vendor = card.vendor
            first = self.query_one("#gp_display_0", RadioButton)  # type: ignore[attr-defined]
            second = self.query_one("#gp_display_1", RadioButton)  # type: ignore[attr-defined]
            # NVIDIA works with the console display only (the notice says why); the first choice is greyed out.
            first.disabled = nvidia
            self.query_one("#gp_display_desc_0", Static).update(display_description(  # type: ignore[attr-defined]
                "Not available for NVIDIA cards. The Proxmox console goes black once the NullMoth driver loads, so "
                "turn on Remote Login and Screen Sharing in macOS first and connect a monitor or dummy plug to the card."
                if nvidia else DISPLAY_OPTIONS[0][2]))
            if nvidia:
                second.value = True
            shown = ["gp_gpu_select", "gp_vm_label", "gp_vm_list", "gp_display_label", "gp_display_radio"]
            if nvidia:
                shown += ["gp_notice_title", "gp_notice", "gp_ack_label", "gp_ack_in"]
            self._gp_show(*shown)
            acked = self.query_one("#gp_ack_in", Input).value.strip() == ACK_PHRASE  # type: ignore[attr-defined]
            choice = self._gp_vm_choice()
            running = self._gp_running_choice(choice)
            stop = self.query_one("#gp_stop_btn", Button)  # type: ignore[attr-defined]
            stop.set_class(running is None, "hidden")
            if running is not None:
                stop.label = f"Stop VM {running.vmid}"
            self.query_one("#gp_primary_btn", Button).disabled = (  # type: ignore[attr-defined]
                card is None or (nvidia and not acked) or running is not None)
        elif s.state["step"] == 4:
            typed = self.query_one("#gp_confirm_in", Input).value.strip() == CONFIRM_PHRASE  # type: ignore[attr-defined]
            plan = self._gp_plan
            if plan is not None and self._gp_needs_typed(plan):
                self.query_one("#gp_primary_btn", Button).disabled = not typed  # type: ignore[attr-defined]
        elif s.state["step"] == 7:
            running = self._gp_running_choice(self._gp_vm_choice())
            stop = self.query_one("#gp_stop_btn", Button)  # type: ignore[attr-defined]
            stop.set_class(running is None, "hidden")
            if running is not None:
                stop.label = f"Stop VM {running.vmid}"

    _gp_vm_list_vendor = ""
    _gp_vm_pool: dict = {"amd": [], "nvidia": []}

    def _gp_running_choice(self, choice: str | None) -> VmOption | None:
        """The highlighted VM when it is running (it must be stopped before it can take a card)."""
        return next((o for o in self._gp_vm_opts if choice == str(o.vmid) and o.status == "running"), None)

    def _gp_stop_selected(self) -> None:
        s = self._gp_session()
        opt = self._gp_running_choice(self._gp_vm_choice())
        if opt is None:
            return
        if self._gp_armed != "stop":
            self._gp_armed = "stop"
            self.query_one("#gp_stop_btn", Button).label = f"Yes, stop VM {opt.vmid} now"  # type: ignore[attr-defined]
            self._gp_result(f"VM {opt.vmid} ({opt.name}) is running. Press the button again to stop it. "
                            "Whatever is open in the guest is closed without saving.")
            return
        self._gp_armed = None

        def done(result: Any) -> None:
            ok, out = result
            if not ok:
                self._gp_result(f"Could not stop VM {opt.vmid}: {out}", fail=True)
                return
            vendor = self._gp_selected_card().vendor if s.state["step"] <= 2 else s.state["gpu"]["vendor"]
            self._gp_vm_pool = {"amd": s.vm_options("amd"), "nvidia": s.vm_options("nvidia")}
            self._gp_fill_vm_list(self._gp_vm_pool[vendor], extras=s.state["step"] <= 2)
            self._gp_result(f"VM {opt.vmid} is stopped.")
            self._gp_validate()

        self._gp_async_quiet(lambda: s.stop_vm(opt.vmid), done)

    def _gp_async_quiet(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        """Run *work* off the UI thread without blanking the screen."""
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                log.exception("GPU setup step failed")
                self.call_from_thread(self._gp_failed, str(exc))  # type: ignore[attr-defined]
                return
            self.call_from_thread(done, result)  # type: ignore[attr-defined]

        Thread(target=runner, daemon=True).start()

    def _gp_choose(self) -> None:
        s = self._gp_session()
        card = self._gp_selected_card()
        if card is None:
            return
        choice = self._gp_vm_choice()
        radio = self.query_one("#gp_display_radio", RadioSet)  # type: ignore[attr-defined]
        pressed = radio.pressed_button
        display = DISPLAY_OPTIONS[int(pressed.id.rsplit("_", 1)[1]) if pressed is not None and pressed.id else 0][0]
        ack = self.query_one("#gp_ack_in", Input).value.strip() == ACK_PHRASE  # type: ignore[attr-defined]
        vmid = int(choice) if choice and choice.isdigit() else None
        if card.stopped_users and not self._gp_confirm_click(
                self._gp_share_key(card), "Choose this card",
                " ".join(card.notes) + " Press the button again to use it anyway."):
            return

        def done(issues: list[str]) -> None:
            if issues:
                self._gp_result("\n".join("Refused: " + i for i in issues), fail=True)
                self._gp_validate()
                return
            self._gp_reset_confirmations()
            if choice == _CREATE:
                self.notify("Your card choice is saved. Create the macOS VM from the Create VM tab, then come back "  # type: ignore[attr-defined]
                            "here for the VM step.")
            self._gp_render()

        self._gp_async(lambda: s.choose(card.bdf, vmid, display, ack, bool(card.stopped_users), confirmed=card.stopped_users), done, "Checking the choices...")

    # ── 3 BIOS ───────────────────────────────────────────────────────

    def _gp_bios(self) -> None:
        s = self._gp_session()
        self._gp_show()
        self._gp_set("Step 3: BIOS", "Checking the BIOS settings...", "Re-check", primary_disabled=True)

        def work() -> Any:
            items = s.bios_items()
            return items, s.bios_card(items), s.running_vms(), s.checked_fields(items)

        self._gp_async(work, self._gp_bios_done)

    def _gp_bios_done(self, result: Any) -> None:
        s = self._gp_session()
        items, card, running, checked = result
        if card is None:
            s.complete(3, skipped=True)
            self._gp_render()
            return
        self._gp_card = card
        text = Text("\n".join(pack_fields(checked, self._gp_width())) + "\n\n")
        body = Text(card["text"])
        for line_idx, start, end in qr_spans(card["text"], card["qr"]):
            offset = sum(len(ln) + 1 for ln in card["text"].split("\n")[:line_idx])
            body.stylize("black on white", offset + start, offset + end)
        text.append_text(body)
        text.append(f"\n\nSaved to {card['path']}")
        if running:
            text.append("\n\nRunning VMs that a reboot will stop: " + ", ".join(f"{i} ({n})" for i, n in running))
        if card["firmware_reboot"]:
            self._gp_set("Step 3: BIOS (take a photo of this screen)", text, "Reboot into BIOS now",
                         "Re-check", back=True, tertiary="I'll do it later")
        else:
            self._gp_set("Step 3: BIOS (take a photo of this screen)", text, "Re-check", "I'll do it later", back=True)
        self._gp_show()

    # ── 4 host ───────────────────────────────────────────────────────

    def _gp_host(self, allow_no_console: bool = False, write_optional: bool = False) -> None:
        s = self._gp_session()
        self._gp_wo, self._gp_anc = write_optional, allow_no_console
        self._gp_show()
        self._gp_reset_confirmations()  # a new plan never inherits an earlier "I understand"
        self._gp_set("Step 4: host configuration", "Working out what this host needs...", "Apply", primary_disabled=True)
        self._gp_async(lambda: s.host_plan(allow_no_console=allow_no_console, write_optional=write_optional),
                      self._gp_host_done)

    @staticmethod
    def _gp_needs_typed(plan: dict) -> bool:
        return bool(plan["hard_warnings"]) or any("only display adapter" in r for r in plan["refusals"])

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
                         "tool to change. Fix the failing item in the verify list (see the docs), then check again.",
                         "Check again", back=True)
            return
        if plan.get("optional") and not s.plan_needs_work(plan) and not plan["refusals"]:
            self._gp_set("Step 4: host configuration", "\n".join(lines), "Continue", "Write it anyway", back=True)
            return
        if not s.plan_needs_work(plan) and not plan["refusals"]:
            s.complete(4, skipped=True)
            if 5 not in s.state["done"]:
                s.complete(5, skipped=True)
            self._gp_render()
            return
        if plan["changes"] or plan["commands"]:
            lines += ["", "This is exactly what will change (each file is backed up first):", host_setup.render_preview(plan)]
        need_typed = self._gp_needs_typed(plan)
        self._gp_set("Step 4: host configuration", "\n".join(lines), "Apply these changes", back=True,
                     primary_disabled=bool(plan["refusals"]) and not need_typed or need_typed)
        self._gp_show("gp_confirm_label", "gp_confirm_in") if need_typed else self._gp_show()

    def _gp_apply_host(self) -> None:
        s = self._gp_session()
        plan = self._gp_plan
        if plan is None:
            return
        typed = self.query_one("#gp_confirm_in", Input).value.strip() == CONFIRM_PHRASE  # type: ignore[attr-defined]
        if self._gp_needs_typed(plan) and not typed:
            return
        if plan.get("optional") and not s.plan_needs_work(plan) and not plan["refusals"]:
            s.complete(4, skipped=True)  # the optional write is skipped by default
            if 5 not in s.state["done"]:
                s.complete(5, skipped=True)
            self._gp_render()
            return
        if plan["refusals"]:
            self._gp_host(allow_no_console=True, write_optional=getattr(self, "_gp_wo", False))  # re-plan once with the typed confirmation; it is not kept
            return
        self._gp_async(lambda: s.host_apply(plan, typed), self._gp_apply_done, "Applying...")

    def _gp_apply_done(self, res: dict) -> None:
        if not res["ok"]:
            self._gp_result(f"Failed: {res['error']}" + (" The files were put back." if res["rolled_back"] else ""), fail=True)
            self.query_one("#gp_primary_btn", Button).disabled = False  # type: ignore[attr-defined]
            return
        self._gp_reset_confirmations()
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
        self._gp_running = running
        body = ("The host must reboot so the new kernel arguments and the early card binding take effect.\n"
                + ("Running VMs that will stop: " + ", ".join(f"{i} ({n})" for i, n in running) + "\n" if running else "")
                + f"\nIf you reboot later, run: {s.state['resume_cmd']}")
        self._gp_set("Step 5: reboot", body, "Reboot now", "Later")

    _gp_running: list = []

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
                         "proxmox-boot-tool refresh), then check again.", "Check again")
            return
        if any("Reboot the host" in c["detail"] for c in fails):
            s.mark_reboot_needed()
            self._gp_render()
            self._gp_result("The change is saved but not active yet. Reboot first.")
            return
        self._gp_fix = fails[0]["fix"]
        if self._gp_fix == "scan":
            self._gp_card_changed(CardChanged(fails[0]["detail"]))
            return
        label = {"bios": "Back to the BIOS step", "host": "Back to the host step"}.get(self._gp_fix, "Check again")
        self._gp_set("Step 6: verify", body, label)

    # ── 7 VM, 8 first boot ───────────────────────────────────────────

    def _gp_vm(self) -> None:
        s = self._gp_session()
        self._gp_show("gp_vm_label", "gp_vm_list", "gp_stop_cb")
        vendor = s.state["gpu"]["vendor"]
        self._gp_set("Step 7: attach the card to the VM",
                     "Pick the macOS VM that gets the card. It must have finished its install.", "Preview", back=True,
                     primary_disabled=True)

        def done(options: list[VmOption]) -> None:
            self._gp_fill_vm_list(options, extras=False)
            if s.state["vm"] is not None:
                ids = [str(o.vmid) for o in options]
                if str(s.state["vm"]) in ids:
                    self.query_one("#gp_vm_list", OptionList).highlighted = ids.index(str(s.state["vm"]))  # type: ignore[attr-defined]
            self.query_one("#gp_primary_btn", Button).disabled = not options  # type: ignore[attr-defined]
            self._gp_validate()

        self._gp_async_quiet(lambda: s.vm_options(vendor), done)

    def _gp_vm_preview(self) -> None:
        s = self._gp_session()
        choice = self._gp_vm_choice()
        if choice is None or not choice.isdigit():
            self._gp_result("Pick a VM first.", fail=True)
            return
        s.state["vm"] = int(choice)
        stop = self.query_one("#gp_stop_cb", Checkbox).value  # type: ignore[attr-defined]

        def done(plan: Any) -> None:
            self._gp_vm_plan = plan
            lines = ["Refused: " + i for i in plan.issues] + plan.hints + ["Warning: " + w for w in plan.warnings]
            if plan.ok:
                lines += ["", f"This is what will change on VM {s.state['vm']} (a config snapshot is saved first):"]
                lines += [f"  {i:02d}. {st.title}" for i, st in enumerate(plan.steps, start=1)]
                if s.state["gpu"]["vendor"] == "nvidia":
                    lines += ["", nullmoth.GUEST_PREP_TITLE + ":"]
                    lines += [f"  {i}. {t}" for i, t in enumerate(nullmoth.GUEST_PREP, start=1)]
            self._gp_set("Step 7: attach the card to the VM", "\n".join(lines), "Attach the card now" if plan.ok else "Preview",
                         back=True)
            self._gp_show("gp_vm_label", "gp_vm_list", "gp_stop_cb")

        self._gp_async(lambda: s.vm_plan(allow_stop=stop), done, "Checking the VM and the card...")

    def _gp_vm_apply(self) -> None:
        s = self._gp_session()
        plan = self._gp_vm_plan
        if plan is None or not plan.ok:
            self._gp_vm_preview()
            return
        if s.state["gpu"]["vendor"] == "nvidia" and not self._gp_confirm_click(
                self._gp_guest_key(s), "Attach the card now", nullmoth.GUEST_PREP_CONFIRM + " Press the button again to attach."):
            return

        def work() -> Any:
            from pathlib import Path
            from .defaults import DEFAULT_ISO_DIR
            from .executor import apply_plan
            from .oc_builder import ensure_amfipass
            from .rollback import create_snapshot
            s.begin_attach()  # the card must still be the chosen one: raises CardChanged before anything is written
            if s.state["gpu"]["vendor"] == "nvidia":
                ensure_amfipass(Path(DEFAULT_ISO_DIR))
            create_snapshot(s.state["vm"])
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
        nvidia = s.state["gpu"]["vendor"] == "nvidia"
        self._gp_show()
        qr = s.first_boot_qr()
        body = Text(s.first_boot_text() + ("" if nvidia else "\n\nDo you see the macOS desktop on your monitor?"))
        if qr:
            body.append("\n\n")
            body.append("\n".join(qr), style="black on white")
        if nvidia:
            self._gp_set("Step 8: first boot", body, "Done")
        else:
            self._gp_set("Step 8: first boot", body, "Yes, I see the desktop", "No, help me")

    def _gp_finish(self) -> None:
        s = self._gp_session()
        s.finish()
        self._gp_show()
        self._gp_set("Setup finished", s.summary(), "Start a new setup")

    # ── buttons ──────────────────────────────────────────────────────

    @staticmethod
    def _gp_share_key(card) -> str:
        """The two-click key of the shared-card warning: bound to this card and this set of holders."""
        return f"share:{card.bdf}:{sorted(card.stopped_users)}"

    @staticmethod
    def _gp_guest_key(session) -> str:
        """The two-click key of the guest-preparation question: bound to this VM and this card."""
        return f"guest:{session.state['vm']}:{session.state['gpu']['bdf']}"

    def _gp_confirm_click(self, key: str, label: str, warning: str) -> bool:
        """Two-click confirmation for actions with side effects. True on the second click only; any
        redraw of the screen disarms it."""
        if self._gp_armed == key:
            self._gp_armed = None
            return True
        self._gp_armed = key
        self._gp_result(warning)
        return False

    def _gp_reboot_warning(self, what: str, running: list) -> str:
        names = ", ".join(f"{i} ({n})" for i, n in running)
        return (f"{what} Press the button again to confirm."
                + (f" These VMs will be shut down: {names}." if running else " No VM is running."))

    def _gp_primary(self) -> None:
        s = self._gp_session()
        step = s.state["step"]
        if s.state["finished"]:
            s.restart()
            self._gp_open()
        elif step <= 2:
            self._gp_choose()
        elif step == 3:
            card = getattr(self, "_gp_card", None)
            if card and card["firmware_reboot"]:
                self._gp_reboot_bios()
            else:
                self._gp_bios()
        elif step == 4:
            self._gp_apply_host()
        elif step == 5:
            self._gp_reboot_now()
        elif step == 6:
            target = {"bios": 3, "host": 4}.get(self._gp_fix)
            if target:
                s.send_back(target)
            self._gp_render()
        elif step == 7:
            self._gp_vm_apply() if self._gp_vm_plan is not None else self._gp_vm_preview()
        else:
            self._gp_finish()

    def _gp_reboot_bios(self) -> None:
        s = self._gp_session()
        running = s.running_vms()
        if not self._gp_confirm_click("reboot_bios", "Reboot into BIOS now", self._gp_reboot_warning(
                "The host reboots into the BIOS setup.", running)):
            self.query_one("#gp_primary_btn", Button).label = "Yes, reboot into the BIOS"  # type: ignore[attr-defined]
            return
        ok, out = s.reboot_to_bios()
        self._gp_result("Rebooting into the BIOS..." if ok else "Could not start the reboot: " + out, fail=not ok)

    def _gp_reboot_now(self) -> None:
        s = self._gp_session()
        if not self._gp_confirm_click("reboot", "Reboot now", self._gp_reboot_warning(
                "The host reboots now.", s.running_vms())):
            self.query_one("#gp_primary_btn", Button).label = "Yes, reboot now"  # type: ignore[attr-defined]
            return
        ok, out = s.reboot_host()
        self._gp_result("Rebooting..." if ok else "Could not start the reboot: " + out, fail=not ok)

    def _gp_secondary(self) -> None:
        s = self._gp_session()
        step = s.state["step"]
        if step == 3:
            card = getattr(self, "_gp_card", None)
            if card and card["firmware_reboot"]:
                self._gp_bios()  # "Re-check" is secondary when the reboot button is the primary action
            else:
                self._gp_result("When you are back in Proxmox, run: " + s.pause())
        elif step == 4:
            self._gp_host(allow_no_console=getattr(self, "_gp_anc", False), write_optional=True)
        elif step == 5:
            self._gp_result("Reboot when it suits you, then run: " + s.pause())
        elif step == 8:
            self._gp_set("Step 8: first boot - troubleshooting", "\n\n".join(TROUBLESHOOTING.values()),
                         "Yes, I see the desktop", "Back")

    def _gp_tertiary(self) -> None:
        s = self._gp_session()
        if s.state["step"] == 3:
            self._gp_result("When you are back in Proxmox, run: " + s.pause())

    def _gp_back(self) -> None:
        s = self._gp_session()
        target = max(1, s.state["step"] - 1)
        while target > 1 and target in s.state["skipped"]:
            target -= 1
        s.goto(target)
        self._gp_render()

    def _gp_undo(self) -> None:
        s = self._gp_session()
        if not self._gp_confirm_click("undo", "Undo everything", (
                "This puts back every file and VM setting this setup changed (anything you edited since is kept "
                "and reported). Press the button again to undo.")):
            self.query_one("#gp_undo_btn", Button).label = "Yes, undo everything"  # type: ignore[attr-defined]
            return
        self.query_one("#gp_undo_btn", Button).label = "Undo everything"  # type: ignore[attr-defined]

        def done(result: Any) -> None:
            ok, messages = result
            self._gp_result("\n".join(messages) or "Nothing to undo.", fail=not ok)
            if ok:
                self._gp = None
                self._gp_open()

        self._gp_async(lambda: s.undo(allow_stop=self.query_one("#gp_stop_cb", Checkbox).value), done, "Undoing...")  # type: ignore[attr-defined]
