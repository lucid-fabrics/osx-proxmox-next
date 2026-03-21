from __future__ import annotations

import logging
from pathlib import Path
from threading import Thread

from textual.widgets import Button, Checkbox, Input, Static

from .domain import MIN_VMID, MAX_VMID, PlanStep
from .executor import StepResult
from .services import list_macos_vms, run_destroy_worker

log = logging.getLogger(__name__)

__all__ = ["ManageModeMixin"]


class ManageModeMixin:
    """Mixin providing the Manage Mode tab methods for NextApp."""

    def _toggle_mode(self, mode: str) -> None:
        is_manage = mode == "manage"
        self.state.manage_mode = is_manage  # type: ignore[attr-defined]
        create_btn = self.query_one("#mode_create", Button)
        manage_btn = self.query_one("#mode_manage", Button)
        create_panel = self.query_one("#create_panel")
        manage_panel = self.query_one("#manage_panel")
        if is_manage:
            create_panel.add_class("hidden")
            manage_panel.remove_class("hidden")
            create_btn.remove_class("mode_active")
            manage_btn.add_class("mode_active")
            self._refresh_vm_list()
        else:
            manage_panel.add_class("hidden")
            create_panel.remove_class("hidden")
            manage_btn.remove_class("mode_active")
            create_btn.add_class("mode_active")

    def _refresh_vm_list(self) -> None:
        Thread(target=self._vm_list_worker, daemon=True).start()

    def _vm_list_worker(self) -> None:
        try:
            result = list_macos_vms()
            self.call_from_thread(self._finish_vm_list, result)  # type: ignore[attr-defined]
        except (OSError, RuntimeError):
            log.debug("Failed to list VMs", exc_info=True)
            self.call_from_thread(self._finish_vm_list, [])  # type: ignore[attr-defined]

    def _finish_vm_list(self, lines: list[str]) -> None:
        self.state.uninstall_vm_list = lines  # type: ignore[attr-defined]
        display = self.query_one("#vm_list_display", Static)
        if lines:
            display.update("\n".join(lines[:20]))
        else:
            display.update("No macOS VMs found.")

    def _validate_manage_vmid(self) -> None:
        text = self.query_one("#manage_vmid", Input).value.strip()
        btn = self.query_one("#manage_destroy_btn", Button)
        try:
            vmid = int(text)
            btn.disabled = vmid < MIN_VMID or vmid > MAX_VMID
        except ValueError:
            btn.disabled = True

    def _toggle_purge(self) -> None:
        cb = self.query_one("#manage_purge_cb", Checkbox)
        self.state.uninstall_purge = cb.value  # type: ignore[attr-defined]
        hint = self.query_one("#manage_hint", Static)
        if self.state.uninstall_purge:  # type: ignore[attr-defined]
            hint.update(
                "This will stop the VM, remove its configuration,\n"
                "and delete all associated disk images."
            )
        else:
            hint.update(
                "This will stop the VM and remove its configuration.\n"
                "Disk images will be kept on storage."
            )

    def _run_destroy(self) -> None:
        if self.state.uninstall_running:  # type: ignore[attr-defined]
            return
        text = self.query_one("#manage_vmid", Input).value.strip()
        try:
            vmid = int(text)
        except ValueError:
            return
        if vmid < MIN_VMID or vmid > MAX_VMID:
            return
        self.state.uninstall_running = True  # type: ignore[attr-defined]
        self.state.uninstall_done = False  # type: ignore[attr-defined]
        self.state.uninstall_log = []  # type: ignore[attr-defined]
        self.query_one("#manage_destroy_btn", Button).disabled = True
        self.query_one("#manage_log").remove_class("hidden")
        self.query_one("#manage_log", Static).update("Removing VM...")
        self.query_one("#manage_result").add_class("hidden")
        Thread(target=self._destroy_worker, args=(vmid,), daemon=True).start()

    def _destroy_worker(self, vmid: int) -> None:
        def on_step(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
            self.call_from_thread(self._update_destroy_log, idx, total, step.title, result)  # type: ignore[attr-defined]
        result, _snapshot = run_destroy_worker(vmid, purge=self.state.uninstall_purge, on_step=on_step)  # type: ignore[attr-defined]
        self.call_from_thread(self._finish_destroy, result.ok, result.log_path)  # type: ignore[attr-defined]

    def _update_destroy_log(self, idx: int, total: int, title: str, result: StepResult | None) -> None:
        if result is None:
            self.state.uninstall_log.append(f"Running {idx}/{total}: {title}")  # type: ignore[attr-defined]
        else:
            self.state.uninstall_log.append(f"{'OK' if result.ok else 'FAIL'} {idx}/{total}: {title}")  # type: ignore[attr-defined]
        visible = self.state.uninstall_log[-10:]  # type: ignore[attr-defined]
        self.query_one("#manage_log", Static).update("\n".join(visible))

    def _finish_destroy(self, ok: bool, log_path: Path) -> None:
        self.state.uninstall_running = False  # type: ignore[attr-defined]
        self.state.uninstall_done = True  # type: ignore[attr-defined]
        self.state.uninstall_ok = ok  # type: ignore[attr-defined]
        self._validate_manage_vmid()
        result_box = self.query_one("#manage_result", Static)
        result_box.remove_class("hidden")
        if ok:
            result_box.remove_class("manage_result_fail")
            result_box.update(f"VM removed successfully.\nLog: {log_path}")
            self._refresh_vm_list()
        else:
            result_box.add_class("manage_result_fail")
            result_box.update(f"Failed to remove VM.\nLog: {log_path}")
