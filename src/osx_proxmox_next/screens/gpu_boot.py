"""Moment 3 of the graphics card flow: does the Mac show its desktop on the card?"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Static

from ..gpu_flow import short_card_name
from ..gpu_setup import TROUBLESHOOTING

LOOK_AT_MONITOR = "Look at the monitor plugged into the card"
FIRST_START = "The first start takes up to 2 minutes."
ON_THE_CARD = "This Mac's screen now lives on the card, not in Proxmox."
ON_CONSOLE = "The card does the acceleration. Keep using the Proxmox console."
QUESTION = "Do you see the macOS desktop?"


class BootMixin:
    """The first boot and the finished summary of GpuScreen."""

    def _boot_title(self, nvidia: bool, troubleshooting: bool) -> str:
        if troubleshooting:
            return "No signal? Try these"
        if nvidia:
            return "Your Mac is ready"
        return LOOK_AT_MONITOR if self.s.state["display"] == "gpu" or self.s.state["gpu"]["vendor"] == "intel" \
            else "Open the Mac in the Proxmox console"

    def _boot_body(self, troubleshooting: bool) -> Text:
        s = self.s
        chosen = s.state["gpu"]
        if troubleshooting:
            return Text("\n\n".join(TROUBLESHOOTING.values()))
        if chosen["vendor"] == "amd":
            body = Text(FIRST_START + "\n" + (ON_THE_CARD if s.state["display"] == "gpu" else ON_CONSOLE))
        else:
            body = Text(s.first_boot_text())
        qr = s.first_boot_qr()
        if qr:
            body.append("\n\n")
            body.append("\n".join(qr), style="black on white")
        return body

    async def _show_first_boot(self, troubleshooting: bool = False) -> None:
        s = self.s
        pal = self.app.get_css_variables()
        chosen = s.state["gpu"]
        nvidia = chosen["vendor"] == "nvidia"
        vmid = s.state["vm"]
        name = self._vm_name(vmid) if vmid is not None else "the Mac"
        body = self._boot_body(troubleshooting)
        extra = self.result_text if self.result_text and not self.result_fail else ""
        if extra:
            body.append("\n\n" + extra)
        if not (nvidia or troubleshooting):
            body.append("\n\n" + QUESTION)
        if nvidia:
            buttons = [Button("Done", id="g-done", classes="primary")]
        elif troubleshooting:
            buttons = [Button("Back", id="g-back", classes="secondary"),
                       Button("Yes, all good", id="g-yes", classes="primary")]
        else:
            buttons = [Button("No, help me", id="g-no", classes="secondary"),
                       Button("Yes, all good", id="g-yes", classes="primary")]
        widgets: list[Widget] = [
            Static(Text(f"{name} is starting on the {short_card_name(chosen['name'])}", style="bold"),
                   id="g-run-title"),
            Horizontal(Static(self.timeline(True), id="g-timeline"),
                       Vertical(Static(Text(self._boot_title(nvidia, troubleshooting), style="bold"), id="g-box-title"),
                                VerticalScroll(Static(body, id="g-box-text"), id="g-box-scroll"),
                                Static("", id="g-result"), Horizontal(*buttons, id="g-box-buttons"), id="g-box"),
                       id="g-run-cols"),
            Static(Text("Not happy with it? u puts the host and the Mac back exactly as they were.",
                        style=pal["muted"]), id="g-run-note"),
        ]
        self.box_buttons = [("g-yes", "Done" if nvidia else "Yes, all good", "primary")]
        await self.set_mode("first_boot", widgets)
        again = lambda: self.run_worker(self._show_first_boot(), exclusive=True, group="gpu-mode")  # noqa: E731
        self.set_actions(self.action_yes, again if troubleshooting else self.action_no)
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
        widgets: list[Widget] = [
            Static(Text("Setup finished", style="bold"), id="g-run-title"),
            VerticalScroll(Static(Text(self.s.summary()), id="g-box-text"), id="g-box-scroll"),
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
