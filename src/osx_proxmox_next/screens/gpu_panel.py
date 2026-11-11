from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, OptionList, RadioButton, RadioSet, Select, Static

from ..gpu_setup import DISPLAY_OPTIONS, NVIDIA_DISPLAY_NOTE
from ..nullmoth import ACK_LABEL, ACK_PHRASE, NOTICE_BODY, NOTICE_TITLE

__all__ = ["compose_gpu_panel"]

CONFIRM_PHRASE = "I understand"


class GpuButton(Button):
    """A button whose width follows its label: Textual caches the width, so a new label needs a new layout."""

    def watch_label(self, *_args: object) -> None:
        if self.is_attached:
            self.refresh(layout=True)


def display_description(description: str) -> str:
    """The one-line description shown under a display choice, so nothing is cut off."""
    return "      " + description


def compose_gpu_panel() -> ComposeResult:
    """Guided GPU passthrough walkthrough (the TUI twin of `osx-next-cli gpu-setup`)."""
    with Vertical(id="gpu_panel", classes="hidden"):
        with Horizontal(id="gp_header"):
            yield Static("GPU Passthrough", id="gp_name")
            yield Static("Beta", id="gp_tag")
        yield Static("", id="gp_rail", markup=False)
        yield Static("", id="gp_title")
        yield Static("", id="gp_body", markup=False)
        yield Select([], prompt="Select a GPU", id="gp_gpu_select", classes="hidden")
        yield Static("Which macOS VM gets the card?", id="gp_vm_label", classes="hidden")
        yield OptionList(id="gp_vm_list", classes="hidden")
        yield Static("Where do you want to see macOS?", id="gp_display_label", classes="hidden")
        with RadioSet(id="gp_display_radio", classes="hidden"):
            for idx, (_value, label, desc) in enumerate(DISPLAY_OPTIONS):
                yield RadioButton(label, value=idx == 0, id=f"gp_display_{idx}")
                yield Static(display_description(desc), id=f"gp_display_desc_{idx}", classes="gp_desc")
        yield Static(NVIDIA_DISPLAY_NOTE, id="gp_display_note", classes="hidden hint")
        yield Static(NOTICE_TITLE, id="gp_notice_title", classes="hidden")
        yield Static(NOTICE_BODY, id="gp_notice", classes="hidden hint")
        yield Static(f"{ACK_LABEL} Type '{ACK_PHRASE}' to confirm:", id="gp_ack_label", classes="hidden",
                     markup=False)
        yield Input(value="", id="gp_ack_in", placeholder="type the phrase here", classes="hidden")
        yield Static(f"Type '{CONFIRM_PHRASE}' to continue:", id="gp_confirm_label", classes="hidden",
                     markup=False)
        yield Input(value="", id="gp_confirm_in", placeholder="type the phrase here", classes="hidden")
        yield Select([("Latest", "latest")], prompt="NullMoth release (latest is preselected)", id="gp_nm_select", classes="hidden",
                     allow_blank=False)
        yield Checkbox("Stop the VM first if it is running", value=False, id="gp_stop_cb", classes="hidden")
        yield Static("", id="gp_result", classes="hidden", markup=False)
        with Horizontal(classes="action_row"):
            yield GpuButton("Scan", id="gp_primary_btn")
            yield GpuButton("", id="gp_secondary_btn", classes="hidden gp_secondary")
            yield GpuButton("", id="gp_tertiary_btn", classes="hidden gp_secondary")
            yield GpuButton("Back", id="gp_back_btn", classes="hidden gp_secondary")
            yield GpuButton("Stop VM", id="gp_stop_btn", classes="hidden gp_secondary")
            yield GpuButton("Undo everything", id="gp_undo_btn", classes="hidden gp_secondary")
