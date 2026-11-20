"""Dialogs of the Home and VM screens: remove, edit and the stop choice."""

from __future__ import annotations

import logging
import tempfile
import traceback
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static

from ..domain import MAX_VMID, MIN_VMID, EditChanges, PlanStep, validate_edit_changes
from ..executor import StepResult
from ..rollback import create_snapshot, rollback_hints
from ..services import (
    VmFacts,
    VmRecord,
    fetch_vm_info,
    get_proxmox_adapter,
    run_destroy_worker,
    run_edit_worker,
)
from ..widgets.checkbox import Check
from ..widgets.chrome import KeyBar
from ..widgets.rows import ChoiceRow, FieldRow, Option
from .base import Choice, ChoiceDialog

log = logging.getLogger(__name__)

__all__ = ["RemoveDialog", "EditDialog", "StopDialog", "changes_from_form", "NIC_MODELS"]

NIC_MODELS = ("vmxnet3", "e1000-82545em")
FIELD_WIDTH = 20             # every field of the edit dialog is this wide, in one column


def StopDialog(record: VmRecord) -> ChoiceDialog:
    """What to do with a running VM: ask it to shut down, or cut the power."""
    return ChoiceDialog(
        f"Stop {record.name} (VM {record.vmid})?",
        ["Shut down asks macOS to close everything and power off.",
         "Force stop cuts the power at once; whatever is open is lost."],
        [Choice("cancel", "Cancel"), Choice("shutdown", "Shut down", "primary"),
         Choice("stop", "Force stop", "danger")],
        focus="shutdown",
    )


def _disk_summary(facts: VmFacts) -> str:
    sized = [d for d in facts.disks if d.size_gb > 1 and d.storage != "local"]
    if not sized:
        return ""
    total = sum(d.size_gb for d in sized)
    return f"{total} GB on {sized[0].storage}"


