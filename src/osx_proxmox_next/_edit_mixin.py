from __future__ import annotations

import logging
import tempfile
import traceback
from pathlib import Path
from threading import Thread

from textual.widgets import Button, Checkbox, Input, Static

from .domain import MIN_VMID, MAX_VMID, EditChanges, PlanStep, validate_edit_changes
from .executor import StepResult
from .rollback import create_snapshot
from .services import run_edit_worker, fetch_vm_info, get_proxmox_adapter

log = logging.getLogger(__name__)

__all__ = ["EditModeMixin"]


class EditModeMixin:
    """Mixin providing the Edit VM panel methods for NextApp."""

    def _validate_edit_form(self) -> None:
        try:
            vmid = int(self.query_one("#edit_vmid", Input).value.strip())
            valid_vmid = MIN_VMID <= vmid <= MAX_VMID
        except ValueError:
            valid_vmid = False

        form = self.query_one("#edit_form")
        if valid_vmid:
            form.remove_class("hidden")
        else:
            form.add_class("hidden")
            self.query_one("#edit_apply_btn", Button).disabled = True
            return

        has_any = any(
            self.query_one(sel, Input).value.strip()
            for sel in ("#edit_name", "#edit_cores", "#edit_memory", "#edit_bridge", "#edit_disk_add")
        )
        self.query_one("#edit_apply_btn", Button).disabled = not has_any

    def _run_edit(self) -> None:
        if self.state.edit_running:  # type: ignore[attr-defined]
            return
        try:
            vmid = int(self.query_one("#edit_vmid", Input).value.strip())
        except ValueError:
            return
        if vmid < MIN_VMID or vmid > MAX_VMID:
            return

        def _opt_int(sel: str) -> int | None:
            v = self.query_one(sel, Input).value.strip()
            if not v:
                return None
            try:
                return int(v)
            except ValueError:
                return None

        def _opt_str(sel: str) -> str | None:
            v = self.query_one(sel, Input).value.strip()
            return v if v else None

        changes = EditChanges(
            name=_opt_str("#edit_name"),
            cores=_opt_int("#edit_cores"),
            memory_mb=_opt_int("#edit_memory"),
            bridge=_opt_str("#edit_bridge"),
            disk_gb_add=_opt_int("#edit_disk_add"),
            nic_model=_opt_str("#edit_nic_model"),
            disk_name=_opt_str("#edit_disk_name") or "virtio0",
        )

        issues = validate_edit_changes(vmid, changes)
        if issues:
            result_box = self.query_one("#edit_result", Static)
            result_box.remove_class("hidden")
            result_box.add_class("edit_result_fail")
            result_box.update("\n".join(issues))
            return

        start_after = self.state.edit_start_after  # type: ignore[attr-defined]

        self.state.edit_running = True  # type: ignore[attr-defined]
        self.state.edit_done = False  # type: ignore[attr-defined]
        self.state.edit_log = []  # type: ignore[attr-defined]
        self.query_one("#edit_apply_btn", Button).disabled = True
        self.query_one("#edit_log").remove_class("hidden")
        self.query_one("#edit_log", Static).update("Applying changes...")
        self.query_one("#edit_result").add_class("hidden")

        Thread(target=self._edit_worker, args=(vmid, changes, start_after), daemon=True).start()

    def _edit_worker(self, vmid: int, changes: EditChanges, start_after: bool) -> None:
        def on_step(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
            self.call_from_thread(self._update_edit_log, idx, total, step.title, result)  # type: ignore[attr-defined]

        try:
            info = fetch_vm_info(vmid, adapter=get_proxmox_adapter())
            if info is None:
                fd, err_path = tempfile.mkstemp(prefix="edit_notfound_", suffix=".log")
                with open(fd, "w") as f:
                    f.write(f"VM {vmid} not found.\n")
                self.call_from_thread(self._finish_edit, False, Path(err_path))  # type: ignore[attr-defined]
                return

            create_snapshot(vmid)
            result = run_edit_worker(
                vmid, changes, start_after=start_after, on_step=on_step,
                current_net0=info.config_raw,
            )
            self.call_from_thread(self._finish_edit, result.ok, result.log_path)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error in edit worker: %s", exc)
            try:
                fd, err_path = tempfile.mkstemp(prefix="edit_error_", suffix=".log")
                with open(fd, "w") as f:
                    traceback.print_exc(file=f)
            except Exception:
                err_path = str(Path(tempfile.gettempdir()) / "edit_error.log")
            self.call_from_thread(self._finish_edit, False, Path(err_path))  # type: ignore[attr-defined]

    def _update_edit_log(self, idx: int, total: int, title: str, result: StepResult | None) -> None:
        if result is None:
            self.state.edit_log.append(f"Running {idx}/{total}: {title}")  # type: ignore[attr-defined]
        else:
            status = "OK" if result.ok else "FAIL"
            self.state.edit_log.append(f"{status} {idx}/{total}: {title}")  # type: ignore[attr-defined]
        visible = self.state.edit_log[-10:]  # type: ignore[attr-defined]
        self.query_one("#edit_log", Static).update("\n".join(visible))

    def _finish_edit(self, ok: bool, log_path: Path) -> None:
        self.state.edit_running = False  # type: ignore[attr-defined]
        self.state.edit_done = True  # type: ignore[attr-defined]
        self.state.edit_ok = ok  # type: ignore[attr-defined]
        if ok:
            # Clear form fields so re-clicking Apply doesn't re-apply (disk resize is non-idempotent)
            for sel in ("#edit_name", "#edit_cores", "#edit_memory", "#edit_bridge", "#edit_disk_add"):
                self.query_one(sel, Input).value = ""
        self._validate_edit_form()
        result_box = self.query_one("#edit_result", Static)
        result_box.remove_class("hidden")
        if ok:
            result_box.remove_class("edit_result_fail")
            result_box.update(f"Changes applied.\nLog: {log_path}")
            self._refresh_vm_list()
        else:
            result_box.add_class("edit_result_fail")
            result_box.update(f"Failed to apply changes.\nLog: {log_path}")
