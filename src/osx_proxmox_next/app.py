from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import check_output
from threading import Thread

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Button, Header, Input, ProgressBar, Static

from .assets import required_assets
from .defaults import DEFAULT_BRIDGE, DEFAULT_STORAGE, default_disk_gb, detect_cpu_cores, detect_memory_mb
from .domain import SUPPORTED_MACOS, VmConfig, validate_config
from .downloader import DownloadError, DownloadProgress, download_opencore, download_recovery
from .executor import apply_plan
from .planner import PlanStep, build_plan, build_destroy_plan, fetch_vm_info
from .preflight import run_preflight
from .rollback import RollbackSnapshot, create_snapshot, rollback_hints
from .smbios import generate_smbios, SmbiosIdentity


@dataclass
class WizardState:
    selected_os: str = ""
    selected_storage: str = ""
    storage_targets: list[str] = field(default_factory=list)
    # Form
    vmid: int = 900
    name: str = ""
    cores: int = 8
    memory_mb: int = 16384
    disk_gb: int = 128
    bridge: str = "vmbr0"
    storage: str = "local-lvm"
    installer_path: str = ""
    smbios: SmbiosIdentity | None = None
    form_errors: dict[str, str] = field(default_factory=dict)
    # Preflight
    preflight_done: bool = False
    preflight_ok: bool = False
    preflight_checks: list = field(default_factory=list)
    # Downloads
    download_running: bool = False
    download_phase: str = ""
    download_pct: int = 0
    download_errors: list[str] = field(default_factory=list)
    downloads_complete: bool = False
    # Config + Plan
    config: VmConfig | None = None
    plan_steps: list = field(default_factory=list)
    assets_ok: bool = False
    assets_missing: list = field(default_factory=list)
    # Dry run
    dry_run_done: bool = False
    dry_run_ok: bool = False
    apply_running: bool = False
    apply_log: list[str] = field(default_factory=list)
    # Live install
    live_done: bool = False
    live_ok: bool = False
    live_log: Path | None = None
    snapshot: RollbackSnapshot | None = None
    # Manage mode
    manage_mode: bool = False
    uninstall_vm_list: list = field(default_factory=list)
    uninstall_purge: bool = True
    uninstall_log: list[str] = field(default_factory=list)
    uninstall_running: bool = False
    uninstall_done: bool = False
    uninstall_ok: bool = False


