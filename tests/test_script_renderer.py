"""Unit tests for script_renderer module."""
from __future__ import annotations

from pathlib import Path

from osx_proxmox_next.domain import PlanStep, VmConfig
from osx_proxmox_next.script_renderer import (
    _apple_id_bypass_patch_keys,
    _build_oc_disk_script,
    _plist_patch_script,
    render_script,
)


def _make_config(**kwargs) -> VmConfig:
    defaults = dict(
        vmid=901,
        name="macos-test",
        macos="sequoia",
        cores=8,
        memory_mb=16384,
        disk_gb=128,
        bridge="vmbr0",
        storage="local-lvm",
    )
    defaults.update(kwargs)
    return VmConfig(**defaults)


def _make_step(title: str, cmd: str) -> PlanStep:
    return PlanStep(title=title, argv=["bash", "-c", cmd])


# ---------------------------------------------------------------------------
# render_script
# ---------------------------------------------------------------------------


def test_render_script_returns_string() -> None:
    cfg = _make_config()
    steps = [_make_step("Do thing", "echo hello")]
    result = render_script(cfg, steps)
    assert isinstance(result, str)


def test_render_script_non_empty() -> None:
    cfg = _make_config()
    steps = [_make_step("Step One", "echo 1")]
    result = render_script(cfg, steps)
    assert len(result) > 0


def test_render_script_has_shebang() -> None:
    cfg = _make_config()
    result = render_script(cfg, [_make_step("S", "echo x")])
    assert result.startswith("#!/usr/bin/env bash")


def test_render_script_has_set_euo_pipefail() -> None:
    cfg = _make_config()
    result = render_script(cfg, [_make_step("S", "echo x")])
    assert "set -euo pipefail" in result


def test_render_script_includes_vmid() -> None:
    cfg = _make_config(vmid=999)
    result = render_script(cfg, [_make_step("S", "echo x")])
    assert "999" in result


def test_render_script_includes_macos_label() -> None:
    cfg = _make_config(macos="sonoma")
    result = render_script(cfg, [_make_step("S", "echo x")])
    assert "Sonoma" in result or "sonoma" in result.lower()


def test_render_script_step_titles_numbered() -> None:
    cfg = _make_config()
    steps = [_make_step("Alpha", "echo a"), _make_step("Beta", "echo b")]
    result = render_script(cfg, steps)
    assert "[1/2] Alpha" in result
    assert "[2/2] Beta" in result


def test_render_script_empty_steps() -> None:
    cfg = _make_config()
    result = render_script(cfg, [])
    assert isinstance(result, str)
    assert "#!/usr/bin/env bash" in result


def test_render_script_all_supported_macos_versions() -> None:
    for macos in ("ventura", "sonoma", "sequoia", "tahoe"):
        cfg = _make_config(macos=macos)
        result = render_script(cfg, [_make_step("S", "echo x")])
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _plist_patch_script
# ---------------------------------------------------------------------------


def test_plist_patch_script_returns_string() -> None:
    result = _plist_patch_script()
    assert isinstance(result, str)


def test_plist_patch_script_non_empty() -> None:
    result = _plist_patch_script()
    assert len(result) > 0


def test_plist_patch_script_contains_plistlib() -> None:
    result = _plist_patch_script()
    assert "plistlib" in result


def test_plist_patch_script_sets_scan_policy() -> None:
    result = _plist_patch_script()
    assert "ScanPolicy" in result


def test_plist_patch_script_verbose_boot_adds_flag() -> None:
    result_verbose = _plist_patch_script(verbose_boot=True)
    result_normal = _plist_patch_script(verbose_boot=False)
    assert "-v" in result_verbose
    assert "-v" not in result_normal


def test_plist_patch_script_amd_adds_quirks() -> None:
    result = _plist_patch_script(is_amd=True)
    assert "AppleCpuPmCfgLock" in result
    assert "AppleXcpmCfgLock" in result


def test_plist_patch_script_no_amd_no_quirks() -> None:
    result = _plist_patch_script(is_amd=False)
    assert "AppleCpuPmCfgLock" not in result


