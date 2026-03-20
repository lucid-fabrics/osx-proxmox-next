from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..domain import DEFAULT_VMID, VmConfig
from ..planner import PlanStep
from ..preflight import PreflightCheck
from ..rollback import RollbackSnapshot
from ..smbios import SmbiosIdentity

__all__ = ["WizardState"]


@dataclass
class WizardState:
    selected_os: str = ""
    selected_storage: str = ""
    storage_targets: list[str] = field(default_factory=list)
    iso_dirs: list[str] = field(default_factory=list)
    selected_iso_dir: str = ""
    # Form
    vmid: int = DEFAULT_VMID
    name: str = ""
    cores: int = 8
    memory_mb: int = 16384
    disk_gb: int = 128
    bridge: str = "vmbr0"
    storage: str = "local-lvm"
    installer_path: str = ""
    smbios: SmbiosIdentity | None = None
    apple_services: bool = False
    use_penryn: bool = False
    net_model: str = "vmxnet3"
    form_errors: dict[str, str] = field(default_factory=dict)
    # Preflight
    preflight_done: bool = False
    preflight_ok: bool = False
    preflight_checks: list[PreflightCheck] = field(default_factory=list)
    # Downloads
    download_running: bool = False
    download_phase: str = ""
    download_pct: int = 0
    download_errors: list[str] = field(default_factory=list)
    downloads_complete: bool = False
    # Config + Plan
    config: VmConfig | None = None
    plan_steps: list[PlanStep] = field(default_factory=list)
    assets_ok: bool = False
    assets_missing: list[str] = field(default_factory=list)
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
    uninstall_vm_list: list[str] = field(default_factory=list)
    uninstall_purge: bool = True
    uninstall_log: list[str] = field(default_factory=list)
    uninstall_running: bool = False
    uninstall_done: bool = False
    uninstall_ok: bool = False