class NextApp(App):
    CSS = """
    Screen { background: #0b1118; color: #f6f8fa; }
    Header { background: #103252; color: #f6f8fa; }

    #step_bar {
        dock: top;
        height: 3;
        padding: 0 2;
        background: #0d1722;
        border-bottom: heavy #2f6fa2;
        content-align: center middle;
    }

    #body { height: 1fr; padding: 1 2; overflow-y: auto; }

    .step_container { height: auto; padding: 1; }
    .step_hidden { display: none; }

    .os_card {
        border: round #1f4f7a;
        padding: 1 2;
        margin: 0 1 1 0;
        min-width: 28;
        height: 5;
        content-align: center middle;
    }
    .os_card:hover { border: round #2f6fa2; background: #162433; }
    .os_selected { border: heavy #2ec27e; background: #0f2a1a; }

    .storage_btn { margin: 0 1 1 0; min-width: 18; }
    .storage_selected { border: heavy #2ec27e; }

    #config_grid {
        layout: grid;
        grid-size: 2;
        grid-columns: 20 1fr;
        grid-gutter: 0 1;
        height: auto;
        width: 100%;
    }
    .label { color: #9fc6e8; content-align: right middle; height: 1; }
    Input {
        height: 3;
        color: #f6f8fa;
        background: #162433;
        border: tall #2f6fa2;
    }
    Input:focus { border: tall #2ec27e; background: #1a2c3f; }
    .invalid { border: tall #d44f4f; background: #2a1717; }

    #preflight_badge {
        height: 1;
        margin-bottom: 1;
    }

    #smbios_preview {
        height: auto;
        margin-top: 1;
        border: tall #1f4f7a;
        padding: 0 1;
    }

    #config_summary {
        background: #0d1722;
        border: tall #1f4f7a;
        padding: 1;
        height: auto;
        margin-bottom: 1;
    }

    #download_status {
        height: auto;
        margin-bottom: 1;
    }

    #dry_log, #live_log {
        background: #0d1722;
        border: tall #1f4f7a;
        padding: 1;
        height: 12;
        overflow: auto;
    }

    #result_box {
        border: heavy #2ec27e;
        padding: 1;
        height: auto;
        margin-top: 1;
        content-align: center middle;
    }
    .result_fail { border: heavy #d44f4f; }

    #install_btn {
        height: 5;
        min-width: 40;
        border: heavy #2ec27e;
        content-align: center middle;
    }

    .nav_row { height: auto; margin-top: 1; }
    .nav_row Button { margin-right: 1; min-width: 14; }

    .action_row { height: auto; margin-bottom: 1; }
    .action_row Button { margin-right: 1; min-width: 14; }

    #form_errors {
        height: auto;
        color: #d44f4f;
        margin-bottom: 1;
    }

    .hidden { display: none; }

    .mode_btn { margin: 0 1 1 0; min-width: 18; }
    .mode_active { border: heavy #2ec27e; background: #0f2a1a; }

    #manage_panel { height: auto; padding: 1; }

    #manage_vmid { width: 30; }

    #vm_list_display {
        background: #0d1722;
        border: tall #1f4f7a;
        padding: 1;
        height: 8;
        overflow: auto;
    }

    #manage_log {
        background: #0d1722;
        border: tall #1f4f7a;
        padding: 1;
        height: 8;
        overflow: auto;
    }

    #manage_result {
        border: heavy #2ec27e;
        padding: 1;
        height: auto;
        margin-top: 1;
    }
    .manage_result_fail { border: heavy #d44f4f; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    current_step: reactive[int] = reactive(1)

    def __init__(self) -> None:
        super().__init__()
        self.state = WizardState()
        self.state.storage_targets = self._detect_storage_targets()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="step_bar")

        with Container(id="body"):
            # Step 1 — Choose OS / Manage VMs
            with Vertical(id="step1", classes="step_container"):
                with Horizontal(classes="action_row"):
                    yield Button("Create VM", id="mode_create", classes="mode_btn mode_active")
                    yield Button("Manage VMs", id="mode_manage", classes="mode_btn")
                # Create panel
                with Vertical(id="create_panel"):
                    yield Static("Choose macOS Version")
                    with Horizontal(id="os_cards"):
                        for key, meta in SUPPORTED_MACOS.items():
                            channel = "STABLE" if meta["channel"] == "stable" else "PREVIEW"
                            yield Button(
                                f"{meta['label']}\n{channel}",
                                id=f"os_{key}",
                                classes="os_card",
                            )
                    with Horizontal(classes="nav_row"):
                        yield Button("Next", id="next_btn", disabled=True)
                # Manage panel
                with Vertical(id="manage_panel", classes="hidden"):
                    yield Static("Manage VMs")
                    yield Static("", id="vm_list_display")
                    with Horizontal(classes="action_row"):
                        yield Button("Refresh List", id="manage_refresh_btn")
                    yield Static("VMID to destroy:", classes="label")
                    yield Input(value="", id="manage_vmid", placeholder="e.g. 106")
                    with Horizontal(classes="action_row"):
                        yield Button("Purge disks: ON", id="manage_purge_btn")
                        yield Button("Destroy VM", id="manage_destroy_btn", disabled=True)
                    yield Static("", id="manage_log", classes="hidden")
                    yield Static("", id="manage_result", classes="hidden")

            # Step 2 — Choose Storage
            with Vertical(id="step2", classes="step_container step_hidden"):
                yield Static("Choose Storage Target")
                with Horizontal(id="storage_row"):
                    for idx, target in enumerate(self.state.storage_targets):
                        cls = "storage_btn storage_selected" if idx == 0 else "storage_btn"
                        yield Button(target, id=f"storage_{idx}", classes=cls)
                with Horizontal(classes="nav_row"):
                    yield Button("Back", id="back_btn")
                    yield Button("Next", id="next_btn_2")

            # Step 3 — Configuration
            with Vertical(id="step3", classes="step_container step_hidden"):
                yield Static("VM Configuration")
                yield Static("", id="preflight_badge")
                with Container(id="config_grid"):
                    yield Static("VMID", classes="label")
                    yield Input(value="900", id="vmid")
                    yield Static("VM Name", classes="label")
                    yield Input(value="", id="name")
                    yield Static("CPU Cores", classes="label")
                    yield Input(value="8", id="cores", disabled=True)
                    yield Static("Memory MB", classes="label")
                    yield Input(value="16384", id="memory")
                    yield Static("Disk GB", classes="label")
                    yield Input(value="128", id="disk")
                    yield Static("Bridge", classes="label")
                    yield Input(value=DEFAULT_BRIDGE, id="bridge")
                    yield Static("Storage", classes="label")
                    yield Input(value=DEFAULT_STORAGE, id="storage_input")
                    yield Static("Installer Path", classes="label")
                    yield Input(value="", id="installer_path")
                yield Static("", id="form_errors")
                with Horizontal(classes="action_row"):
                    yield Button("Suggest Defaults", id="suggest_btn")
                    yield Button("Generate SMBIOS", id="smbios_btn")
                yield Static("SMBIOS: not generated yet.", id="smbios_preview")
                with Horizontal(classes="nav_row"):
                    yield Button("Back", id="back_btn_3")
                    yield Button("Next", id="next_btn_3")

            # Step 4 — Review & Dry Run
            with Vertical(id="step4", classes="step_container step_hidden"):
                yield Static("Review & Dry Run")
                yield Static("", id="config_summary")
                yield Static("", id="download_status")
                yield ProgressBar(total=100, show_eta=False, id="download_progress", classes="hidden")
                with Horizontal(classes="action_row"):
                    yield Button("Run Dry Apply", id="dry_run_btn", disabled=True)
                yield ProgressBar(total=1, show_eta=False, id="dry_progress", classes="hidden")
                yield Static("", id="dry_log", classes="hidden")
                with Horizontal(classes="nav_row"):
                    yield Button("Back", id="back_btn_4")
                    yield Button("Next: Install", id="next_btn_4", disabled=True)

            # Step 5 — Install
            with Vertical(id="step5", classes="step_container step_hidden"):
                yield Static("Install macOS")
                yield Button("Install", id="install_btn", classes="hidden")
                yield ProgressBar(total=1, show_eta=False, id="live_progress", classes="hidden")
                yield Static("", id="live_log", classes="hidden")
                yield Static("", id="result_box", classes="hidden")
                with Horizontal(classes="nav_row"):
                    yield Button("Back", id="back_btn_5")

    def on_mount(self) -> None:
        self._update_step_bar()
        if self.state.storage_targets:
            self.state.selected_storage = self.state.storage_targets[0]
        Thread(target=self._preflight_worker, daemon=True).start()

    def watch_current_step(self, old_value: int, new_value: int) -> None:
        for step_num in range(1, 6):
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
                pass
            return

        handlers = {
            "next_btn": lambda: self._go_next(),
            "next_btn_2": lambda: self._go_next(),
            "next_btn_3": lambda: self._go_next(),
            "next_btn_4": lambda: self._go_next(),
            "back_btn": lambda: self._go_back(),
            "back_btn_3": lambda: self._go_back(),
            "back_btn_4": lambda: self._go_back(),
            "back_btn_5": lambda: self._go_back(),
            "suggest_btn": self._apply_host_defaults,
            "smbios_btn": self._generate_smbios,
            "dry_run_btn": self._run_dry_apply,
            "install_btn": self._run_live_install,
            "mode_create": lambda: self._toggle_mode("create"),
            "mode_manage": lambda: self._toggle_mode("manage"),
            "manage_refresh_btn": self._refresh_vm_list,
            "manage_purge_btn": self._toggle_purge,
            "manage_destroy_btn": self._run_destroy,
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

    # ── Navigation ──────────────────────────────────────────────────

    def _go_next(self) -> None:
        step = self.current_step
        if step == 1:
            if not self.state.selected_os:
                return
            self.current_step = 2
        elif step == 2:
            if not self.state.selected_storage:
                return
            self._prefill_form()
            self.current_step = 3
        elif step == 3:
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
            self.current_step = 4
            self._check_and_download_assets()
        elif step == 4:
            if not self.state.dry_run_ok:
                return
            self._prepare_install_step()
            self.current_step = 5

    def _go_back(self) -> None:
        self.current_step = max(1, self.current_step - 1)

    # ── Step 1: OS Selection ────────────────────────────────────────

    def _select_os(self, key: str) -> None:
        self.state.selected_os = key
        self.state.smbios = generate_smbios(key)
        # Update card styles
        for os_key in SUPPORTED_MACOS:
            card = self.query_one(f"#os_{os_key}")
            if os_key == key:
                card.add_class("os_selected")
            else:
                card.remove_class("os_selected")
        # Enable Next
        self.query_one("#next_btn", Button).disabled = False

    # ── Step 2: Storage Selection ───────────────────────────────────

    def _select_storage(self, target: str) -> None:
        self.state.selected_storage = target
        for idx in range(len(self.state.storage_targets)):
            btn = self.query_one(f"#storage_{idx}", Button)
            if self.state.storage_targets[idx] == target:
                btn.add_class("storage_selected")
            else:
                btn.remove_class("storage_selected")

    # ── Step 3: Configuration ───────────────────────────────────────

    def _prefill_form(self) -> None:
        macos = self.state.selected_os
        self._set_input_value("#vmid", str(self._detect_next_vmid()))
        self._set_input_value("#name", f"macos-{macos}")
        self._set_input_value("#cores", str(detect_cpu_cores()))
        self._set_input_value("#memory", str(detect_memory_mb()))
        self._set_input_value("#disk", str(default_disk_gb(macos)))
        self._set_input_value("#bridge", DEFAULT_BRIDGE)
        self._set_input_value("#storage_input", self.state.selected_storage)
        self._set_input_value("#installer_path", "")
        self._update_smbios_preview()
        self._update_preflight_badge()

    def _apply_host_defaults(self) -> None:
        macos = self.state.selected_os or "sequoia"
        self._set_input_value("#vmid", str(self._detect_next_vmid()))
        self._set_input_value("#name", f"macos-{macos}")
        self._set_input_value("#cores", str(detect_cpu_cores()))
        self._set_input_value("#memory", str(detect_memory_mb()))
        self._set_input_value("#disk", str(default_disk_gb(macos)))
        self._set_input_value("#bridge", DEFAULT_BRIDGE)
        self._set_input_value("#storage_input", self.state.selected_storage or DEFAULT_STORAGE)
        if not self.state.smbios:
            self.state.smbios = generate_smbios(macos)
        self._update_smbios_preview()

    def _generate_smbios(self) -> None:
        macos = self.state.selected_os or "sequoia"
        self.state.smbios = generate_smbios(macos)
        self._update_smbios_preview()

    def _update_smbios_preview(self) -> None:
        smbios = self.state.smbios
        if smbios:
            text = f"SMBIOS: serial={smbios.serial}  uuid={smbios.uuid}  model={smbios.model}"
        else:
            text = "SMBIOS: not generated yet."
        self.query_one("#smbios_preview", Static).update(text)

    def _update_preflight_badge(self) -> None:
        if not self.state.preflight_done:
            text = "Preflight: running..."
        elif self.state.preflight_ok:
            text = "Host Ready"
        else:
            fail_names = [c.name for c in self.state.preflight_checks if not c.ok]
            text = f"Preflight: {len(fail_names)} checks failed — {', '.join(fail_names[:3])}"
        self.query_one("#preflight_badge", Static).update(text)

    def _validate_form(self, quiet: bool = False) -> bool:
        errors: dict[str, str] = {}

        vmid_text = self.query_one("#vmid", Input).value.strip()
        name_text = self.query_one("#name", Input).value.strip()
        memory_text = self.query_one("#memory", Input).value.strip()
        disk_text = self.query_one("#disk", Input).value.strip()
        bridge_text = self.query_one("#bridge", Input).value.strip()
        storage_text = self.query_one("#storage_input", Input).value.strip()

        try:
            vmid_val = int(vmid_text)
            if vmid_val < 100 or vmid_val > 999999:
                raise ValueError
        except ValueError:
            errors["vmid"] = "VMID must be 100-999999."

        if len(name_text) < 3:
            errors["name"] = "VM Name must be at least 3 chars."

        try:
            mem_val = int(memory_text)
            if mem_val < 4096:
                raise ValueError
        except ValueError:
            errors["memory"] = "Memory must be >= 4096 MB."

        try:
            disk_val = int(disk_text)
            if disk_val < 64:
                raise ValueError
        except ValueError:
            errors["disk"] = "Disk must be >= 64 GB."

        if not bridge_text.startswith("vmbr"):
            errors["bridge"] = "Bridge should look like vmbr0."

        if not storage_text:
            errors["storage_input"] = "Storage target is required."

        # Apply invalid classes
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

    def _read_form(self) -> VmConfig | None:
        try:
            vmid = int(self.query_one("#vmid", Input).value.strip())
        except ValueError:
            return None

        macos = self.state.selected_os or "sequoia"
        smbios = self.state.smbios

        return VmConfig(
            vmid=vmid,
            name=self.query_one("#name", Input).value.strip(),
            macos=macos,
            cores=int(self.query_one("#cores", Input).value.strip() or "8"),
            memory_mb=int(self.query_one("#memory", Input).value.strip() or "16384"),
            disk_gb=int(self.query_one("#disk", Input).value.strip() or "128"),
            bridge=self.query_one("#bridge", Input).value.strip() or DEFAULT_BRIDGE,
            storage=self.query_one("#storage_input", Input).value.strip() or DEFAULT_STORAGE,
            installer_path=self.query_one("#installer_path", Input).value.strip(),
            smbios_serial=smbios.serial if smbios else "",
            smbios_uuid=smbios.uuid if smbios else "",
            smbios_mlb=smbios.mlb if smbios else "",
            smbios_rom=smbios.rom if smbios else "",
            smbios_model=smbios.model if smbios else "",
        )

    # ── Step 4: Review & Dry Run ────────────────────────────────────

    def _render_config_summary(self) -> None:
        config = self.state.config
        if not config:
            return
        meta = SUPPORTED_MACOS.get(config.macos, {})
        lines = [
            f"Target: {meta.get('label', config.macos)} ({meta.get('channel', '?')})",
            f"VM: {config.vmid} / {config.name}",
            f"CPU: {config.cores} cores | Memory: {config.memory_mb} MB | Disk: {config.disk_gb} GB",
            f"Storage: {config.storage} | Bridge: {config.bridge}",
        ]
        if config.installer_path:
            lines.append(f"Installer: {config.installer_path}")
        lines.append("")
        lines.append(f"Plan: {len(self.state.plan_steps)} steps")
        for idx, step in enumerate(self.state.plan_steps, start=1):
            prefix = "!" if step.risk in {"warn", "action"} else "-"
            lines.append(f"  {idx:02d}. {prefix} {step.title}")
        self.query_one("#config_summary", Static).update("\n".join(lines))

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

    def _download_worker(self, config: VmConfig, missing: list) -> None:
        dest_dir = Path("/var/lib/vz/template/iso")
        errors: list[str] = []

        def on_progress(p: DownloadProgress) -> None:
            if p.total > 0:
                pct = int(p.downloaded * 100 / p.total)
                self.call_from_thread(self._update_download_progress, p.phase, pct)

        for asset in missing:
            if not asset.downloadable:
                continue
            if "OpenCore" in asset.name:
                try:
                    download_opencore(config.macos, dest_dir, on_progress=on_progress)
                except DownloadError as exc:
                    errors.append(f"OpenCore: {exc}")
            elif "recovery" in asset.name.lower() or "installer" in asset.name.lower():  # pragma: no branch
                try:
                    download_recovery(config.macos, dest_dir, on_progress=on_progress)
                except DownloadError as exc:
                    errors.append(f"Recovery: {exc}")

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
            self.state.plan_steps = build_plan(config)
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

        def callback(idx: int, total: int, step: PlanStep, result: object) -> None:
            self.call_from_thread(self._update_dry_progress, idx, total, step.title, result)

        def worker() -> None:
            result = apply_plan(self.state.plan_steps, execute=False, on_step=callback)
            self.call_from_thread(self._finish_dry_apply, result.ok, result.log_path)

        Thread(target=worker, daemon=True).start()

    def _update_dry_progress(self, idx: int, total: int, title: str, result: object) -> None:
        self.query_one("#dry_progress", ProgressBar).update(total=total, progress=idx)
        if result is None:
            self._append_log("#dry_log", f"Running {idx}/{total}: {title}")
        else:
            ok = getattr(result, "ok", False)
            rc = getattr(result, "returncode", 0)
            self._append_log("#dry_log", f"{'OK' if ok else 'FAIL'} {idx}/{total}: {title} (rc={rc})")

    def _finish_dry_apply(self, ok: bool, log_path: Path) -> None:
        self.state.apply_running = False
        self.state.dry_run_done = True
        self.state.dry_run_ok = ok
        if ok:
            self._append_log("#dry_log", f"Dry run complete. Log: {log_path}")
            self.query_one("#next_btn_4", Button).disabled = False
            self.notify("Dry run passed", severity="information")
        else:
            self._append_log("#dry_log", f"Dry run FAILED. Log: {log_path}")
            self.query_one("#dry_run_btn", Button).disabled = False
            self.notify("Dry run failed", severity="error")

    # ── Step 5: Live Install ────────────────────────────────────────

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

        def callback(idx: int, total: int, step: PlanStep, result: object) -> None:
            self.call_from_thread(self._update_live_progress, idx, total, step.title, result)

        def worker() -> None:
            snapshot = create_snapshot(self.state.config.vmid)
            self.state.snapshot = snapshot
            result = apply_plan(self.state.plan_steps, execute=True, on_step=callback)
            self.call_from_thread(self._finish_live_install, result.ok, result.log_path, snapshot)

        Thread(target=worker, daemon=True).start()

    def _update_live_progress(self, idx: int, total: int, title: str, result: object) -> None:
        self.query_one("#live_progress", ProgressBar).update(total=total, progress=idx)
        if result is None:
            self._append_log("#live_log", f"Running {idx}/{total}: {title}")
        else:
            ok = getattr(result, "ok", False)
            rc = getattr(result, "returncode", 0)
            self._append_log("#live_log", f"{'OK' if ok else 'FAIL'} {idx}/{total}: {title} (rc={rc})")

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
            lines = [
                "Install completed successfully!",
                f"Log: {log_path}",
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
            output = check_output(["qm", "list"], text=True, timeout=5.0)
            lines = output.strip().splitlines()
            self.call_from_thread(self._finish_vm_list, lines)
        except Exception:
            self.call_from_thread(self._finish_vm_list, [])

    def _finish_vm_list(self, lines: list[str]) -> None:
        self.state.uninstall_vm_list = lines
        display = self.query_one("#vm_list_display", Static)
        if lines:
            display.update("\n".join(lines[:20]))
        else:
            display.update("No VMs found or qm not available.")

    def _validate_manage_vmid(self) -> None:
        text = self.query_one("#manage_vmid", Input).value.strip()
        btn = self.query_one("#manage_destroy_btn", Button)
        try:
            vmid = int(text)
            btn.disabled = vmid < 100 or vmid > 999999
        except ValueError:
            btn.disabled = True

    def _toggle_purge(self) -> None:
        self.state.uninstall_purge = not self.state.uninstall_purge
        btn = self.query_one("#manage_purge_btn", Button)
        if self.state.uninstall_purge:
            btn.label = "Purge disks: ON"
        else:
            btn.label = "Purge disks: OFF"

    def _run_destroy(self) -> None:
        if self.state.uninstall_running:
            return
        text = self.query_one("#manage_vmid", Input).value.strip()
        try:
            vmid = int(text)
        except ValueError:
            return
        if vmid < 100 or vmid > 999999:
            return

        self.state.uninstall_running = True
        self.state.uninstall_done = False
        self.state.uninstall_log = []
        self.query_one("#manage_destroy_btn", Button).disabled = True
        self.query_one("#manage_log").remove_class("hidden")
        self.query_one("#manage_log", Static).update("Starting destroy...")
        self.query_one("#manage_result").add_class("hidden")

        Thread(target=self._destroy_worker, args=(vmid,), daemon=True).start()

    def _destroy_worker(self, vmid: int) -> None:
        snapshot = create_snapshot(vmid)
        steps = build_destroy_plan(vmid, purge=self.state.uninstall_purge)

        def on_step(idx: int, total: int, step: PlanStep, result: object) -> None:
            self.call_from_thread(self._update_destroy_log, idx, total, step.title, result)

        result = apply_plan(steps, execute=True, on_step=on_step)
        self.call_from_thread(self._finish_destroy, result.ok, result.log_path)

    def _update_destroy_log(self, idx: int, total: int, title: str, result: object) -> None:
        if result is None:
            self.state.uninstall_log.append(f"Running {idx}/{total}: {title}")
        else:
            ok = getattr(result, "ok", False)
            self.state.uninstall_log.append(f"{'OK' if ok else 'FAIL'} {idx}/{total}: {title}")
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
            result_box.update(f"VM destroyed successfully.\nLog: {log_path}")
            self._refresh_vm_list()
        else:
            result_box.add_class("manage_result_fail")
            result_box.update(f"Destroy FAILED.\nLog: {log_path}")

    # ── Preflight Worker ────────────────────────────────────────────

    def _preflight_worker(self) -> None:
        checks = run_preflight()
        self.call_from_thread(self._finish_preflight, checks)

    def _finish_preflight(self, checks: list) -> None:
        self.state.preflight_done = True
        self.state.preflight_checks = checks
        self.state.preflight_ok = all(c.ok for c in checks)
        self._update_preflight_badge()

    # ── Detection Helpers ───────────────────────────────────────────

    def _detect_storage_targets(self) -> list[str]:
        try:
            output = check_output(
                ["pvesm", "status", "-content", "images"], text=True, timeout=2.0
            )
        except Exception:
            return [DEFAULT_STORAGE, "local"]
        targets: list[str] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if parts:
                name = parts[0]
                if name not in targets:
                    targets.append(name)
        if DEFAULT_STORAGE not in targets:
            targets.insert(0, DEFAULT_STORAGE)
        return targets[:5]

    def _detect_next_vmid(self) -> int:
        try:
            output = check_output(
                ["pvesh", "get", "/cluster/nextid"], text=True, timeout=2.0
            ).strip()
            if output.isdigit():
                vmid = int(output)
                if 100 <= vmid <= 999999:
                    return vmid
            parsed = json.loads(output)
            if isinstance(parsed, int) and 100 <= parsed <= 999999:
                return parsed  # pragma: no cover
        except Exception:
            pass

        try:
            output = check_output(["qm", "list"], text=True, timeout=2.0)
            vmids: list[int] = []
            for line in output.splitlines()[1:]:
                parts = line.split()
                if parts and parts[0].isdigit():
                    vmids.append(int(parts[0]))
            next_vmid = (max(vmids) + 1) if vmids else 900
            if next_vmid < 100:
                return 100
            if next_vmid > 999999:
                return 999999
            return next_vmid
        except Exception:
            return 900

    # ── UI Helpers ──────────────────────────────────────────────────

    def _set_input_value(self, selector: str, value: str) -> None:
        widget = self.query_one(selector, Input)
        widget.value = value
        widget.cursor_position = len(value)
        widget.refresh(layout=True)

    def _update_step_bar(self) -> None:
        step_labels = ["OS", "Storage", "Config", "Dry Run", "Install"]
        parts: list[str] = []
        for idx, label in enumerate(range(1, 6)):
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
