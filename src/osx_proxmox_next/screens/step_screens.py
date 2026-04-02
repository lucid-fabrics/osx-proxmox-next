from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, ProgressBar, Static

from ..defaults import DEFAULT_BRIDGE, DEFAULT_ISO_DIR, DEFAULT_MEMORY_MB, DEFAULT_STORAGE, CpuInfo
from ..domain import DEFAULT_VMID, SUPPORTED_MACOS

__all__ = [
    "compose_step1",
    "compose_step2",
    "compose_step3",
    "compose_step4",
    "compose_step5",
    "compose_step6",
]


def compose_step1() -> ComposeResult:
    with Vertical(id="step1", classes="step_container"):
        yield Static("Host Preflight Checks")
        yield Static("Checking...", id="preflight_checks")
        with Horizontal(classes="nav_row"):
            yield Button("Continue", id="preflight_next_btn", disabled=True)
            yield Button("Exit", id="exit_btn")


def compose_step2() -> ComposeResult:
    with Vertical(id="step2", classes="step_container step_hidden"):
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
                yield Button("Back", id="back_btn_2")
                yield Button("Next", id="next_btn", disabled=True)
                yield Button("Exit", id="exit_btn_2")
        # Manage panel
        with Vertical(id="manage_panel", classes="hidden"):
            yield Static("Manage VMs")
            yield Static("", id="vm_list_display")
            with Horizontal(classes="action_row"):
                yield Button("Refresh List", id="manage_refresh_btn")
            yield Static("Enter the VM ID to remove:", id="manage_vmid_label")
            yield Input(value="", id="manage_vmid", placeholder="e.g. 106")
            with Horizontal(classes="action_row"):
                yield Checkbox("Delete disk images", value=True, id="manage_purge_cb")
                yield Button("Remove VM", id="manage_destroy_btn", disabled=True)
            yield Static(
                "This will stop the VM, remove its configuration,\n"
                "and delete all associated disk images.",
                id="manage_hint",
                classes="hint",
            )
            yield Static("", id="manage_log", classes="hidden")
            yield Static("", id="manage_result", classes="hidden")
            # ── Edit VM ──────────────────────────────────────────────
            yield Static("Edit VM", classes="manage_section_header")
            yield Static("VM ID to edit:", id="edit_vmid_label")
            yield Input(value="", id="edit_vmid", placeholder="e.g. 106")
            with Vertical(id="edit_form", classes="hidden"):
                with Container(id="edit_grid"):
                    yield Static("Name", classes="label")
                    yield Input(value="", id="edit_name", placeholder="leave blank to keep")
                    yield Static("CPU Cores", classes="label")
                    yield Input(value="", id="edit_cores", placeholder="leave blank to keep")
                    yield Static("Memory MB", classes="label")
                    yield Input(value="", id="edit_memory", placeholder="leave blank to keep")
                    yield Static("Bridge", classes="label")
                    yield Input(value="", id="edit_bridge", placeholder="leave blank to keep")
                    yield Static("NIC Model", classes="label")
                    yield Input(value="vmxnet3", id="edit_nic_model", placeholder="vmxnet3 or e1000")
                    yield Static("Add Disk GB", classes="label")
                    yield Input(value="", id="edit_disk_add", placeholder="GB to add, e.g. 64")
                    yield Static("Disk Name", classes="label")
                    yield Input(value="virtio0", id="edit_disk_name", placeholder="virtio0, sata0, …")
                with Horizontal(classes="action_row"):
                    yield Checkbox("Start VM after", value=False, id="edit_start_after_cb")
                    yield Button("Apply Changes", id="edit_apply_btn", disabled=True)
            yield Static("", id="edit_log", classes="hidden")
            yield Static("", id="edit_result", classes="hidden")


def compose_step3(storage_targets: list[str]) -> ComposeResult:
    with Vertical(id="step3", classes="step_container step_hidden"):
        yield Static("Choose Storage Target")
        with Horizontal(id="storage_row"):
            for idx, target in enumerate(storage_targets):
                cls = "storage_btn storage_selected" if idx == 0 else "storage_btn"
                yield Button(target, id=f"storage_{idx}", classes=cls)
        with Horizontal(classes="nav_row"):
            yield Button("Back", id="back_btn_3")
            yield Button("Next", id="next_btn_3")
            yield Button("Exit", id="exit_btn_3")


