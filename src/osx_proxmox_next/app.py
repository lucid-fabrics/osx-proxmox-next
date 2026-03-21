from __future__ import annotations

import logging
from pathlib import Path
from threading import Thread

log = logging.getLogger(__name__)

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, Header, Input, ProgressBar, Static

from .assets import AssetCheck, required_assets
from .defaults import (
    DEFAULT_BRIDGE,
    DEFAULT_ISO_DIR,
    DEFAULT_STORAGE,
    default_disk_gb,
    detect_cpu_cores,
    detect_cpu_info,
    detect_iso_storage,
    detect_memory_mb,
    detect_net_model,
)
from .domain import MIN_VMID, MAX_VMID, PlanStep, SUPPORTED_MACOS, VmConfig, validate_config
from .executor import StepResult
from .forms import validate_form_values, build_vm_config_from_values
from .forms.form_handler import FormValues
from .models import WizardState
from .planner import build_plan
from .preflight import PreflightCheck
from .rollback import RollbackSnapshot, rollback_hints
from .screens import (
    compose_step1,
    compose_step2,
    compose_step3,
    compose_step4,
    compose_step5,
    compose_step6,
    build_config_summary_text,
)
from .services import (
    detect_next_vmid,
    detect_storage_targets,
    get_proxmox_adapter,
    run_destroy_worker,
    run_download_worker,
    run_dry_apply,
    run_live_install,
    run_preflight_worker,
)
from .smbios import generate_smbios

