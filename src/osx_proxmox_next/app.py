from __future__ import annotations

import json
from pathlib import Path
from subprocess import check_output
from threading import Thread

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Header, Input, ProgressBar, Static

from .assets import required_assets, suggested_fetch_commands
from .defaults import DEFAULT_BRIDGE, DEFAULT_STORAGE, default_disk_gb, detect_cpu_cores, detect_memory_mb
from .diagnostics import build_health_status
from .domain import SUPPORTED_MACOS, VmConfig, validate_config
from .executor import apply_plan
from .planner import PlanStep, build_plan
from .preflight import run_preflight
from .rollback import RollbackSnapshot, create_snapshot, rollback_hints
from .smbios import generate_smbios, SmbiosIdentity


class NextApp(App):
    CSS = """
    Screen { background: #0b1118; color: #f6f8fa; }
    Header, Footer { background: #103252; color: #f6f8fa; }

    #topnav { dock: top; height: 3; padding: 0 1; background: #122033; border-bottom: heavy #2f6fa2; }
    #topnav Button { margin: 0 1; min-width: 14; }

    #body { height: 1fr; padding: 1; }
    .panel { border: round #2f6fa2; padding: 1; height: 1fr; }
    .hidden { display: none; }

    #hero {
        height: 7;
        content-align: center middle;
        border: heavy #2ec27e;
        background: #102133;
        margin-bottom: 1;
    }

    .step {
        border: round #1f4f7a;
        padding: 1;
        margin-bottom: 1;
        height: auto;
    }
    .step_hidden { display: none; }

    #wizard_view { overflow-y: hidden; }

    #basic_grid, #advanced_grid {
        layout: grid;
        grid-size: 2;
        grid-columns: 24 1fr;
        grid-gutter: 0 2;
        height: auto;
        width: 100%;
    }
    #basic_grid {
        margin-bottom: 1;
    }

    .label { color: #9fc6e8; content-align: right middle; height: 1; }
    Input {
        height: 3;
        color: #f6f8fa;
        background: #162433;
        border: tall #2f6fa2;
    }
    Input:focus {
        border: tall #2ec27e;
        background: #1a2c3f;
    }
    .invalid {
        border: tall #d44f4f;
        background: #2a1717;
    }

    #workflow_flow {
        height: 3;
        border: round #2ec27e;
        padding: 0 1;
        content-align: left middle;
        margin-bottom: 1;
    }

    .action_row { height: auto; }
    .action_row Button { margin-right: 1; margin-bottom: 1; min-width: 14; }

    #health_output, #preflight_output_alt {
        background: #0d1722;
        border: tall #1f4f7a;
        padding: 1;
        height: 1fr;
        overflow: auto;
    }
    #plan_output {
        background: #0d1722;
        border: tall #1f4f7a;
        padding: 1;
        height: 5;
        overflow: auto;
    }
    #preflight_summary {
        height: 4;
        border: tall #1f4f7a;
        padding: 0 1;
        content-align: left middle;
    }

    #home_status, #wizard_status {
        height: 4;
        border: round #2f6fa2;
        padding: 0 1;
        content-align: left middle;
    }
    #defaults_preview {
        height: 3;
        border: tall #1f4f7a;
        padding: 0 1;
        content-align: left middle;
        margin-bottom: 1;
    }
    #form_validation {
        height: auto;
        border: tall #1f4f7a;
        padding: 0 1;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("h", "go_home", "Home"),
        ("w", "go_wizard", "Wizard"),
        ("p", "go_preflight", "Preflight"),
        ("b", "prev_step", "Prev Step"),
        ("n", "next_step", "Next Step"),
        ("a", "apply_dry", "Apply Dry"),
        ("A", "apply_live", "Apply Live"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.last_config: VmConfig | None = None
        self.last_steps: list[PlanStep] = []
        self.apply_running = False
        self.plan_output_text = ""
        self.wizard_status_text = "Step 1: Run Preflight."
        self.preflight_has_run = False
        self.preflight_ok = False
        self.workflow_stage = 1
        self.step_page = 1
        self.storage_targets = self._detect_storage_targets()
        self.smbios_identity: SmbiosIdentity | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="topnav"):
            yield Button("Home", id="nav_home")
            yield Button("Wizard", id="nav_wizard")
            yield Button("Preflight", id="nav_preflight")

        with Container(id="body"):
            with Vertical(id="home_view", classes="panel"):
                yield Static("OSX Proxmox Next", id="hero")
                yield Static(
                    "Beginner flow: 1) Preflight  2) Configure  3) Review  4) Dry Apply  5) Live Apply"
                )
                yield Static("Ready.", id="home_status")
                yield Static("", id="health_output")

            with Vertical(id="wizard_view", classes="panel hidden"):
                yield Static("Guided Wizard")
                yield Static("", id="workflow_flow")
                with Horizontal(classes="action_row"):
                    yield Button("Previous Step", id="prev_step")
                    yield Button("Next Step", id="next_step")

                with Vertical(id="step1_section", classes="step"):
                    yield Static("Step 1: Check host readiness")
                    with Horizontal(classes="action_row"):
                        yield Button("Run Preflight", id="run_preflight")
                    yield Static("Preflight not run yet.", id="preflight_summary")
                    with Horizontal(classes="action_row"):
                        yield Button("Next: Step 2", id="goto_step2")

                with Vertical(id="step2_section", classes="step step_hidden"):
                    yield Static("Step 2: Basic VM setup")
                    with Horizontal(classes="action_row", id="step2_macos_row"):
                        yield Button("Sonoma 14", id="macos_sonoma")
                        yield Button("Sequoia 15", id="macos_sequoia")
                        yield Button("Tahoe 26", id="macos_tahoe")
                    with Container(id="basic_grid"):
                        yield Static("VMID", classes="label")
                        yield Input(value=str(self._detect_next_vmid()), id="vmid")
                        yield Static("VM Name", classes="label")
                        yield Input(value="macos-sequoia", id="name")
                        yield Static("macOS", classes="label")
                        yield Input(value="sequoia", id="macos", disabled=True)
                    with Horizontal(classes="action_row", id="storage_buttons"):
                        for idx, target in enumerate(self.storage_targets):
                            yield Button(target, id=f"storage_pick_{idx}")
                    with Horizontal(classes="action_row", id="step2_actions_basic"):
                        yield Button("Use Recommended", id="defaults")
                        yield Button("Show Advanced", id="toggle_advanced")
                        yield Button("Back: Step 1", id="goto_step1")
                        yield Button("Next: Step 3", id="goto_step3")
                    yield Static("Recommended values not applied yet.", id="defaults_preview")
                    yield Static("Form validation: pending.", id="form_validation")

                    with Container(id="advanced_section", classes="hidden"):
                        with Horizontal(classes="action_row"):
                            yield Button("Back To Basic", id="back_basic")
                        with Container(id="advanced_grid"):
                            yield Static("CPU Cores (locked)", classes="label")
                            yield Input(value="8", id="cores", disabled=True)
                            yield Static("Memory MB", classes="label")
                            yield Input(value="16384", id="memory")
                            yield Static("Disk GB", classes="label")
                            yield Input(value="128", id="disk")
                            yield Static("Bridge", classes="label")
                            yield Input(value=DEFAULT_BRIDGE, id="bridge")
                            yield Static("Storage", classes="label")
                            yield Input(value=DEFAULT_STORAGE, id="storage")
                            yield Static("Installer Path", classes="label")
                            yield Input(value="", id="installer_path")
                        with Horizontal(classes="action_row"):
                            yield Button("Generate SMBIOS", id="generate_smbios")
                        yield Static("SMBIOS: not generated yet.", id="smbios_preview")

                with Vertical(id="step3_section", classes="step step_hidden"):
                    yield Static("Step 3: Review and apply")
                    with Horizontal(classes="action_row"):
                        yield Button("Review Checks", id="validate")
                        yield Button("Apply Dry", id="apply_dry")
                        yield Button("Apply Live", id="apply_live")
                        yield Button("Back: Step 2", id="goto_step2")
                    yield ProgressBar(total=1, show_eta=False, id="apply_progress", classes="hidden")
                    yield Static("", id="plan_output", classes="hidden")

                yield Static(self.wizard_status_text, id="wizard_status")

            with Vertical(id="preflight_view", classes="panel hidden"):
                yield Static("Host Preflight")
                yield Button("Run Preflight", id="run_preflight_alt")
                yield Static("", id="preflight_output_alt")

    def on_mount(self) -> None:
        self._show("home_view")
        self._apply_host_defaults(silent=True)
        self._update_defaults_preview()
        self._refresh_health()
        self._set_stage(1)
        self._update_step_nav()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        mapping = {
            "nav_home": self.action_go_home,
            "nav_wizard": self.action_go_wizard,
            "nav_preflight": self.action_go_preflight,
            "run_preflight": self._run_preflight,
            "run_preflight_alt": self._run_preflight_alt,
            "prev_step": self.action_prev_step,
            "next_step": self.action_next_step,
            "goto_step1": lambda: self._show_step_page(1),
            "goto_step2": lambda: self._show_step_page(2),
            "goto_step3": self.action_goto_step3,
            "defaults": self._apply_host_defaults,
            "toggle_advanced": self._toggle_advanced,
            "back_basic": self._toggle_advanced,
            "macos_sonoma": lambda: self._set_macos("sonoma"),
            "macos_sequoia": lambda: self._set_macos("sequoia"),
            "macos_tahoe": lambda: self._set_macos("tahoe"),
            "generate_smbios": self._generate_smbios,
            "validate": self._validate_only,
            "apply_dry": self.action_apply_dry,
            "apply_live": self.action_apply_live,
        }
        if bid.startswith("storage_pick_"):
            try:
                idx = int(bid.split("_")[-1])
                self._set_storage_target(self.storage_targets[idx])
            except (ValueError, IndexError):
                self._set_wizard_status("Invalid storage target selection.")
            return
        handler = mapping.get(bid)
        if handler:
            handler()

    def on_input_changed(self, event: Input.Changed) -> None:
        target_ids = {"vmid", "name", "memory", "disk", "bridge", "storage", "installer_path"}
        if (event.input.id or "") in target_ids:
            self._validate_form_inputs(quiet=True)

    def action_go_home(self) -> None:
        self._show("home_view")
        self._refresh_health()

    def action_go_wizard(self) -> None:
        self._show("wizard_view")
        if not self.preflight_has_run:
            self._run_preflight()
        self._set_stage(self.workflow_stage)
        page = 1 if self.workflow_stage <= 1 else 2 if self.workflow_stage <= 3 else 3
        self._show_step_page(page)

    def action_go_preflight(self) -> None:
        self._show("preflight_view")
        self._run_preflight_alt()

    def action_prev_step(self) -> None:
        self._show("wizard_view")
        self._show_step_page(max(1, self.step_page - 1))

    def action_next_step(self) -> None:
        self._show("wizard_view")
        if self.step_page == 2:
            self.action_goto_step3()
            return
        self._show_step_page(min(3, self.step_page + 1))

    def action_goto_step3(self) -> None:
        self._show("wizard_view")
        if not self._validate_form_inputs(quiet=False):
            self._set_wizard_status("Fix highlighted fields before continuing.")
            return
        self._show_step_page(3)

    def action_generate_plan(self) -> None:
        self._autofill_tahoe_installer_path()
        if not self._validate_form_inputs(quiet=False):
            self._set_wizard_status("Fix highlighted fields before review/apply.")
            self._set_stage(2)
            self._show_step_page(2)
            return
        config = self._read_form()
        if not config:
            return

        issues = validate_config(config)
        if issues:
            self._handle_validation_issues(issues)
            return

        self.last_config = config
        self.last_steps = build_plan(config)

        meta = SUPPORTED_MACOS[config.macos]
        lines = [f"Target: {meta['label']} ({meta['channel']})", f"VM: {config.vmid} / {config.name}", ""]
        for idx, step in enumerate(self.last_steps, start=1):
            prefix = "!" if step.risk in {"warn", "action"} else "-"
            lines.append(f"{idx:02d}. {prefix} {step.title}")
            lines.append(f"    {step.command}")

        self._set_plan_output("\n".join(lines))
        self.query_one("#apply_progress").remove_class("hidden")
        self.query_one("#plan_output").remove_class("hidden")
        self._set_wizard_status("Review complete. Next: run Apply Dry.")
        self._set_stage(3)
        self._show_step_page(3)
        self._check_assets()

    def action_apply_dry(self) -> None:
        self._apply(execute=False)

    def action_apply_live(self) -> None:
        self._apply(execute=True)

    def _apply(self, execute: bool) -> None:
        if self.apply_running:
            self._set_wizard_status("Apply already running.")
            return

        if not self.preflight_has_run:
            self._run_preflight()
        if execute and not self.preflight_ok:
            self._set_wizard_status("Live apply blocked: preflight has failures. Fix those first.")
            return
        if not self._validate_form_inputs(quiet=False):
            self._set_wizard_status("Fix highlighted fields before apply.")
            self._set_stage(2)
            self._show_step_page(2)
            return
        config = self._read_form()
        if not config:
            return
        if execute:
            missing = [asset for asset in required_assets(config) if not asset.ok]
            if missing:
                names = ", ".join(asset.name for asset in missing)
                self._set_wizard_status(f"Live apply blocked: missing assets ({names}).")
                self.notify("Missing required ISO assets for live apply", severity="error")
                self._set_stage(3)
                self._show_step_page(3)
                return

        if not self.last_config or not self.last_steps:
            self.action_generate_plan()
            if not self.last_config or not self.last_steps:
                return

        self.apply_running = True
        self.query_one("#apply_progress").remove_class("hidden")
        self.query_one("#plan_output").remove_class("hidden")
        self.query_one("#apply_progress", ProgressBar).update(total=len(self.last_steps), progress=0)
        self._set_plan_output("")
        self._set_wizard_status(f"Running {'live' if execute else 'dry'} apply...")

        def callback(idx: int, total: int, step: PlanStep, result: object) -> None:
            self.call_from_thread(self._update_apply_progress, idx, total, step.title, result)

        def worker() -> None:
            snapshot: RollbackSnapshot | None = None
            if execute and self.last_config:
                snapshot = create_snapshot(self.last_config.vmid)
            result = apply_plan(self.last_steps, execute=execute, on_step=callback)
            self.call_from_thread(self._finish_apply, execute, result.ok, result.log_path, snapshot)

        Thread(target=worker, daemon=True).start()

    def _toggle_advanced(self) -> None:
        section = self.query_one("#advanced_section")
        button = self.query_one("#toggle_advanced", Button)
        basic_grid = self.query_one("#basic_grid")
        basic_actions = self.query_one("#step2_actions_basic")
        basic_macos = self.query_one("#step2_macos_row")
        basic_storage = self.query_one("#storage_buttons")
        preview = self.query_one("#defaults_preview")
        if section.has_class("hidden"):
            section.remove_class("hidden")
            basic_grid.add_class("hidden")
            basic_actions.add_class("hidden")
            basic_macos.add_class("hidden")
            basic_storage.add_class("hidden")
            preview.add_class("hidden")
            button.label = "Hide Advanced"
            self._set_wizard_status("Advanced options shown. You can keep defaults if unsure.")
        else:
            section.add_class("hidden")
            basic_grid.remove_class("hidden")
            basic_actions.remove_class("hidden")
            basic_macos.remove_class("hidden")
            basic_storage.remove_class("hidden")
            preview.remove_class("hidden")
            button.label = "Show Advanced"
            self._set_wizard_status("Advanced options hidden.")

    def _set_macos(self, macos: str) -> None:
        self.query_one("#macos", Input).value = macos
        if self.query_one("#name", Input).value.startswith("macos-"):
            self.query_one("#name", Input).value = f"macos-{macos}"
        self.smbios_identity = generate_smbios(macos)
        self._update_smbios_preview()
        self._apply_host_defaults(silent=True)
        self._update_defaults_preview()
        self._autofill_tahoe_installer_path()
        self._set_wizard_status(f"macOS target set to {SUPPORTED_MACOS[macos]['label']}.")

    def _check_assets(self) -> None:
        config = self._read_form()
        if not config:
            return
        missing = 0
        first_hint = ""
        for item in required_assets(config):
            if not item.ok:
                missing += 1
                if not first_hint:
                    first_hint = f"{item.name}: {item.hint}"

        if missing == 0:
            self._set_wizard_status("Assets check OK. Ready for Apply Dry.")
        else:
            extra = suggested_fetch_commands(config)
            tail = f" ({extra[0]})" if extra else ""
            self._set_wizard_status(f"Assets missing: {missing}. {first_hint}{tail}")

    def _refresh_health(self) -> None:
        health = build_health_status()
        checks = run_preflight()
        self.query_one("#health_output", Static).update(self._render_health_dashboard(health.summary, checks))
        self.query_one("#home_status", Static).update("Use Wizard to start.")

    def _validate_only(self) -> None:
        config = self._read_form()
        if not config:
            return
        issues = validate_config(config)
        if issues:
            self._handle_validation_issues(issues)
            return
        self._check_assets()
        self.action_generate_plan()
        self._set_wizard_status("Validation OK. Review prepared automatically. Next: Apply Dry.")
        self._set_stage(3)
        self._show_step_page(3)

    def _handle_validation_issues(self, issues: list[str]) -> None:
        joined = "\n".join(issues).lower()
        tahoe_missing_installer = "tahoe requires installer_path" in joined
        if tahoe_missing_installer:
            if self._autofill_tahoe_installer_path():
                self._set_wizard_status("Tahoe installer detected automatically. Retry Apply/Review.")
                return
            self._set_stage(2)
            self._show_step_page(2)
            section = self.query_one("#advanced_section")
            if section.has_class("hidden"):
                self._toggle_advanced()
            self._set_wizard_status(
                "Tahoe needs a full installer ISO. In Step 2 Advanced, set Installer Path "
                "to a Tahoe full image (example: /var/lib/vz/template/iso/macos-tahoe-full.iso)."
            )
            self.notify("Set Tahoe Installer Path in Advanced options", severity="warning")
            return

        self._set_wizard_status("Validation failed:\n" + "\n".join(f"- {issue}" for issue in issues))
        self.notify("Validation failed", severity="error")

    def _run_preflight(self) -> None:
        checks = run_preflight()
        self.preflight_has_run = True
        self.preflight_ok = all(c.ok for c in checks)
        ok_count = sum(1 for c in checks if c.ok)
        fail_names = [c.name for c in checks if not c.ok]
        if fail_names:
            summary = f"{ok_count}/{len(checks)} passed. Failing: {', '.join(fail_names[:2])}"
        else:
            summary = f"{ok_count}/{len(checks)} passed. Host ready."
        self.query_one("#preflight_summary", Static).update(summary)

        lines = [f"Preflight: {ok_count}/{len(checks)} checks passed", ""]
        for check in checks:
            lines.append(f"[{'OK' if check.ok else 'FAIL'}] {check.name}")
            lines.append(f"  {check.details}")
        if self.preflight_ok:
            self._set_wizard_status("Step 1 complete. Step 2: configure VM fields.")
            self._set_stage(2)
            self._show_step_page(2)
        else:
            self._set_wizard_status("Preflight has failures. Fix those before Apply Live.")
            self._set_stage(1)

    def _run_preflight_alt(self) -> None:
        checks = run_preflight()
        self.query_one("#preflight_output_alt", Static).update(self._render_preflight_report(checks))

    def _read_form(self) -> VmConfig | None:
        self._autofill_tahoe_installer_path()
        try:
            vmid = int(self.query_one("#vmid", Input).value.strip())
        except ValueError:
            self._set_wizard_status("VMID must be a number.")
            return None

        cores = int(self.query_one("#cores", Input).value.strip() or "8")
        memory_mb = int(self.query_one("#memory", Input).value.strip() or "16384")
        disk_gb = int(self.query_one("#disk", Input).value.strip() or "128")

        smbios = self.smbios_identity
        return VmConfig(
            vmid=vmid,
            name=self.query_one("#name", Input).value.strip(),
            macos=self.query_one("#macos", Input).value.strip().lower(),
            cores=cores,
            memory_mb=memory_mb,
            disk_gb=disk_gb,
            bridge=self.query_one("#bridge", Input).value.strip() or DEFAULT_BRIDGE,
            storage=self.query_one("#storage", Input).value.strip() or DEFAULT_STORAGE,
            installer_path=self.query_one("#installer_path", Input).value.strip(),
            smbios_serial=smbios.serial if smbios else "",
            smbios_uuid=smbios.uuid if smbios else "",
            smbios_mlb=smbios.mlb if smbios else "",
            smbios_rom=smbios.rom if smbios else "",
            smbios_model=smbios.model if smbios else "",
        )

    def _apply_host_defaults(self, silent: bool = False) -> None:
        before = {
            "vmid": self.query_one("#vmid", Input).value,
            "name": self.query_one("#name", Input).value,
            "macos": self.query_one("#macos", Input).value,
            "cores": self.query_one("#cores", Input).value,
            "memory": self.query_one("#memory", Input).value,
            "disk": self.query_one("#disk", Input).value,
            "bridge": self.query_one("#bridge", Input).value,
            "storage": self.query_one("#storage", Input).value,
        }
        macos = self.query_one("#macos", Input).value.strip().lower() or "sequoia"
        self._set_input_value("#vmid", str(self._detect_next_vmid()))
        self._set_input_value("#macos", macos)
        self._set_input_value("#name", f"macos-{macos}")

        self._set_input_value("#cores", str(detect_cpu_cores()))
        self._set_input_value("#memory", str(detect_memory_mb()))
        self._set_input_value("#disk", str(default_disk_gb(macos)))
        self._set_input_value("#bridge", DEFAULT_BRIDGE)
        self._set_input_value("#storage", DEFAULT_STORAGE)
        after = {
            "vmid": self.query_one("#vmid", Input).value,
            "name": self.query_one("#name", Input).value,
            "macos": self.query_one("#macos", Input).value,
            "cores": self.query_one("#cores", Input).value,
            "memory": self.query_one("#memory", Input).value,
            "disk": self.query_one("#disk", Input).value,
            "bridge": self.query_one("#bridge", Input).value,
            "storage": self.query_one("#storage", Input).value,
        }
        changed = [k for k in after if after[k] != before[k]]
        if not self.smbios_identity:
            self.smbios_identity = generate_smbios(macos)
            self._update_smbios_preview()
        self._update_defaults_preview()
        if not silent:
            labels = {
                "vmid": "VMID",
                "name": "VM Name",
                "macos": "macOS",
                "cores": "CPU",
                "memory": "Memory",
                "disk": "Disk",
                "bridge": "Bridge",
                "storage": "Storage",
            }
            if changed:
                details = ", ".join(labels[key] for key in changed)
                self._set_wizard_status(f"Recommended settings applied: {details}. Next: Validate.")
            else:
                self._set_wizard_status("Recommended settings already active. Next: Validate.")
            self.notify("Recommended settings applied")
            self._set_stage(2)

    def _update_defaults_preview(self) -> None:
        preview = (
            f"Applied: vmid={self.query_one('#vmid', Input).value.strip()}  "
            f"name={self.query_one('#name', Input).value.strip()}  "
            f"macos={self.query_one('#macos', Input).value.strip()}  "
            f"cpu={self.query_one('#cores', Input).value.strip()}  "
            f"mem={self.query_one('#memory', Input).value.strip()}MB  "
            f"disk={self.query_one('#disk', Input).value.strip()}GB  "
            f"storage={self.query_one('#storage', Input).value.strip()}"
        )
        self.query_one("#defaults_preview", Static).update(preview)

    def _generate_smbios(self) -> None:
        macos = self.query_one("#macos", Input).value.strip().lower() or "sequoia"
        self.smbios_identity = generate_smbios(macos)
        self._update_smbios_preview()
        self._set_wizard_status("SMBIOS identity generated.")

    def _update_smbios_preview(self) -> None:
        if self.smbios_identity:
            text = (
                f"SMBIOS: serial={self.smbios_identity.serial}  "
                f"uuid={self.smbios_identity.uuid}  "
                f"model={self.smbios_identity.model}"
            )
        else:
            text = "SMBIOS: not generated yet."
        self.query_one("#smbios_preview", Static).update(text)

    def _set_input_value(self, selector: str, value: str) -> None:
        widget = self.query_one(selector, Input)
        widget.value = value
        widget.cursor_position = len(value)
        widget.refresh(layout=True)

    def _set_storage_target(self, target: str) -> None:
        self._set_input_value("#storage", target)
        self._update_defaults_preview()
        self._validate_form_inputs(quiet=True)
        self._set_wizard_status(f"Storage target set to {target}.")

    def _autofill_tahoe_installer_path(self) -> bool:
        if self.query_one("#macos", Input).value.strip().lower() != "tahoe":
            return False
        current = self.query_one("#installer_path", Input).value.strip()
        if current:
            return True
        candidate = self._find_tahoe_installer_path()
        if not candidate:
            return False
        self._set_input_value("#installer_path", candidate)
        self._set_wizard_status(f"Tahoe installer auto-detected: {candidate}")
        return True

    def _find_tahoe_installer_path(self) -> str:
        search_dirs = [
            Path("/var/lib/vz/template/iso"),
            Path("/var/lib/vz/snippets"),
        ]
        mnt_pve = Path("/mnt/pve")
        if mnt_pve.exists():
            for entry in sorted(mnt_pve.iterdir()):
                iso_path = entry / "template" / "iso"
                if iso_path.exists():
                    search_dirs.append(iso_path)
        patterns = [
            "*tahoe*full*.iso",
            "*tahoe*.iso",
            "*26*.iso",
            "*InstallAssistant*.iso",
        ]
        for root in search_dirs:
            if not root.exists():
                continue
            for pattern in patterns:
                matches = sorted(root.glob(pattern))
                for path in matches:
                    if path.is_file():
                        return str(path)
        return ""

    def _detect_storage_targets(self) -> list[str]:
        try:
            output = check_output(["pvesm", "status", "-content", "images"], text=True, timeout=2.0)
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
            output = check_output(["pvesh", "get", "/cluster/nextid"], text=True, timeout=2.0).strip()
            if output.isdigit():
                vmid = int(output)
                if 100 <= vmid <= 999999:
                    return vmid
            parsed = json.loads(output)
            if isinstance(parsed, int) and 100 <= parsed <= 999999:
                return parsed  # pragma: no cover – isdigit() already handles pure-digit strings
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

    def _render_health_dashboard(self, summary: str, checks: list[object]) -> str:
        ok_count = sum(1 for c in checks if getattr(c, "ok", False))
        total = len(checks) or 1
        ratio = ok_count / total
        meter_width = 28
        filled = int(ratio * meter_width)
        meter = f"[{'#' * filled}{'.' * (meter_width - filled)}]"
        grade = "READY" if ok_count == total else "ATTENTION"
        lines = [
            "Host Health Dashboard",
            "",
            f"Status : {grade}",
            f"Score  : {ok_count}/{total} {meter}",
            f"Summary: {summary}",
            "",
            "Checks",
            "------",
        ]
        for c in checks:
            mark = "PASS" if getattr(c, "ok", False) else "FAIL"
            name = getattr(c, "name", "unknown")
            details = getattr(c, "details", "")
            lines.append(f"{mark:<4} | {name}")
            lines.append(f"      {details}")
        return "\n".join(lines)

    def _render_preflight_report(self, checks: list[object]) -> str:
        ok_count = sum(1 for c in checks if getattr(c, "ok", False))
        total = len(checks)
        lines = [
            "Preflight Report",
            "",
            f"Result: {ok_count}/{total} checks passed",
            "",
            "Detailed Results",
            "----------------",
        ]
        for idx, check in enumerate(checks, start=1):
            mark = "PASS" if getattr(check, "ok", False) else "FAIL"
            name = getattr(check, "name", "unknown")
            details = getattr(check, "details", "")
            lines.append(f"{idx:02d}. {mark}  {name}")
            lines.append(f"    {details}")
        return "\n".join(lines)

    def _set_wizard_status(self, text: str) -> None:
        self.wizard_status_text = text
        self.query_one("#wizard_status", Static).update(text)

    def _validate_form_inputs(self, quiet: bool = False) -> bool:
        self._autofill_tahoe_installer_path()
        errors: list[str] = []

        vmid_text = self.query_one("#vmid", Input).value.strip()
        name_text = self.query_one("#name", Input).value.strip()
        memory_text = self.query_one("#memory", Input).value.strip()
        disk_text = self.query_one("#disk", Input).value.strip()
        bridge_text = self.query_one("#bridge", Input).value.strip()
        storage_text = self.query_one("#storage", Input).value.strip()
        installer_text = self.query_one("#installer_path", Input).value.strip()
        macos_text = self.query_one("#macos", Input).value.strip().lower()

        invalid: dict[str, bool] = {
            "#vmid": False,
            "#name": False,
            "#memory": False,
            "#disk": False,
            "#bridge": False,
            "#storage": False,
            "#installer_path": False,
        }

        try:
            vmid_value = int(vmid_text)
            if vmid_value < 100 or vmid_value > 999999:
                raise ValueError
        except ValueError:
            invalid["#vmid"] = True
            errors.append("VMID must be 100-999999.")

        if len(name_text) < 3:
            invalid["#name"] = True
            errors.append("VM Name must be at least 3 chars.")

        try:
            memory_value = int(memory_text)
            if memory_value < 4096:
                raise ValueError
        except ValueError:
            invalid["#memory"] = True
            errors.append("Memory must be >= 4096 MB.")

        try:
            disk_value = int(disk_text)
            if disk_value < 64:
                raise ValueError
        except ValueError:
            invalid["#disk"] = True
            errors.append("Disk must be >= 64 GB.")

        if not bridge_text.startswith("vmbr"):
            invalid["#bridge"] = True
            errors.append("Bridge should look like vmbr0.")

        if not storage_text:
            invalid["#storage"] = True
            errors.append("Storage target is required.")

        if macos_text == "tahoe" and not installer_text:
            invalid["#installer_path"] = True
            errors.append("Tahoe requires a full installer path.")

        for selector, is_invalid in invalid.items():
            widget = self.query_one(selector, Input)
            if is_invalid:
                widget.add_class("invalid")
            else:
                widget.remove_class("invalid")

        if errors:
            self.query_one("#form_validation", Static).update("Form validation: " + " ".join(errors))
            if not quiet:
                self.notify("Form has invalid fields", severity="warning")
            return False

        self.query_one("#form_validation", Static).update("Form validation: OK.")
        return True

    def _set_plan_output(self, text: str) -> None:
        self.plan_output_text = text
        self.query_one("#plan_output", Static).update(text)

    def _append_plan_output(self, line: str) -> None:
        lines = [ln for ln in self.plan_output_text.splitlines() if ln.strip()]
        lines.append(line)
        # Keep a short rolling window so users always see latest progress.
        self.plan_output_text = "\n".join(lines[-12:])
        self.query_one("#plan_output", Static).update(self.plan_output_text)

    def _set_stage(self, stage: int) -> None:
        self.workflow_stage = stage
        labels = [
            "1 Preflight",
            "2 Configure",
            "3 Review",
            "4 Dry",
            "5 Live",
        ]
        chips: list[str] = []
        for idx, label in enumerate(labels, start=1):
            if idx < stage:
                chips.append(f"[x] {label}")
            elif idx == stage:
                chips.append(f"[>] {label}")
            else:
                chips.append(f"[ ] {label}")
        self.query_one("#workflow_flow", Static).update("  ".join(chips))

    def _show_step_page(self, step_page: int) -> None:
        self.step_page = step_page
        sections = {
            1: self.query_one("#step1_section"),
            2: self.query_one("#step2_section"),
            3: self.query_one("#step3_section"),
        }
        for key, widget in sections.items():
            if key == step_page:
                widget.remove_class("step_hidden")
            else:
                widget.add_class("step_hidden")
        self._update_step_nav()

    def _update_step_nav(self) -> None:
        prev_button = self.query_one("#prev_step", Button)
        next_button = self.query_one("#next_step", Button)
        prev_button.disabled = self.step_page <= 1
        next_button.disabled = self.step_page >= 3

    def _update_apply_progress(self, idx: int, total: int, title: str, result: object) -> None:
        self.query_one("#apply_progress", ProgressBar).update(total=total, progress=idx)
        if result is None:
            self._set_wizard_status(f"Running {idx}/{total}: {title}")
        else:
            ok = getattr(result, "ok", False)
            returncode = getattr(result, "returncode", 0)
            self._append_plan_output(f"{'OK' if ok else 'FAIL'} {idx}/{total}: {title} (rc={returncode})")

    def _finish_apply(
        self,
        execute: bool,
        ok: bool,
        log_path: Path,
        snapshot: RollbackSnapshot | None,
    ) -> None:
        self.apply_running = False
        if ok:
            self._set_wizard_status(
                f"{'Live' if execute else 'Dry'} apply completed. Log: {log_path}."
            )
            self._append_plan_output("All steps completed successfully.")
            self._append_plan_output(f"Completed. Log: {log_path}")
            if execute:
                self._append_plan_output("")
                self._append_plan_output("If this saved you time: https://ko-fi.com/lucidfabrics")
            self._set_stage(4 if not execute else 5)
            return

        hints = ""
        if snapshot is not None:
            hints = "\n".join(rollback_hints(snapshot))
        self._set_wizard_status(f"Apply failed. Log: {log_path}\n{hints}")
        self._append_plan_output(f"FAILED. Log: {log_path}")

    def _show(self, visible_id: str) -> None:
        for view_id in ("home_view", "wizard_view", "preflight_view"):
            view = self.query_one(f"#{view_id}")
            if view_id == visible_id:
                view.remove_class("hidden")
            else:
                view.add_class("hidden")


def run() -> None:
    NextApp().run()
