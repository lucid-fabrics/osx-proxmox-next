from __future__ import annotations

import logging
from pathlib import Path
from threading import Thread

log = logging.getLogger(__name__)

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, Header, Input, ProgressBar, Static

from ._edit_mixin import EditModeMixin
from ._manage_mixin import ManageModeMixin
from ._wizard_mixin import WizardStepsMixin
from .assets import required_assets
from .defaults import (
    DEFAULT_ISO_DIR,
    detect_cpu_cores,
    detect_cpu_info,
    detect_iso_storage,
    detect_net_model,
)
from .domain import PlanStep, SUPPORTED_MACOS, validate_config
from .executor import StepResult
from .models import WizardState
from .planner import build_plan
from .preflight import PreflightCheck
from .rollback import RollbackSnapshot
from .screens import (
    compose_step1,
    compose_step2,
    compose_step3,
    compose_step4,
    compose_step5,
    compose_step6,
    format_preflight_text,
    format_install_result,
)
from .services import (
    detect_next_vmid,
    detect_storage_targets,
    run_dry_apply,
    run_live_install,
    run_preflight_worker,
)


class NextApp(WizardStepsMixin, ManageModeMixin, EditModeMixin, App):
    CSS_PATH = Path(__file__).with_suffix(".tcss")

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
    ]

    current_step: reactive[int] = reactive(1)

    def __init__(self) -> None:
        super().__init__()
        self.state = WizardState()
        self._cpu_info = detect_cpu_info()
        self.state.use_penryn = self._cpu_info.needs_penryn
        self.state.net_model = detect_net_model(self._cpu_info)
        self.state.storage_targets = self._detect_storage_targets()
        self.state.iso_dirs = detect_iso_storage()
        self.state.selected_iso_dir = self.state.iso_dirs[0] if self.state.iso_dirs else DEFAULT_ISO_DIR

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="step_bar")
        with Container(id="body"):
            yield from compose_step1()
            yield from compose_step2()
            yield from compose_step3(self.state.storage_targets)
            yield from compose_step4(self._cpu_info)
            yield from compose_step5()
            yield from compose_step6()

    def on_mount(self) -> None:
        self._update_step_bar()
        if self.state.storage_targets:
            self.state.selected_storage = self.state.storage_targets[0]
        Thread(target=self._preflight_worker, daemon=True).start()

    def watch_current_step(self, old_value: int, new_value: int) -> None:
        for step_num in range(1, 7):
            container = self.query_one(f"#step{step_num}")
            if step_num == new_value:
                container.remove_class("step_hidden")
            else:
                container.add_class("step_hidden")
        self._update_step_bar()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid.startswith("os_"):
            os_key = bid[3:]
            if os_key in SUPPORTED_MACOS:
                self._select_os(os_key)
            return

        if bid.startswith("storage_"):
            try:
                idx = int(bid.split("_")[1])
                self._select_storage(self.state.storage_targets[idx])
            except (ValueError, IndexError):
                log.debug("Invalid storage button index: %s", bid)
            return

        _NEXT = {"preflight_next_btn", "next_btn", "next_btn_3", "next_btn_4", "next_btn_5"}
        _BACK = {"back_btn_2", "back_btn_3", "back_btn_4", "back_btn_5", "back_btn_6"}
        _EXIT = {"exit_btn", "exit_btn_2", "exit_btn_3", "exit_btn_4", "exit_btn_5", "exit_btn_6"}
        if bid in _NEXT:
            self._go_next()
        elif bid in _BACK:
            self._go_back()
        elif bid in _EXIT:
            self.exit()
        else:
            handlers = {
                "preflight_rerun_btn": self._rerun_preflight,
                "suggest_btn": self._apply_host_defaults,
                "smbios_btn": self._generate_smbios,
                "dry_run_btn": self._run_dry_apply,
                "install_btn": self._run_live_install,
                "mode_create": lambda: self._toggle_mode("create"),
                "mode_manage": lambda: self._toggle_mode("manage"),
                "manage_refresh_btn": self._refresh_vm_list,
                "manage_destroy_btn": self._run_destroy,
                "edit_apply_btn": self._run_edit,
            }
            handler = handlers.get(bid)
            if handler:
                handler()

    def on_input_changed(self, event: Input.Changed) -> None:
        target_ids = {"vmid", "name", "memory", "disk", "bridge", "storage_input", "installer_path"}
        if (event.input.id or "") in target_ids:
            self._validate_form(quiet=True)
        if event.input.id == "manage_vmid":
            self._validate_manage_vmid()
        if event.input.id in {"edit_vmid", "edit_name", "edit_cores", "edit_memory", "edit_bridge", "edit_disk_add"}:
            self._validate_edit_form()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "manage_purge_cb":
            self._toggle_purge()
        if event.checkbox.id == "edit_start_after_cb":
            self.state.edit_start_after = event.checkbox.value
        if event.checkbox.id == "apple_services_cb":
            self.state.apple_services = event.checkbox.value
            self._generate_smbios()
            self._toggle_apple_services_fields()
        if event.checkbox.id == "penryn_cb":
            self.state.use_penryn = event.checkbox.value
            if event.checkbox.value:
                self.query_one("#cores", Input).value = "4"
                self.query_one("#penryn_hint", Static).remove_class("step_hidden")
            else:
                self.query_one("#cores", Input).value = str(detect_cpu_cores())
                self.query_one("#penryn_hint", Static).add_class("step_hidden")
        if event.checkbox.id == "e1000_cb":
            self.state.net_model = "e1000-82545em" if event.checkbox.value else "vmxnet3"
            if event.checkbox.value:
                self.query_one("#e1000_hint", Static).remove_class("step_hidden")
            else:
                self.query_one("#e1000_hint", Static).add_class("step_hidden")

    def _toggle_apple_services_fields(self) -> None:
        c = self.query_one("#apple_services_fields")
        (c.remove_class if self.state.apple_services else c.add_class)("hidden")

    def _go_next(self) -> None:
        step = self.current_step
        if step == 1:
            if not self.state.preflight_ok:
                return
            self.current_step = 2
        elif step == 2:
            if not self.state.selected_os:
                return
            self.current_step = 3
        elif step == 3:
            if not self.state.selected_storage:
                return
            self._fill_form()
            self.current_step = 4
        elif step == 4:
            if not self._validate_form(quiet=False):
                return
            config = self._read_form()
            if not config:
                return
            issues = validate_config(config)
            if issues:
                self._show_form_errors(issues)
                return
            self.state.config = config
            self.state.plan_steps = build_plan(config)
            self._render_config_summary()
            self.current_step = 5
            self._check_and_download_assets()
        elif step == 5:
            if not self.state.dry_run_ok:
                return
            self._prepare_install_step()
            self.current_step = 6

    def _go_back(self) -> None:
        self.current_step = max(1, self.current_step - 1)

    def _update_preflight_display(self) -> None:
        text = format_preflight_text(self.state.preflight_done, self.state.preflight_checks)
        self.query_one("#preflight_checks", Static).update(text)

    def _rerun_preflight(self) -> None:
        self.state.preflight_done = False
        self.state.preflight_ok = False
        self.query_one("#preflight_next_btn", Button).disabled = True
        self._update_preflight_display()
        Thread(target=self._preflight_worker, daemon=True).start()

    def _check_and_download_assets(self) -> None:
        config = self.state.config
        if not config:
            return
        assets = required_assets(config)
        missing = [a for a in assets if not a.ok]
        downloadable = [a for a in missing if a.downloadable]
        if not missing:
            self.state.assets_ok = True
            self.state.assets_missing = []
            self.state.downloads_complete = True
            self.query_one("#download_status", Static).update("Assets: OK")
            self.query_one("#dry_run_btn", Button).disabled = False
            return
        self.state.assets_ok = False
        self.state.assets_missing = missing
        if downloadable:
            names = ", ".join(a.name for a in downloadable)
            self.query_one("#download_status", Static).update(f"Downloading: {names}...")
            self.query_one("#download_progress").remove_class("hidden")
            self.query_one("#download_progress", ProgressBar).update(total=100, progress=0)
            self.state.download_running = True
            Thread(target=self._download_worker, args=(config, missing), daemon=True).start()
        else:
            self.query_one("#download_status", Static).update(
                f"Missing assets: {', '.join(a.name for a in missing)}. Provide path manually."
            )

    def _run_dry_apply(self) -> None:
        if self.state.apply_running:
            return
        if not self.state.plan_steps:
            return
        self.state.apply_running = True
        self.state.apply_log = []
        self.query_one("#dry_progress").remove_class("hidden")
        self.query_one("#dry_log").remove_class("hidden")
        self.query_one("#dry_progress", ProgressBar).update(total=len(self.state.plan_steps), progress=0)
        self.query_one("#dry_log", Static).update("Starting dry run...")
        self.query_one("#dry_run_btn", Button).disabled = True

        def callback(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
            self.call_from_thread(self._update_dry_progress, idx, total, step.title, result)

        def worker() -> None:
            result = run_dry_apply(self.state.plan_steps, on_step=callback)
            self.call_from_thread(self._finish_dry_apply, result.ok, result.log_path)

        Thread(target=worker, daemon=True).start()

    def _update_dry_progress(self, idx: int, total: int, title: str, result: StepResult | None) -> None:
        self.query_one("#dry_progress", ProgressBar).update(total=total, progress=idx)
        self._append_log("#dry_log", self._step_log_line(idx, total, title, result))

    def _finish_dry_apply(self, ok: bool, log_path: Path) -> None:
        self.state.apply_running = False
        self.state.dry_run_done = True
        self.state.dry_run_ok = ok
        if ok:
            self._append_log("#dry_log", f"Dry run complete. Log: {log_path}")
            self.query_one("#next_btn_5", Button).disabled = False
            self.notify("Dry run passed", severity="information")
        else:
            self._append_log("#dry_log", f"Dry run FAILED. Log: {log_path}")
            self.query_one("#dry_run_btn", Button).disabled = False
            self.notify("Dry run failed", severity="error")

    def _prepare_install_step(self) -> None:
        if not self.state.config:
            return
        label = SUPPORTED_MACOS.get(self.state.config.macos, {}).get("label", self.state.config.macos)
        self.query_one("#install_btn", Button).label = f"Install {label}"
        self.query_one("#install_btn").remove_class("hidden")

    def _run_live_install(self) -> None:
        if self.state.apply_running:
            return
        if not self.state.config or not self.state.plan_steps:
            return
        if not self.state.preflight_ok:
            self.notify("Preflight has failures. Fix before install.", severity="error")
            return
        self.state.apply_running = True
        self.state.apply_log = []
        self.query_one("#install_btn").add_class("hidden")
        self.query_one("#live_progress").remove_class("hidden")
        self.query_one("#live_log").remove_class("hidden")
        self.query_one("#live_progress", ProgressBar).update(
            total=len(self.state.plan_steps), progress=0
        )
        self.query_one("#live_log", Static).update("Starting live install...")

        def callback(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
            self.call_from_thread(self._update_live_progress, idx, total, step.title, result)

        def worker() -> None:
            result, snapshot = run_live_install(
                self.state.config.vmid, self.state.plan_steps, on_step=callback
            )
            self.state.snapshot = snapshot
            self.call_from_thread(self._finish_live_install, result.ok, result.log_path, snapshot)

        Thread(target=worker, daemon=True).start()

    def _update_live_progress(self, idx: int, total: int, title: str, result: StepResult | None) -> None:
        self.query_one("#live_progress", ProgressBar).update(total=total, progress=idx)
        self._append_log("#live_log", self._step_log_line(idx, total, title, result))

    def _finish_live_install(
        self, ok: bool, log_path: Path, snapshot: RollbackSnapshot | None
    ) -> None:
        self.state.apply_running = False
        self.state.live_done = True
        self.state.live_ok = ok
        self.state.live_log = log_path
        vmid = self.state.config.vmid if self.state.config else "???"
        result_box = self.query_one("#result_box", Static)
        result_box.remove_class("hidden")
        text = format_install_result(ok, vmid, log_path, snapshot)
        if ok:
            result_box.remove_class("result_fail")
            self.notify("macOS VM created", severity="information")
        else:
            result_box.add_class("result_fail")
            self.notify("Install failed", severity="error")
        result_box.update(text)

    def _preflight_worker(self) -> None:
        def _on_status(msg: str) -> None:
            self.call_from_thread(self._update_preflight_status, msg)
        checks = run_preflight_worker(on_status=_on_status)
        self.call_from_thread(self._finish_preflight, checks)

    def _update_preflight_status(self, msg: str) -> None:
        self.query_one("#preflight_checks", Static).update(f"  ⏳ {msg}")

    def _finish_preflight(self, checks: list[PreflightCheck]) -> None:
        self.state.preflight_done = True
        self.state.preflight_checks = checks
        self.state.preflight_ok = all(c.ok for c in checks)
        self._update_preflight_display()
        self.query_one("#preflight_next_btn", Button).disabled = not self.state.preflight_ok

    def _detect_storage_targets(self) -> list[str]:
        return detect_storage_targets()

    def _detect_next_vmid(self) -> int:
        return detect_next_vmid()

    def _set_input_value(self, selector: str, value: str) -> None:
        widget = self.query_one(selector, Input)
        widget.value = value
        widget.cursor_position = len(value)
        widget.refresh(layout=True)

    def _update_step_bar(self) -> None:
        labels = ["Preflight", "OS", "Storage", "Config", "Dry Run", "Install"]
        s = self.current_step
        parts = [
            f"{'[x]' if n < s else '[>]' if n == s else '[ ]'} {n}.{labels[n-1]}"
            for n in range(1, 7)
        ]
        self.query_one("#step_bar", Static).update("  ".join(parts))

    def _step_log_line(self, idx: int, total: int, title: str, result: StepResult | None) -> str:
        if result is None:
            return f"Running {idx}/{total}: {title}"
        return f"{'OK' if result.ok else 'FAIL'} {idx}/{total}: {title} (rc={result.returncode})"

    def _append_log(self, selector: str, line: str) -> None:
        self.state.apply_log.append(line)
        widget = self.query_one(selector, Static)
        visible = self.state.apply_log[-15:]
        widget.update("\n".join(visible))


def run() -> None:
    NextApp().run()
