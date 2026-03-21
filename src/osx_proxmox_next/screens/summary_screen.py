from __future__ import annotations

from ..defaults import CpuInfo
from ..domain import SUPPORTED_MACOS, PlanStep, VmConfig
from ..preflight import PreflightCheck
from ..rollback import RollbackSnapshot, rollback_hints

__all__ = [
    "build_config_summary_text",
    "format_preflight_text",
    "format_install_result",
]


def build_config_summary_text(
    config: VmConfig,
    plan_steps: list[PlanStep],
    cpu: CpuInfo,
) -> str:
    """Return the plain-text config summary shown in the review step."""
    meta = SUPPORTED_MACOS.get(config.macos, {})
    cpu_label = cpu.model_name or cpu.vendor
    lines = [
        f"Target: {meta.get('label', config.macos)} ({meta.get('channel', '?')})",
        f"VM: {config.vmid} / {config.name}",
        f"CPU: {cpu_label} — {config.cores} cores | Memory: {config.memory_mb} MB | Disk: {config.disk_gb} GB",
        f"Storage: {config.storage} | Bridge: {config.bridge}",
    ]
    if config.installer_path:
        lines.append(f"Installer: {config.installer_path}")
    lines.append("")
    lines.append(f"Plan: {len(plan_steps)} steps")
    for idx, step in enumerate(plan_steps, start=1):
        prefix = "!" if step.risk in {"warn", "action"} else "-"
        lines.append(f"  {idx:02d}. {prefix} {step.title}")
    return "\n".join(lines)


def format_preflight_text(done: bool, checks: list[PreflightCheck]) -> str:
    """Return the preflight status text for display in the TUI."""
    if not done:
        return "Checking..."
    passed = [c for c in checks if c.ok]
    failed = [c for c in checks if not c.ok]
    lines = [f"  ✓ {c.name}" for c in passed] + [f"  ✗ {c.name}: {c.details}" for c in failed]
    header = f"{len(failed)} check(s) failed" if failed else f"All {len(passed)} checks passed"
    return header + "\n" + "\n".join(lines)


def format_install_result(
    ok: bool,
    vmid: int | str,
    log_path: object,
    snapshot: RollbackSnapshot | None,
) -> str:
    """Return the install result text for the result box."""
    if ok:
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
    else:
        lines = ["Install FAILED.", f"Log: {log_path}"]
        if snapshot:
            lines.append("")
            lines.extend(rollback_hints(snapshot))
    return "\n".join(lines)