class RemoveDialog(ModalScreen[bool]):
    """The Remove confirmation: type the VM ID, choose whether the disks go too."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, record: VmRecord) -> None:
        super().__init__()
        self.record = record
        self.running = False
        self.finished = False

    def compose(self) -> ComposeResult:
        rec = self.record
        disk = _disk_summary(rec.facts)
        with Vertical(id="remove-box", classes="dialog danger"):
            yield Static(f"Remove VM {rec.vmid} · {rec.name}?", classes="dialog-title")
            yield Static("", classes="blank")
            yield Static("This stops the VM and deletes its configuration.", classes="dialog-line")
            yield Static("It cannot be undone.", classes="dialog-line error")
            yield Static("", classes="blank")
            label = "Also delete its disk images" + (f" ({disk})" if disk else "")
            yield Check(label, value=True, id="purge")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-field"):
                yield Static("Type the VM ID to confirm ", classes="dialog-line")
                yield Input(id="confirm-id", max_length=8)
            yield Static("", id="remove-log", classes="dialog-line")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel", classes="secondary")
                yield Button("Remove VM", id="remove", classes="danger", disabled=True)
        yield KeyBar([("tab", "Next field", None), ("enter", "Confirm", None), ("esc", "Cancel", "screen.cancel")],
                     id="keybar")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#remove", Button).disabled = self.running or event.value.strip() != str(self.record.vmid)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self.query_one("#remove", Button).disabled:
            self.action_remove()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "remove":
            self.action_remove()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        if not self.running:
            self.dismiss(self.finished)

    def action_remove(self) -> None:
        vmid = self.record.vmid
        if self.running or self.finished or not (MIN_VMID <= vmid <= MAX_VMID):
            return
        if self.query_one("#confirm-id", Input).value.strip() != str(vmid):
            return
        self.running = True
        purge = self.query_one("#purge", Checkbox).value
        self.query_one("#remove", Button).disabled = True
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#remove-log", Static).update("Removing VM...")
        self.run_worker(lambda: self._work(vmid, purge), thread=True, exit_on_error=False)

    def _work(self, vmid: int, purge: bool) -> None:
        def on_step(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
            word = "Running" if result is None else ("OK" if result.ok else "FAIL")
            self.app.call_from_thread(self._say, f"{word} {idx}/{total}: {step.title}")

        try:
            if fetch_vm_info(vmid, adapter=get_proxmox_adapter()) is None:
                self.app.call_from_thread(self._done, False, f"VM {vmid} was not found.", [])
                return
            result, snapshot = run_destroy_worker(vmid, purge=purge, on_step=on_step)
            hints = rollback_hints(snapshot) if (snapshot and not result.ok) else []
            self.app.call_from_thread(self._done, result.ok, f"Log: {result.log_path}", hints)
        except Exception as exc:  # noqa: BLE001
            log.exception("destroy worker failed: %s", exc)
            self.app.call_from_thread(self._done, False, _error_log(), [])

    def _say(self, line: str) -> None:
        self.query_one("#remove-log", Static).update(line)

    def _done(self, ok: bool, detail: str, hints: list[str]) -> None:
        self.running = False
        self.finished = ok
        cancel = self.query_one("#cancel", Button)
        cancel.disabled = False
        cancel.label = "Close"
        cancel.focus()
        # a removed VM cannot be removed again; after a failure the typed ID still decides
        self.query_one("#remove", Button).disabled = ok or self.query_one("#confirm-id", Input).value.strip() != str(
            self.record.vmid)
        head = f"VM {self.record.vmid} was removed." if ok else "Removing the VM failed."
        self.query_one("#remove-log", Static).update(Text("\n".join([head, detail, *hints])))
        if ok:
            self.app.notify(f"VM {self.record.vmid} removed")


def _error_log() -> str:
    try:
        fd, path = tempfile.mkstemp(prefix="osx-next-error_", suffix=".log")
        with open(fd, "w") as handle:
            traceback.print_exc(file=handle)
        return f"Log: {path}"
    except OSError:  # pragma: no cover
        return f"Log: {Path(tempfile.gettempdir()) / 'osx-next-error.log'}"


def changes_from_form(
    orig: VmFacts,
    name: str,
    cores: str,
    memory_gb: str,
    bridge: str,
    nic_model: str,
    add_disk_gb: str,
    disk_slot: str,
) -> tuple[EditChanges | None, list[str]]:
    """(changes, problems): only fields that differ from the VM's current values count.

    A NIC model change alone is a change too: the plan rewrites net0, so the bridge rides along."""
    problems: list[str] = []
    changes = EditChanges()
    changed = False

    def whole(text: str, what: str) -> int | None:
        try:
            return int(text)
        except ValueError:
            problems.append(f"{what} must be a whole number.")
            return None

    name = name.strip()
    if name and name != orig.name:
        changes.name, changed = name, True
    if cores.strip() and cores.strip() != str(orig.cores):
        value = whole(cores.strip(), "Cores")
        if value is not None:
            changes.cores, changed = value, True
    if memory_gb.strip() and memory_gb.strip() != str(round(orig.memory_mb / 1024)):
        value = whole(memory_gb.strip(), "Memory (GB)")
        if value is not None:
            changes.memory_mb, changed = value * 1024, True
    bridge = bridge.strip()
    new_bridge = bridge and bridge != orig.bridge
    new_nic = bool(nic_model) and nic_model != orig.nic_model
    if new_bridge or new_nic:
        changes.bridge, changed = bridge or orig.bridge, True
        changes.nic_model = nic_model or None
    if add_disk_gb.strip():
        value = whole(add_disk_gb.strip(), "Added disk space")
        if value is not None:
            changes.disk_gb_add, changed = value, True
            changes.disk_name = disk_slot.strip() or "virtio0"
    if not changed or problems:
        return None, problems
    return changes, problems


class EditDialog(ModalScreen[bool]):
    """Edit a VM, prefilled with its current values. Only what changed is applied."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "field(-1)", "Previous field", show=False),
        Binding("down", "field(1)", "Next field", show=False),
    ]

    def __init__(self, record: VmRecord) -> None:
        super().__init__()
        self.record = record
        self.facts = record.facts
        self.running = False
        self.finished = False

    def _main_disk(self):
        """The main disk is the big one: the OpenCore and recovery disks are small. The edit grows it in place."""
        return max(self.facts.disks, key=lambda d: d.size_gb) if self.facts.disks else None

    def _disk_suffix(self) -> str:
        disk = self._main_disk()
        return "GB more on " + (f"{disk.slot} \u00b7 {disk.storage}" if disk else "virtio0")

    def compose(self) -> ComposeResult:
        f, rec = self.facts, self.record
        nics = list(NIC_MODELS)
        if f.nic_model and f.nic_model not in nics:
            nics.insert(0, f.nic_model)
        with Vertical(id="edit-box", classes="dialog"):
            yield Static(f"Edit VM {rec.vmid} · {rec.name}", classes="dialog-title")
            yield Static("", classes="blank")
            yield FieldRow("Name", f.name or rec.name, id="e-name", max_width=FIELD_WIDTH)
            yield FieldRow("Cores", str(f.cores or rec.cores), id="e-cores", max_width=FIELD_WIDTH)
            yield FieldRow("Memory", str(round((f.memory_mb or rec.memory_mb) / 1024)), id="e-memory",
                           suffix="GB", max_width=FIELD_WIDTH)
            yield FieldRow("Bridge", f.bridge or "vmbr0", id="e-bridge", max_width=FIELD_WIDTH)
            yield ChoiceRow("NIC model", [Option(n, n) for n in nics], f.nic_model or nics[0], hint_lines=0,
                            id="e-nic")
            yield FieldRow("Grow disk", "", id="e-disk", suffix=self._disk_suffix(), max_width=FIELD_WIDTH)
            yield Check("Start it again afterwards", value=rec.status == "running", id="e-start")
            yield Static("", id="e-note", classes="dialog-line")
            yield Static("", classes="blank")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel", classes="secondary")
                yield Button("Apply changes", id="apply", classes="primary", disabled=True)
        yield KeyBar([("tab", "Next field", None), ("enter", "Apply", None), ("esc", "Cancel", "screen.cancel")],
                     id="keybar")

    def on_mount(self) -> None:
        self.query_one("#e-name Input", Input).focus()
        if self.record.status == "running":
            self.query_one("#e-note", Static).update("It is running: applying changes stops it first.")

    def action_field(self, delta: int) -> None:
        (self.focus_next if delta > 0 else self.focus_previous)()

    def _values(self) -> tuple:
        q = self.query_one
        return (q("#e-name", FieldRow).value, q("#e-cores", FieldRow).value, q("#e-memory", FieldRow).value,
                q("#e-bridge", FieldRow).value, q("#e-nic", ChoiceRow).value, q("#e-disk", FieldRow).value,
                self._main_disk().slot if self._main_disk() else "virtio0")

    def _recheck(self) -> None:
        changes, _problems = changes_from_form(self.facts, *self._values())
        self.query_one("#apply", Button).disabled = self.running or changes is None

    def on_input_changed(self, event: Input.Changed) -> None:
        self._recheck()

    def on_choice_row_changed(self, event: ChoiceRow.Changed) -> None:
        self._recheck()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_apply()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "apply":
            self.action_apply()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        if not self.running:
            self.dismiss(self.finished)

    def action_apply(self) -> None:
        if self.running or self.finished:
            return
        changes, problems = changes_from_form(self.facts, *self._values())
        note = self.query_one("#e-note", Static)
        if changes is None:
            if problems:
                note.update("\n".join(problems))
            return
        issues = validate_edit_changes(self.record.vmid, changes)
        if issues:
            note.update(Text("\n".join(issues)))
            return
        self.running = True
        self.query_one("#apply", Button).disabled = True
        start = self.query_one("#e-start", Checkbox).value
        note.update("Applying changes...")
        self.run_worker(lambda: self._work(changes, start), thread=True, exit_on_error=False)

    def _work(self, changes: EditChanges, start_after: bool) -> None:
        vmid = self.record.vmid

        def on_step(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
            word = "Running" if result is None else ("OK" if result.ok else "FAIL")
            self.app.call_from_thread(self._say, f"{word} {idx}/{total}: {step.title}")

        try:
            info = fetch_vm_info(vmid, adapter=get_proxmox_adapter())
            if info is None:
                self.app.call_from_thread(self._done, False, f"VM {vmid} was not found.")
                return
            create_snapshot(vmid)
            result = run_edit_worker(vmid, changes, start_after=start_after, on_step=on_step,
                                     current_net0=info.config_raw)
            self.app.call_from_thread(self._done, result.ok, f"Log: {result.log_path}")
        except Exception as exc:  # noqa: BLE001
            log.exception("edit worker failed: %s", exc)
            self.app.call_from_thread(self._done, False, _error_log())

    def _say(self, line: str) -> None:
        self.query_one("#e-note", Static).update(line)

    def _done(self, ok: bool, detail: str) -> None:
        self.running = False
        self.finished = ok
        head = "Changes applied." if ok else "Applying the changes failed."
        self.query_one("#e-note", Static).update(Text(f"{head}\n{detail}"))
        cancel = self.query_one("#cancel", Button)
        cancel.label = "Close"
        cancel.focus()
        if ok:
            self.query_one("#apply", Button).disabled = True  # applying the same changes twice would resize the disk twice
            self.app.notify("Changes applied")