def test_plist_patch_script_apple_services_includes_platform_info() -> None:
    result = _plist_patch_script(
        apple_services=True,
        smbios_serial="ABCDEFG12345",
        smbios_uuid="A1B2C3D4",
        smbios_mlb="MLB1234",
        smbios_rom="AABBCC",
        smbios_model="MacPro71",
    )
    assert "PlatformInfo" in result
    assert "SystemSerialNumber" in result


def test_plist_patch_script_no_apple_services_no_platform_info() -> None:
    result = _plist_patch_script(apple_services=False, smbios_serial="ABCDEFG12345")
    assert "SystemSerialNumber" not in result


def test_plist_patch_script_apple_services_no_serial_no_platform_info() -> None:
    # apple_services=True but no serial → should not inject PlatformInfo
    result = _plist_patch_script(apple_services=True, smbios_serial="")
    assert "SystemSerialNumber" not in result


def test_plist_patch_script_apple_services_includes_bypass_patch() -> None:
    result = _plist_patch_script(apple_services=True)
    assert "hv_vmm_present" in result
    assert "Apple ID VM bypass" in result


def test_plist_patch_script_no_apple_services_no_bypass_patch() -> None:
    result = _plist_patch_script(apple_services=False)
    assert "hv_vmm_present" not in result


# ---------------------------------------------------------------------------
# _apple_id_bypass_patch_keys
# ---------------------------------------------------------------------------


def test_apple_id_bypass_patch_keys_returns_string() -> None:
    result = _apple_id_bypass_patch_keys()
    assert isinstance(result, str)


def test_apple_id_bypass_patch_keys_contains_find_hex() -> None:
    result = _apple_id_bypass_patch_keys()
    assert "68696265726e61746568696472656164790068696265726e617465636f756e7400" in result


def test_apple_id_bypass_patch_keys_contains_replace_hex() -> None:
    result = _apple_id_bypass_patch_keys()
    assert "68696265726e61746568696472656164790068765f766d6d5f70726573656e7400" in result


def test_apple_id_bypass_patch_keys_scoped_to_sequoia() -> None:
    result = _apple_id_bypass_patch_keys()
    assert "24.0.0" in result  # MinKernel for Sequoia (Darwin 24.x)


# ---------------------------------------------------------------------------
# _build_oc_disk_script
# ---------------------------------------------------------------------------


def test_build_oc_disk_script_returns_string() -> None:
    result = _build_oc_disk_script(
        opencore_path=Path("/iso/opencore.iso"),
        recovery_path=Path("/iso/sequoia-recovery.iso"),
        dest=Path("/tmp/oc.img"),
        macos="sequoia",
    )
    assert isinstance(result, str)


def test_build_oc_disk_script_non_empty() -> None:
    result = _build_oc_disk_script(
        opencore_path=Path("/iso/opencore.iso"),
        recovery_path=Path("/iso/sequoia-recovery.iso"),
        dest=Path("/tmp/oc.img"),
        macos="sequoia",
    )
    assert len(result) > 0


def test_build_oc_disk_script_contains_efi_check() -> None:
    result = _build_oc_disk_script(
        opencore_path=Path("/iso/opencore.iso"),
        recovery_path=Path("/iso/sequoia-recovery.iso"),
        dest=Path("/tmp/oc.img"),
        macos="sequoia",
    )
    assert "EFI/OC" in result


def test_build_oc_disk_script_contains_plistlib() -> None:
    result = _build_oc_disk_script(
        opencore_path=Path("/iso/opencore.iso"),
        recovery_path=Path("/iso/sequoia-recovery.iso"),
        dest=Path("/tmp/oc.img"),
        macos="sequoia",
    )
    assert "plistlib" in result


def test_build_oc_disk_script_contains_dest_path() -> None:
    dest = Path("/tmp/custom_oc.img")
    result = _build_oc_disk_script(
        opencore_path=Path("/iso/opencore.iso"),
        recovery_path=Path("/iso/sequoia-recovery.iso"),
        dest=dest,
        macos="sequoia",
    )
    assert str(dest) in result