class NextApp(App):
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

        # OS selection
        if bid.startswith("os_"):
            os_key = bid[3:]
            if os_key in SUPPORTED_MACOS:
                self._select_os(os_key)
            return

        # Storage selection
        if bid.startswith("storage_"):
            try:
                idx = int(bid.split("_")[1])
                self._select_storage(self.state.storage_targets[idx])
            except (ValueError, IndexError):
                log.debug("Invalid storage button index: %s", bid)
            return

        handlers = {
            "preflight_next_btn": lambda: self._go_next(),
            "next_btn": lambda: self._go_next(),
            "next_btn_3": lambda: self._go_next(),
            "next_btn_4": lambda: self._go_next(),
            "next_btn_5": lambda: self._go_next(),
            "back_btn_2": lambda: self._go_back(),
            "back_btn_3": lambda: self._go_back(),
            "back_btn_4": lambda: self._go_back(),
            "back_btn_5": lambda: self._go_back(),
            "back_btn_6": lambda: self._go_back(),
            "preflight_rerun_btn": self._rerun_preflight,
            "suggest_btn": self._apply_host_defaults,
            "smbios_btn": self._generate_smbios,
            "dry_run_btn": self._run_dry_apply,
            "install_btn": self._run_live_install,
            "mode_create": lambda: self._toggle_mode("create"),
            "mode_manage": lambda: self._toggle_mode("manage"),
            "manage_refresh_btn": self._refresh_vm_list,
            "manage_destroy_btn": self._run_destroy,
            "exit_btn": lambda: self.exit(),
            "exit_btn_2": lambda: self.exit(),
            "exit_btn_3": lambda: self.exit(),
            "exit_btn_4": lambda: self.exit(),
            "exit_btn_5": lambda: self.exit(),
            "exit_btn_6": lambda: self.exit(),
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

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "manage_purge_cb":
            self._toggle_purge()
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
        container = self.query_one("#apple_services_fields")
        if self.state.apple_services:
            container.remove_class("hidden")
        else:
            container.add_class("hidden")

    # ── Navigation ──────────────────────────────────────────────────

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
            self._prefill_form()
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

    # ── Step 1: Preflight ────────────────────────────────────────────

    def _update_preflight_display(self) -> None:
        if not self.state.preflight_done:
            text = "Checking..."
        else:
            lines = []
            passed = [c for c in self.state.preflight_checks if c.ok]
            failed = [c for c in self.state.preflight_checks if not c.ok]
            for c in passed:
                lines.append(f"  ✓ {c.name}")
            for c in failed:
                lines.append(f"  ✗ {c.name}: {c.details}")
            if failed:
                header = f"{len(failed)} check(s) failed"
            else:
                header = f"All {len(passed)} checks passed"
            text = header + "\n" + "\n".join(lines)
        self.query_one("#preflight_checks", Static).update(text)

    def _rerun_preflight(self) -> None:
        self.state.preflight_done = False
        self.state.preflight_ok = False
        self.query_one("#preflight_next_btn", Button).disabled = True
        self._update_preflight_display()
        Thread(target=self._preflight_worker, daemon=True).start()

    # ── Step 2: OS Selection ────────────────────────────────────────

    def _select_os(self, key: str) -> None:
        self.state.selected_os = key
        self.state.smbios = generate_smbios(key, self.state.apple_services)
        # Update card styles
        for os_key in SUPPORTED_MACOS:
            card = self.query_one(f"#os_{os_key}")
            if os_key == key:
                card.add_class("os_selected")
            else:
                card.remove_class("os_selected")
        # Enable Next
        self.query_one("#next_btn", Button).disabled = False

    # ── Step 3: Storage Selection ───────────────────────────────────

    def _select_storage(self, target: str) -> None:
        self.state.selected_storage = target
        for idx in range(len(self.state.storage_targets)):
            btn = self.query_one(f"#storage_{idx}", Button)
            if self.state.storage_targets[idx] == target:
                btn.add_class("storage_selected")
            else:
                btn.remove_class("storage_selected")

    # ── Step 4: Configuration ───────────────────────────────────────

    def _prefill_form(self) -> None:
        macos = self.state.selected_os
        self._set_input_value("#vmid", str(self._detect_next_vmid()))
        self._set_input_value("#name", f"macos-{macos}")
        self._set_input_value("#cores", str(detect_cpu_cores()))
        self._set_input_value("#memory", str(detect_memory_mb()))
        self._set_input_value("#disk", str(default_disk_gb(macos)))
        self._set_input_value("#bridge", DEFAULT_BRIDGE)
        self._set_input_value("#storage_input", self.state.selected_storage)
        self._set_input_value("#iso_dir", self.state.selected_iso_dir)
        self._set_input_value("#installer_path", "")
        self._update_smbios_preview()

    def _apply_host_defaults(self) -> None:
        macos = self.state.selected_os or "sequoia"
        self._set_input_value("#vmid", str(self._detect_next_vmid()))
        self._set_input_value("#name", f"macos-{macos}")
        self._set_input_value("#cores", str(detect_cpu_cores()))
        self._set_input_value("#memory", str(detect_memory_mb()))
        self._set_input_value("#disk", str(default_disk_gb(macos)))
        self._set_input_value("#bridge", DEFAULT_BRIDGE)
        self._set_input_value("#storage_input", self.state.selected_storage or DEFAULT_STORAGE)
        self._set_input_value("#iso_dir", self.state.selected_iso_dir)
        if not self.state.smbios:
            existing_uuid = self.query_one("#existing_uuid", Input).value.strip().upper()
            apple_services = self.state.apple_services
            if existing_uuid:
                identity = generate_smbios(macos, apple_services)
                identity.uuid = existing_uuid
                self.state.smbios = identity
            else:
                self.state.smbios = generate_smbios(macos, apple_services)
        self._update_smbios_preview()

    def _generate_smbios(self) -> None:
        macos = self.state.selected_os or "sequoia"
        existing_uuid = self.query_one("#existing_uuid", Input).value.strip().upper()
        apple_services = self.state.apple_services

        if existing_uuid:
            # Preserve existing UUID, generate other fields
            identity = generate_smbios(macos, apple_services)
            identity.uuid = existing_uuid
            self.state.smbios = identity
        else:
            self.state.smbios = generate_smbios(macos, apple_services)
        self._update_smbios_preview()

    def _update_smbios_preview(self) -> None:
        smbios = self.state.smbios
        if smbios:
            text = f"SMBIOS: serial={smbios.serial}  uuid={smbios.uuid}  model={smbios.model}"
            if self.state.apple_services:
                text += "  [Apple Services]"
        else:
            text = "SMBIOS: not generated yet."
        self.query_one("#smbios_preview", Static).update(text)

    def _validate_form(self, quiet: bool = False) -> bool:
        values = self._read_form_values()
        errors = validate_form_values(values)

        # Apply invalid CSS classes to widgets
        for field_id in ("vmid", "name", "memory", "disk", "bridge", "storage_input"):
            widget = self.query_one(f"#{field_id}", Input)
            if field_id in errors:
                widget.add_class("invalid")
            else:
                widget.remove_class("invalid")

        self.state.form_errors = errors
        if errors:
            self.query_one("#form_errors", Static).update(" ".join(errors.values()))
            if not quiet:
                self.notify("Fix form errors before continuing", severity="warning")
            return False

        self.query_one("#form_errors", Static).update("")
        return True

    def _show_form_errors(self, issues: list[str]) -> None:
        self.query_one("#form_errors", Static).update(" ".join(issues))
        self.notify("Validation failed", severity="error")

    def _read_form_values(self) -> FormValues:
        """Read current widget values into a FormValues dataclass."""
        return FormValues(
            vmid=self.query_one("#vmid", Input).value.strip(),
            name=self.query_one("#name", Input).value.strip(),
            cores=self.query_one("#cores", Input).value.strip(),
            memory=self.query_one("#memory", Input).value.strip(),
            disk=self.query_one("#disk", Input).value.strip(),
            bridge=self.query_one("#bridge", Input).value.strip(),
            storage=self.query_one("#storage_input", Input).value.strip(),
            iso_dir=self.query_one("#iso_dir", Input).value.strip(),
            installer_path=self.query_one("#installer_path", Input).value.strip(),
            existing_uuid=self.query_one("#existing_uuid", Input).value.strip().upper(),
            custom_vmgenid=self.query_one("#custom_vmgenid", Input).value.strip() if self.state.apple_services else "",
            custom_mac=self.query_one("#custom_mac", Input).value.strip() if self.state.apple_services else "",
            selected_os=self.state.selected_os,
            apple_services=self.state.apple_services,
            use_penryn=self.state.use_penryn,
            net_model=self.state.net_model,
            smbios=self.state.smbios,
        )

    def _read_form(self) -> VmConfig | None:
        return build_vm_config_from_values(self._read_form_values())

    # ── Step 5: Review & Dry Run ────────────────────────────────────

    def _render_config_summary(self) -> None:
        config = self.state.config
        if not config:
            return
        cpu = detect_cpu_info()
        text = build_config_summary_text(config, self.state.plan_steps, cpu)
        self.query_one("#config_summary", Static).update(text)

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

    def _download_worker(self, config: VmConfig, missing: list[AssetCheck]) -> None:
        def on_progress(phase: str, pct: int) -> None:
            self.call_from_thread(self._update_download_progress, phase, pct)

        errors = run_download_worker(config, missing, on_progress=on_progress)
        self.call_from_thread(self._finish_download, errors)

    def _update_download_progress(self, phase: str, pct: int) -> None:
        self.state.download_pct = pct
        self.state.download_phase = phase
        self.query_one("#download_progress", ProgressBar).update(total=100, progress=pct)
        if pct >= 100:
            self.query_one("#download_status", Static).update(f"Finalizing {phase}...")
        else:
            self.query_one("#download_status", Static).update(f"Downloading {phase}... {pct}%")

    def _finish_download(self, errors: list[str]) -> None:
        self.state.download_running = False
        self.query_one("#download_progress").add_class("hidden")
        if errors:
            self.state.download_errors = errors
            self.query_one("#download_status", Static).update(
                "Download errors: " + "; ".join(errors)
            )
            self.notify("Some downloads failed", severity="error")
        else:
            self.state.downloads_complete = True
            # Rebuild plan now that downloaded assets exist on disk
            self._rebuild_plan_after_download()
            self.query_one("#download_status", Static).update("Assets: downloaded and ready")
            self.query_one("#dry_run_btn", Button).disabled = False
            self.notify("Assets downloaded", severity="information")

    def _rebuild_plan_after_download(self) -> None:
        """Rebuild config and plan so asset paths resolve to newly downloaded files."""
        config = self._read_form()
        if config:
            self.state.config = config
            try:
                self.state.plan_steps = build_plan(config)
            except ValueError:
                log.debug("Failed to rebuild plan after download", exc_info=True)
            self._render_config_summary()

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
        if result is None:
            self._append_log("#dry_log", f"Running {idx}/{total}: {title}")
        else:
            self._append_log("#dry_log", f"{'OK' if result.ok else 'FAIL'} {idx}/{total}: {title} (rc={result.returncode})")

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

    # ── Step 6: Live Install ────────────────────────────────────────

    def _prepare_install_step(self) -> None:
        config = self.state.config
        if not config:
            return
        meta = SUPPORTED_MACOS.get(config.macos, {})
        label = meta.get("label", config.macos)
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
        if result is None:
            self._append_log("#live_log", f"Running {idx}/{total}: {title}")
        else:
            self._append_log("#live_log", f"{'OK' if result.ok else 'FAIL'} {idx}/{total}: {title} (rc={result.returncode})")

    def _finish_live_install(
        self, ok: bool, log_path: Path, snapshot: RollbackSnapshot | None
    ) -> None:
        self.state.apply_running = False
        self.state.live_done = True
        self.state.live_ok = ok
        self.state.live_log = log_path

        result_box = self.query_one("#result_box", Static)
        result_box.remove_class("hidden")

        if ok:
            result_box.remove_class("result_fail")
            vmid = self.state.config.vmid if self.state.config else "???"
            lines = [
                "Install completed successfully!",
                f"Log: {log_path}",
                "",
                "POST-INSTALL: After macOS finishes installing, fix the boot order",
                "so the main disk boots first (instead of recovery):",
                f"  qm set {vmid} --boot order=virtio0;ide0",
                "",
                "If this saved you time: https://ko-fi.com/lucidfabrics",
            ]
            result_box.update("\n".join(lines))
            self.notify("macOS VM created", severity="information")
        else:
            result_box.add_class("result_fail")
            lines = ["Install FAILED.", f"Log: {log_path}"]
            if snapshot:
                lines.append("")
                lines.extend(rollback_hints(snapshot))
            result_box.update("\n".join(lines))
            self.notify("Install failed", severity="error")

    # ── Manage Mode ─────────────────────────────────────────────────

    def _toggle_mode(self, mode: str) -> None:
        is_manage = mode == "manage"
        self.state.manage_mode = is_manage
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
            res = get_proxmox_adapter().qm("list")
            if not res.ok:
                self.call_from_thread(self._finish_vm_list, [])
                return
            all_lines = res.output.strip().splitlines()
            # Filter to macOS VMs only (have isa-applesmc in config)
            macos_lines: list[str] = []
            for line in all_lines[1:]:  # skip header
                parts = line.split()
                if not parts:
                    continue
                vmid = parts[0]
                cfg_res = get_proxmox_adapter().qm("config", vmid)
                if cfg_res.ok and "isa-applesmc" in cfg_res.output:
                    macos_lines.append(line)
            header = all_lines[0] if all_lines else ""
            result = [header] + macos_lines if macos_lines else []
            self.call_from_thread(self._finish_vm_list, result)
        except (OSError, RuntimeError):
            log.debug("Failed to list VMs", exc_info=True)
            self.call_from_thread(self._finish_vm_list, [])

    def _finish_vm_list(self, lines: list[str]) -> None:
        self.state.uninstall_vm_list = lines
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
        self.state.uninstall_purge = cb.value
        hint = self.query_one("#manage_hint", Static)
        if self.state.uninstall_purge:
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
        if self.state.uninstall_running:
            return
        text = self.query_one("#manage_vmid", Input).value.strip()
        try:
            vmid = int(text)
        except ValueError:
            return
        if vmid < MIN_VMID or vmid > MAX_VMID:
            return

        self.state.uninstall_running = True
        self.state.uninstall_done = False
        self.state.uninstall_log = []
        self.query_one("#manage_destroy_btn", Button).disabled = True
        self.query_one("#manage_log").remove_class("hidden")
        self.query_one("#manage_log", Static).update("Removing VM...")
        self.query_one("#manage_result").add_class("hidden")

        Thread(target=self._destroy_worker, args=(vmid,), daemon=True).start()

    def _destroy_worker(self, vmid: int) -> None:
        def on_step(idx: int, total: int, step: PlanStep, result: StepResult | None) -> None:
            self.call_from_thread(self._update_destroy_log, idx, total, step.title, result)

        result, _snapshot = run_destroy_worker(vmid, purge=self.state.uninstall_purge, on_step=on_step)
        self.call_from_thread(self._finish_destroy, result.ok, result.log_path)

    def _update_destroy_log(self, idx: int, total: int, title: str, result: StepResult | None) -> None:
        if result is None:
            self.state.uninstall_log.append(f"Running {idx}/{total}: {title}")
        else:
            self.state.uninstall_log.append(f"{'OK' if result.ok else 'FAIL'} {idx}/{total}: {title}")
        visible = self.state.uninstall_log[-10:]
        self.query_one("#manage_log", Static).update("\n".join(visible))

    def _finish_destroy(self, ok: bool, log_path: Path) -> None:
        self.state.uninstall_running = False
        self.state.uninstall_done = True
        self.state.uninstall_ok = ok
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

    # ── Preflight Worker ────────────────────────────────────────────

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

    # ── Detection Helpers ───────────────────────────────────────────
    # These thin wrappers are intentionally kept as mockable seams for tests.

    def _detect_storage_targets(self) -> list[str]:
        return detect_storage_targets()

    def _detect_next_vmid(self) -> int:
        return detect_next_vmid()

    # ── UI Helpers ──────────────────────────────────────────────────

    def _set_input_value(self, selector: str, value: str) -> None:
        widget = self.query_one(selector, Input)
        widget.value = value
        widget.cursor_position = len(value)
        widget.refresh(layout=True)

    def _update_step_bar(self) -> None:
        step_labels = ["Preflight", "OS", "Storage", "Config", "Dry Run", "Install"]
        parts: list[str] = []
        for idx in range(6):
            num = idx + 1
            name = step_labels[idx]
            if num < self.current_step:
                parts.append(f"[x] {num}.{name}")
            elif num == self.current_step:
                parts.append(f"[>] {num}.{name}")
            else:
                parts.append(f"[ ] {num}.{name}")
        self.query_one("#step_bar", Static).update("  ".join(parts))

    def _append_log(self, selector: str, line: str) -> None:
        self.state.apply_log.append(line)
        widget = self.query_one(selector, Static)
        # Keep rolling window
        visible = self.state.apply_log[-15:]
        widget.update("\n".join(visible))


def run() -> None:
    NextApp().run()
