from __future__ import annotations

import pytest

from osx_proxmox_next.defaults import CpuInfo
from osx_proxmox_next.domain import PlanStep, VmConfig
from osx_proxmox_next.screens.summary_screen import build_config_summary_text


def _cfg(**kwargs) -> VmConfig:
    defaults = dict(
        vmid=100, name="macos-test", macos="sequoia",
        cores=4, memory_mb=8192, disk_gb=64,
        bridge="vmbr0", storage="local-lvm",
    )
    defaults.update(kwargs)
    return VmConfig(**defaults)


def _cpu(model: str = "Intel Core i9") -> CpuInfo:
    return CpuInfo(
        vendor="GenuineIntel", model_name=model,
        family=6, model=60, needs_emulated_cpu=False,
    )


def _step(title: str, risk: str = "safe") -> PlanStep:
    return PlanStep(title=title, argv=[], risk=risk)


# --- summary_screen ---

class TestBuildConfigSummaryText:
    def test_contains_vm_id_and_name(self):
        text = build_config_summary_text(_cfg(), [], _cpu())
        assert "100" in text
        assert "macos-test" in text

    def test_contains_macos_label(self):
        text = build_config_summary_text(_cfg(macos="sequoia"), [], _cpu())
        assert "Sequoia" in text or "sequoia" in text.lower()

    def test_contains_cpu_model(self):
        text = build_config_summary_text(_cfg(), [], _cpu("AMD Ryzen 9"))
        assert "AMD Ryzen 9" in text

    def test_falls_back_to_vendor_when_no_model_name(self):
        cpu = CpuInfo(vendor="AuthenticAMD", model_name="", family=25, model=1, needs_emulated_cpu=True)
        text = build_config_summary_text(_cfg(), [], cpu)
        assert "AuthenticAMD" in text

    def test_plan_step_count(self):
        steps = [_step("Create VM"), _step("Import disk")]
        text = build_config_summary_text(_cfg(), steps, _cpu())
        assert "2 steps" in text

    def test_plan_steps_listed(self):
        steps = [_step("Create VM"), _step("Import disk", risk="warn")]
        text = build_config_summary_text(_cfg(), steps, _cpu())
        assert "Create VM" in text
        assert "Import disk" in text

    def test_warn_step_uses_exclamation_prefix(self):
        steps = [_step("Risky step", risk="warn")]
        text = build_config_summary_text(_cfg(), steps, _cpu())
        assert "!" in text

    def test_action_step_uses_exclamation_prefix(self):
        steps = [_step("Action step", risk="action")]
        text = build_config_summary_text(_cfg(), steps, _cpu())
        assert "!" in text

    def test_safe_step_uses_dash_prefix(self):
        steps = [_step("Safe step", risk="safe")]
        text = build_config_summary_text(_cfg(), steps, _cpu())
        assert "-" in text

    def test_installer_path_shown_when_set(self):
        cfg = _cfg(installer_path="/tmp/installer.iso")
        text = build_config_summary_text(cfg, [], _cpu())
        assert "/tmp/installer.iso" in text

    def test_installer_path_absent_when_not_set(self):
        text = build_config_summary_text(_cfg(installer_path=""), [], _cpu())
        assert "Installer:" not in text

    def test_empty_plan_shows_zero_steps(self):
        text = build_config_summary_text(_cfg(), [], _cpu())
        assert "0 steps" in text

    def test_returns_string(self):
        result = build_config_summary_text(_cfg(), [], _cpu())
        assert isinstance(result, str)


# --- step_screens (structural) ---

class TestStepScreensImport:
    """Verify step screen functions are importable and callable without a running Textual app."""

    def test_all_expected_exports_present(self):
        from osx_proxmox_next.screens import step_screens
        for name in ["compose_step1", "compose_step2", "compose_step3",
                     "compose_step4", "compose_step5", "compose_step6"]:
            assert hasattr(step_screens, name), f"Missing export: {name}"

    def test_compose_step1_returns_generator(self):
        from osx_proxmox_next.screens.step_screens import compose_step1
        import types
        result = compose_step1()
        assert isinstance(result, types.GeneratorType)

    def test_compose_step3_accepts_storage_list(self):
        from osx_proxmox_next.screens.step_screens import compose_step3
        import types
        result = compose_step3(["local-lvm", "nvme"])
        assert isinstance(result, types.GeneratorType)

    def test_compose_step4_accepts_cpu_info(self):
        from osx_proxmox_next.screens.step_screens import compose_step4
        import types
        cpu = _cpu()
        result = compose_step4(cpu)
        assert isinstance(result, types.GeneratorType)
