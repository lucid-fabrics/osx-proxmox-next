from __future__ import annotations

from ..defaults import CpuInfo
from ..domain import SUPPORTED_MACOS, VmConfig
from ..planner import PlanStep

__all__ = ["build_config_summary_text"]


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
