from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Select, Static

from ..nullmoth import ACK_LABEL, NOTICE_BODY, NOTICE_TITLE

__all__ = ["compose_gpu_panel"]


def compose_gpu_panel() -> ComposeResult:
    """Guided GPU passthrough walkthrough (the TUI twin of `osx-next-cli gpu-setup`)."""
    with Vertical(id="gpu_panel", classes="hidden"):
        yield Static("", id="gp_rail", markup=False)
        yield Static("GPU Passthrough (guided)", id="gp_title")
        yield Static("", id="gp_body", markup=False)
        yield Static("", id="gp_qr", classes="hidden")
        yield Select([], prompt="Select a GPU", id="gp_gpu_select", classes="hidden")
        yield Static("VM ID to attach the card to (leave empty to set up the host first):", id="gp_vm_label",
                     classes="hidden")
        yield Input(value="", id="gp_vmid", placeholder="e.g. 106", classes="hidden")
        yield Select(
            [("Monitor on the GPU (best performance, the Proxmox console goes blank)", "gpu"),
             ("Keep the Proxmox console (the card only accelerates)", "console")],
            allow_blank=False, id="gp_display_select", classes="hidden",
        )
        yield Static(NOTICE_TITLE, id="gp_notice_title", classes="hidden")
        yield Static(NOTICE_BODY, id="gp_notice", classes="hidden hint")
        yield Checkbox(ACK_LABEL, value=False, id="gp_ack_cb", classes="hidden")
        yield Checkbox("I understand", value=False, id="gp_confirm_cb", classes="hidden")
        yield Checkbox("Stop the VM first if it is running", value=False, id="gp_stop_cb", classes="hidden")
        with Horizontal(classes="action_row"):
            yield Button("Scan", id="gp_primary_btn")
            yield Button("", id="gp_secondary_btn", classes="hidden")
            yield Button("Back", id="gp_back_btn", classes="hidden")
            yield Button("Undo everything", id="gp_undo_btn", classes="hidden")
        yield Static("", id="gp_result", classes="hidden", markup=False)