def _compose_step4_vm_fields() -> ComposeResult:
    """Yield the basic VM configuration input grid."""
    with Container(id="config_grid"):
        yield Static("VMID", classes="label")
        yield Input(value=str(DEFAULT_VMID), id="vmid")
        yield Static("VM Name", classes="label")
        yield Input(value="", id="name")
        yield Static("CPU Cores", classes="label")
        yield Input(value="8", id="cores", disabled=True)
        yield Static("Memory MB", classes="label")
        yield Input(value=str(DEFAULT_MEMORY_MB), id="memory")
        yield Static("Disk GB", classes="label")
        yield Input(value="128", id="disk")
        yield Static("Bridge", classes="label")
        yield Input(value=DEFAULT_BRIDGE, id="bridge")
        yield Static("Storage", classes="label")
        yield Input(value=DEFAULT_STORAGE, id="storage_input")
        yield Static("ISO Storage", classes="label")
        yield Input(value=DEFAULT_ISO_DIR, id="iso_dir")
        yield Static("Installer Path", classes="label")
        yield Input(value="", id="installer_path")
        yield Static("Existing UUID (optional)", classes="label")
        yield Input(value="", id="existing_uuid", placeholder="Preserve existing VM UUID")


def _compose_step4_cpu_network(cpu_info: CpuInfo) -> ComposeResult:
    """Yield CPU mode and network adapter checkboxes with their hint statics."""
    with Horizontal(classes="action_row"):
        yield Checkbox(
            "Use Penryn CPU mode (recommended for older Intel CPUs)",
            id="penryn_cb",
            value=cpu_info.needs_penryn,
        )
    yield Static(
        "Older Intel CPU detected (pre-Skylake). Penryn mode improves macOS install stability on this hardware. (Xeon CPUs are automatically excluded — they use -cpu host.)",
        id="penryn_hint",
        classes="penryn_hint" + ("" if cpu_info.needs_penryn else " step_hidden"),
    )
    _e1000_default = cpu_info.is_xeon or cpu_info.needs_penryn
    with Horizontal(classes="action_row"):
        yield Checkbox(
            "Use e1000 network adapter (recommended for Xeon / older Intel — no kext needed)",
            id="e1000_cb",
            value=_e1000_default,
        )
    yield Static(
        "Xeon or legacy Intel CPU detected. e1000 has a native macOS driver and avoids slow recovery downloads caused by vmxnet3 kext not loading during install.",
        id="e1000_hint",
        classes="penryn_hint" + ("" if _e1000_default else " step_hidden"),
    )


def _compose_step4_apple_services() -> ComposeResult:
    """Yield Apple services checkbox and its conditional fields."""
    with Horizontal(classes="action_row"):
        yield Checkbox("Enable Apple Services (iMessage, FaceTime, iCloud)", id="apple_services_cb")
    with Container(id="apple_services_fields", classes="hidden"):
        yield Static("Custom vmgenid (optional)", classes="label")
        yield Input(value="", id="custom_vmgenid", placeholder="Auto-generated if empty")
        yield Static("Custom MAC (optional)", classes="label")
        yield Input(value="", id="custom_mac", placeholder="Auto-generated if empty")


def compose_step4(cpu_info: CpuInfo) -> ComposeResult:
    with Vertical(id="step4", classes="step_container step_hidden"):
        yield Static("VM Configuration")
        yield from _compose_step4_vm_fields()
        yield from _compose_step4_apple_services()
        yield from _compose_step4_cpu_network(cpu_info)
        yield Static("", id="form_errors")
        with Horizontal(classes="action_row"):
            yield Button("Suggest Defaults", id="suggest_btn")
            yield Button("Generate SMBIOS", id="smbios_btn")
        yield Static("SMBIOS: not generated yet.", id="smbios_preview")
        with Horizontal(classes="nav_row"):
            yield Button("Back", id="back_btn_4")
            yield Button("Next", id="next_btn_4")
            yield Button("Exit", id="exit_btn_4")


def compose_step5() -> ComposeResult:
    with Vertical(id="step5", classes="step_container step_hidden"):
        yield Static("Review & Dry Run")
        yield Static("", id="config_summary")
        yield Static("", id="download_status")
        yield ProgressBar(total=100, show_eta=False, id="download_progress", classes="hidden")
        with Horizontal(classes="action_row"):
            yield Button("Run Dry Apply", id="dry_run_btn", disabled=True)
        yield ProgressBar(total=1, show_eta=False, id="dry_progress", classes="hidden")
        yield Static("", id="dry_log", classes="hidden")
        with Horizontal(classes="nav_row"):
            yield Button("Back", id="back_btn_5")
            yield Button("Next: Install", id="next_btn_5", disabled=True)
            yield Button("Exit", id="exit_btn_5")


def compose_step6() -> ComposeResult:
    with Vertical(id="step6", classes="step_container step_hidden"):
        yield Static("Install macOS")
        yield Button("Install", id="install_btn", classes="hidden")
        yield ProgressBar(total=1, show_eta=False, id="live_progress", classes="hidden")
        yield Static("", id="live_log", classes="hidden")
        yield Static("", id="result_box", classes="hidden")
        with Horizontal(classes="nav_row"):
            yield Button("Back", id="back_btn_6")
            yield Button("Exit", id="exit_btn_6")
