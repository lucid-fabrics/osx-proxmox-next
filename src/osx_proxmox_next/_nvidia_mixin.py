from __future__ import annotations

import logging
import tempfile
import traceback
from pathlib import Path
from threading import Thread

from textual.widgets import Button, Checkbox, Input, Select, Static

from . import nullmoth
from .defaults import DEFAULT_ISO_DIR
from .domain import MAX_VMID, MIN_VMID, PlanStep
from .downloader import DownloadError
from .executor import StepResult, apply_plan
from .oc_builder import AMFIPASS, ensure_amfipass
from .gpu import GpuInfo
from .nvidia_host import detect_nvidia_gpus
from .rollback import create_snapshot

log = logging.getLogger(__name__)

__all__ = ["NvidiaModeMixin"]


class NvidiaModeMixin:
    """Mixin providing the NVIDIA GPU (NullMoth, Beta) panel for NextApp."""

    # ── form state ────────────────────────────────────────────────────

    def _nv_vmid(self) -> int | None:
        try:
            vmid = int(self.query_one("#nv_vmid", Input).value.strip())  # type: ignore[attr-defined]
        except ValueError:
            return None
        return vmid if MIN_VMID <= vmid <= MAX_VMID else None

    def _nv_gpu_bdf(self) -> str | None:
        value = self.query_one("#nv_gpu_select", Select).value  # type: ignore[attr-defined]
        return None if value is Select.BLANK else str(value)

    def _validate_nv_form(self) -> None:
        """Apply stays disabled until a VM, a GPU and the acknowledgement are all set."""
        has_vm = self._nv_vmid() is not None
        acked = self.query_one("#nv_ack_cb", Checkbox).value  # type: ignore[attr-defined]
        running = self.state.nv_running  # type: ignore[attr-defined]
        self.query_one("#nv_apply_btn", Button).disabled = not (  # type: ignore[attr-defined]
            has_vm and acked and self._nv_gpu_bdf() is not None and not running
        )
        self.query_one("#nv_detach_btn", Button).disabled = not has_vm or running  # type: ignore[attr-defined]

    # ── GPU detection ─────────────────────────────────────────────────

    def _nv_detect(self) -> None:
        Thread(target=self._nv_detect_worker, daemon=True).start()

    def _nv_detect_worker(self) -> None:
        try:
            gpus = detect_nvidia_gpus()
        except OSError:
            log.debug("GPU detection failed", exc_info=True)
            gpus = []
        self.call_from_thread(self._finish_nv_detect, gpus)  # type: ignore[attr-defined]

    def _finish_nv_detect(self, gpus: list[GpuInfo]) -> None:
        select = self.query_one("#nv_gpu_select", Select)  # type: ignore[attr-defined]
        select.set_options([(g.label, g.bdf) for g in gpus])
        info = self.query_one("#nv_gpu_info", Static)  # type: ignore[attr-defined]
        info.update("\n".join(g.label for g in gpus) if gpus else "No NVIDIA GPU found on this host.")
        self._validate_nv_form()

    # ── attach ────────────────────────────────────────────────────────

    def _nv_show(self, text: str, fail: bool) -> None:
        box = self.query_one("#nv_result", Static)  # type: ignore[attr-defined]
        box.remove_class("hidden")
        (box.add_class if fail else box.remove_class)("nv_result_fail")
        box.update(text)

    def _run_nv_apply(self) -> None:
        vmid, bdf = self._nv_vmid(), self._nv_gpu_bdf()
        acked = self.query_one("#nv_ack_cb", Checkbox).value  # type: ignore[attr-defined]
        if self.state.nv_running or vmid is None or bdf is None or not acked:  # type: ignore[attr-defined]
            return
        macos = self.query_one("#nv_macos_select", Select).value  # type: ignore[attr-defined]
        stop = self.query_one("#nv_stop_cb", Checkbox).value  # type: ignore[attr-defined]
        self._nv_begin("Checking the VM and the card...")
        Thread(
            target=self._nv_attach_worker,
            args=(vmid, bdf, None if macos is Select.BLANK else str(macos), stop),
            daemon=True,
        ).start()

    def _run_nv_detach(self) -> None:
        vmid = self._nv_vmid()
        if self.state.nv_running or vmid is None:  # type: ignore[attr-defined]
            return
        stop = self.query_one("#nv_stop_cb", Checkbox).value  # type: ignore[attr-defined]
        self._nv_begin("Checking the VM...")
        Thread(target=self._nv_detach_worker, args=(vmid, stop), daemon=True).start()

    def _nv_begin(self, first_line: str) -> None:
        self.state.nv_running = True  # type: ignore[attr-defined]
        self.state.nv_log = []  # type: ignore[attr-defined]
        self.query_one("#nv_log").remove_class("hidden")  # type: ignore[attr-defined]
        self.query_one("#nv_log", Static).update(first_line)  # type: ignore[attr-defined]
        self.query_one("#nv_result").add_class("hidden")  # type: ignore[attr-defined]
        self._validate_nv_form()

    def _nv_on_step(self, idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
        self.call_from_thread(self._update_nv_log, idx, total, step.title, result)  # type: ignore[attr-defined]

    def _nv_attach_worker(self, vmid: int, bdf: str, macos: str | None, stop: bool) -> None:
        try:
            cache = Path(DEFAULT_ISO_DIR)
            plan = nullmoth.prepare_attach(vmid, bdf, cache / AMFIPASS.filename, macos=macos, allow_stop=stop)
            if not plan.ok:
                self.call_from_thread(self._finish_nv, False, _plan_text(plan), None)  # type: ignore[attr-defined]
                return
            ensure_amfipass(cache)
            create_snapshot(vmid)
            result = apply_plan(plan.steps, execute=True, on_step=self._nv_on_step)
            text = _plan_text(plan) if result.ok else "Failed to attach the GPU."
            self.call_from_thread(self._finish_nv, result.ok, text, result.log_path)  # type: ignore[attr-defined]
        except DownloadError as exc:
            self.call_from_thread(self._finish_nv, False, f"Could not fetch AMFIPass: {exc}", None)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            log.exception("Unexpected error in NVIDIA attach worker")
            self.call_from_thread(self._finish_nv, False, _crash_log(), None)  # type: ignore[attr-defined]

    def _nv_detach_worker(self, vmid: int, stop: bool) -> None:
        try:
            plan = nullmoth.prepare_detach(vmid, allow_stop=stop)
            if not plan.ok:
                self.call_from_thread(self._finish_nv, False, _plan_text(plan), None)  # type: ignore[attr-defined]
                return
            create_snapshot(vmid)
            result = apply_plan(plan.steps, execute=True, on_step=self._nv_on_step)
            text = "NullMoth changes removed." if result.ok else "Failed to detach the GPU."
            self.call_from_thread(self._finish_nv, result.ok, text, result.log_path)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            log.exception("Unexpected error in NVIDIA detach worker")
            self.call_from_thread(self._finish_nv, False, _crash_log(), None)  # type: ignore[attr-defined]

    def _update_nv_log(self, idx: int, total: int, title: str, result: StepResult | None) -> None:
        status = "Running" if result is None else ("OK" if result.ok else "FAIL")
        self.state.nv_log.append(f"{status} {idx}/{total}: {title}")  # type: ignore[attr-defined]
        self.query_one("#nv_log", Static).update("\n".join(self.state.nv_log[-10:]))  # type: ignore[attr-defined]

    def _finish_nv(self, ok: bool, text: str, log_path: Path | None) -> None:
        self.state.nv_running = False  # type: ignore[attr-defined]
        self.state.nv_ok = ok  # type: ignore[attr-defined]
        if ok:
            # The acknowledgement is per run: a second attach asks again.
            self.query_one("#nv_ack_cb", Checkbox).value = False  # type: ignore[attr-defined]
        self._validate_nv_form()
        suffix = f"\nLog: {log_path}" if log_path else ""
        self._nv_show(text + suffix, fail=not ok)


def _plan_text(plan: nullmoth.AttachPlan) -> str:
    """Refusals, hints and warnings, or the next steps after a clean run."""
    lines: list[str] = []
    if plan.issues:
        lines += [f"Refused: {issue}" for issue in plan.issues]
        lines += plan.hints
    else:
        lines.append("GPU attached and OpenCore prepared.")
        lines += [f"{idx}. {step}" for idx, step in enumerate(nullmoth.NEXT_STEPS, start=1)]
    lines += [f"Warning: {w}" for w in plan.warnings]
    return "\n".join(lines)


def _crash_log() -> str:
    fd, path = tempfile.mkstemp(prefix="nvidia_error_", suffix=".log")
    with open(fd, "w") as handle:
        traceback.print_exc(file=handle)
    return f"Unexpected error. Details: {path}"
