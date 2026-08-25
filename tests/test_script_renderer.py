"""Unit tests for script_renderer module."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from osx_proxmox_next.domain import PlanStep, VmConfig
from osx_proxmox_next.script_renderer import (
    PICKER_TIMEOUT_INSTALL,
    PICKER_TIMEOUT_INSTALLED,
    _APPLE_ID_BYPASS_PATCHES,
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


def test_plist_patch_script_allows_set_default() -> None:
    # Lets the user persist "macOS" as the boot entry (Ctrl+Enter in the picker)
    # so a fresh install stops falling back to Recovery on reboot.
    result = _plist_patch_script()
    assert "AllowSetDefault" in result


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


def _exec_bypass_fragment() -> list[dict]:
    """Run the fragment the way the generated script does: one shared namespace."""
    p: dict = {}
    exec(_apple_id_bypass_patch_keys(), {"p": p})  # noqa: S102
    return p["Kernel"]["Patch"]


def test_apple_id_bypass_hides_real_hv_vmm_present() -> None:
    """Without this patch both OIDs are named hv_vmm_present and the real one wins."""
    patches = _exec_bypass_fragment()
    finds = [bytes(x["Find"]) for x in patches]
    assert b"boot session UUID\x00hv_vmm_present\x00" in finds
    real = patches[finds.index(b"boot session UUID\x00hv_vmm_present\x00")]
    assert b"hv_vmm_present" not in bytes(real["Replace"])


def test_apple_id_bypass_patches_are_length_preserving() -> None:
    for patch in _exec_bypass_fragment():
        assert len(patch["Find"]) == len(patch["Replace"])


def test_apple_id_bypass_patch_bytes_are_exact() -> None:
    """A silent hex typo would make the patch never match and Apple ID fail quietly."""
    assert [(bytes(x["Find"]), bytes(x["Replace"])) for x in _exec_bypass_fragment()] == [
        # swap, not a rename: hv_vmm_present and hibernatecount trade names, so
        # no OID is invented and none disappears
        (b"boot session UUID\x00hv_vmm_present\x00",
         b"boot session UUID\x00hibernatecount\x00"),
        (b"hibernatehidready\x00hibernatecount\x00",
         b"hibernatehidready\x00hv_vmm_present\x00"),
    ]


def test_apple_id_bypass_patches_swap_rather_than_invent() -> None:
    """Post-patch the kernel must hold one of each name, not an invented one.

    Renaming the real OID to something like hv_vmm_hidden_ leaves a sysctl
    reading 1 whose description is still "running on a vmm", which fingerprints
    the guest harder than hiding the flag hides it.
    """
    names = {n for x in _exec_bypass_fragment()
             for n in bytes(x["Replace"]).split(b"\x00") if n}
    assert names == {b"boot session UUID", b"hibernatecount",
                     b"hibernatehidready", b"hv_vmm_present"}


_BASH_INSTALLER = Path(__file__).resolve().parents[1] / "scripts/bash/osx-proxmox-next.sh"


def _skeleton_config(path: Path) -> None:
    """Minimal config.plist with the containers both patchers assume exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps({
        "Misc": {"Security": {}, "Boot": {}},
        "NVRAM": {"Add": {"7C436110-AB2A-4BBB-A880-FE41995C9F82": {}}},
        "Kernel": {"Add": [{"BundlePath": "Lilu.kext", "Enabled": True}], "Quirks": {}},
        "UEFI": {"Quirks": {}},
        "PlatformInfo": {},
    }))


def _bash_inline_python() -> str:
    """Extract the config.plist patcher out of the bash installer.

    Sliced by marker rather than line number so it survives edits above it.
    """
    lines = _BASH_INSTALLER.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == 'python3 -c "')
    end = next(i for i, ln in enumerate(lines[start:], start)
               if ln.startswith('" "$dest_mnt'))
    return "\n".join(lines[start + 1:end])


def _run_bash_patcher(cfg: Path, tmp: Path, apple: str = "true") -> dict:
    script = tmp / "bash_patcher.py"
    script.write_text(_bash_inline_python())
    subprocess.run(
        [sys.executable, str(script), str(cfg), "Intel", apple, "C02XX0XXXXXX",
         "11111111-2222-3333-4444-555555555555", "C02XX000XXXXXXXXX",
         "001122334455", "MacPro7,1"],
        check=True, capture_output=True,
    )
    with cfg.open("rb") as fh:
        return plistlib.load(fh)


def _run_python_patcher(cfg: Path, apple: bool = True) -> dict:
    cmd = _plist_patch_script(
        apple_services=apple, smbios_serial="C02XX0XXXXXX",
        smbios_uuid="11111111-2222-3333-4444-555555555555",
        smbios_mlb="C02XX000XXXXXXXXX", smbios_rom="001122334455",
        smbios_model="MacPro7,1",
    )
    env = {**os.environ, "OC_DEST": str(cfg.parent.parent.parent)}
    subprocess.run(["bash", "-c", cmd], check=True, capture_output=True, env=env)
    with cfg.open("rb") as fh:
        return plistlib.load(fh)


@pytest.mark.parametrize("apple", [True, False])
def test_bash_and_python_emit_identical_kernel_patches(tmp_path: Path, apple: bool) -> None:
    """Execute both patchers and compare, rather than grepping for constants.

    A substring check only catches a typo'd hex literal. It sails past a
    swapped Find/Replace, Enabled=False, a bumped MinKernel, or the whole
    block being gated off, all of which ship a silently inert patch to bash
    users. This is the guard for the Python/bash parity rule.
    """
    py_cfg = tmp_path / "py/EFI/OC/config.plist"
    ba_cfg = tmp_path / "ba/EFI/OC/config.plist"
    _skeleton_config(py_cfg)
    _skeleton_config(ba_cfg)

    py = _run_python_patcher(py_cfg, apple=apple)
    ba = _run_bash_patcher(ba_cfg, tmp_path, apple="true" if apple else "false")

    assert py["Kernel"].get("Patch", []) == ba["Kernel"].get("Patch", [])
    assert len(py["Kernel"].get("Patch", [])) == (len(_APPLE_ID_BYPASS_PATCHES) if apple else 0)


def test_bash_inline_python_has_no_double_quotes() -> None:
    """The block is a double-quoted python3 -c argument in the shell.

    A single double-quote anywhere inside, even in a comment, ends the shell
    string early and shifts every positional argument. That shipped once: a
    comment mentioning the memory banner in quotes made the patcher receive
    'Modules' as its config path and the installer died with FileNotFoundError.
    shellcheck -S error does not catch it and bash -n parses it fine.
    """
    block = _bash_inline_python()
    bad = [(i, ln) for i, ln in enumerate(block.splitlines(), 1) if '"' in ln]
    assert not bad, f"double-quote inside python3 -c block: {bad[:3]}"


def test_bash_installer_sets_the_same_opencore_quirks(tmp_path: Path) -> None:
    """AllowSetDefault and RequestBootVarRouting reached bash long after Python."""
    py_cfg = tmp_path / "py/EFI/OC/config.plist"
    ba_cfg = tmp_path / "ba/EFI/OC/config.plist"
    _skeleton_config(py_cfg)
    _skeleton_config(ba_cfg)

    py = _run_python_patcher(py_cfg)
    ba = _run_bash_patcher(ba_cfg, tmp_path)

    assert py["Misc"]["Security"]["AllowSetDefault"] == ba["Misc"]["Security"]["AllowSetDefault"]
    assert py["UEFI"]["Quirks"]["RequestBootVarRouting"] == ba["UEFI"]["Quirks"]["RequestBootVarRouting"]
    assert py["Misc"]["Boot"]["Timeout"] == ba["Misc"]["Boot"]["Timeout"]
    guid = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
    assert py["NVRAM"]["Add"][guid]["boot-args"] == ba["NVRAM"]["Add"][guid]["boot-args"]


def test_boot_args_configure_restrictevents(tmp_path: Path) -> None:
    """revblock=pci is the documented config for the memory-warning block.

    This asserts the boot-arg is emitted, nothing more. It does NOT assert the
    banner is suppressed: measured on Sequoia 15.7.9 with Lilu, RestrictEvents
    1.1.6 and VirtualSMC all loaded, SIP off, hw.model MacPro7,1 and this exact
    boot-arg live, MemorySlotNotification still ran and still posted. Upstream
    limitation, see BOOT_ARGS_BASE.
    """
    cfg = tmp_path / "py/EFI/OC/config.plist"
    _skeleton_config(cfg)
    nvram = _run_python_patcher(cfg)["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]
    assert "revblock=pci" in nvram["boot-args"]


def test_boot_args_keep_verbose_flag_separate(tmp_path: Path) -> None:
    """--verbose-boot must add -v without dropping the RestrictEvents config."""
    verbose = _plist_patch_script(verbose_boot=True)
    normal = _plist_patch_script(verbose_boot=False)
    assert "revblock=pci" in verbose and "revblock=pci" in normal
    assert " -v" in verbose and " -v" not in normal


def test_build_disables_picker_auto_boot(tmp_path: Path) -> None:
    """Any non-zero timeout auto-boots recovery and the install never finishes.

    The picker lists the attached recovery disk ahead of the installer, so
    auto-boot always picks the wrong entry until post-install detaches recovery.
    Timeout=0 makes the picker wait instead of choosing wrongly.
    """
    cfg = tmp_path / "py/EFI/OC/config.plist"
    _skeleton_config(cfg)
    assert _run_python_patcher(cfg)["Misc"]["Boot"]["Timeout"] == PICKER_TIMEOUT_INSTALL
    assert PICKER_TIMEOUT_INSTALL == 0
    assert PICKER_TIMEOUT_INSTALLED > 0


def test_apple_id_bypass_patches_are_armed() -> None:
    """Byte-perfect but Enabled=False lands an inert patch and nothing complains.

    Same for Count=0 (matches nothing) and Identifier != kernel (would byte-patch
    every binary OpenCore loads). All three fail silently at runtime.
    """
    for patch in _exec_bypass_fragment():
        assert patch["Enabled"] is True
        assert patch["Count"] == 1
        assert patch["Identifier"] == "kernel"
        assert patch["Arch"] == "x86_64"
        assert patch["Base"] == ""


def test_apple_id_bypass_patch_comments_have_no_quotes() -> None:
    """Comments are interpolated raw into the fragment, so quotes break it.

    The fragment is wrapped in `python3 -c '...'`, where an apostrophe ends the
    shell string, and its dict literals use double quotes, where a double quote
    ends the Python string. Neither is escaped on the way in.
    """
    frag = _apple_id_bypass_patch_keys()
    for comment, _find, _replace in _APPLE_ID_BYPASS_PATCHES:
        assert "'" not in comment and '"' not in comment, comment
    assert "'" not in frag


def test_apple_id_bypass_patch_keys_is_idempotent() -> None:
    p: dict = {}
    frag = _apple_id_bypass_patch_keys()
    exec(frag, {"p": p})  # noqa: S102
    exec(frag, {"p": p})  # noqa: S102
    assert len(p["Kernel"]["Patch"]) == len(_APPLE_ID_BYPASS_PATCHES)


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


# ---------------------------------------------------------------------------
# RestrictEvents injection (silences MacPro7,1 "Memory Modules Misconfigured")
# ---------------------------------------------------------------------------


def _oc_script() -> str:
    return _build_oc_disk_script(
        opencore_path=Path("/iso/opencore.iso"),
        recovery_path=Path("/iso/sequoia-recovery.iso"),
        dest=Path("/tmp/oc.img"),
        macos="sequoia",
    )


def test_build_oc_disk_script_injects_restrictevents() -> None:
    result = _oc_script()
    assert "RestrictEvents.kext" in result
    assert "RestrictEvents-1.1.6-RELEASE.zip" in result


def test_build_oc_disk_script_restrictevents_zip_next_to_iso() -> None:
    result = _oc_script()
    assert "/iso/RestrictEvents-1.1.6-RELEASE.zip" in result


def test_build_oc_disk_script_restrictevents_curl_fallback() -> None:
    result = _oc_script()
    assert "curl -fsSL" in result
    assert "github.com/acidanthera/RestrictEvents/releases/download" in result


def test_build_oc_disk_script_restrictevents_failure_is_non_fatal() -> None:
    result = _oc_script()
    assert "memory warning fix skipped" in result


def test_build_oc_disk_script_injection_runs_before_plist_patch() -> None:
    result = _oc_script()
    assert result.index("RE_ZIP") < result.index("plistlib")


def test_plist_patch_script_registers_restrictevents_kext() -> None:
    script = _plist_patch_script()
    assert "RestrictEvents.kext" in script
    assert "Contents/MacOS/RestrictEvents" in script
    assert "Contents/Info.plist" in script


def test_plist_patch_script_restrictevents_entry_guarded_by_kext_dir() -> None:
    script = _plist_patch_script()
    assert 'os.path.isdir(oc_dest+"/EFI/OC/Kexts/RestrictEvents.kext")' in script


@pytest.mark.parametrize("version,accepted", [
    ("8.0.4", True),
    ("8.4.1", True),
    ("9.0.3", True),
    ("9.1.1", True),
    ("9.2.4", True),   # regression: minor-version cap rejected 9.2 (#123)
    ("9.7.0", True),
    ("10.0.1", False),
    ("7.4.1", False),
    ("", False),
])
def test_bash_pve_check_gates_on_major_version_only(tmp_path: Path, version: str,
                                                    accepted: bool) -> None:
    """Execute the real pve_check with a stubbed pveversion.

    Proxmox minor releases stay compatible, so only the major version may be
    gated; capping the minor broke every new point release (9.2, issue #123).
    """
    import re as _re
    import subprocess

    script = (Path(__file__).resolve().parent.parent
              / "scripts/bash/osx-proxmox-next.sh").read_text()
    m = _re.search(r"pve_check\(\) \{.*?\n\}", script, _re.S)
    assert m, "pve_check not found in bash script"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        f'pveversion() {{ echo "pve-manager/{version}/abcdef"; }}\n'
        'msg_error() { echo "ERR: $1"; }\n'
        + m.group(0) + "\n"
        "pve_check && echo ACCEPTED\n"
    )
    result = subprocess.run(["bash", str(harness)], capture_output=True, text=True)
    if accepted:
        assert "ACCEPTED" in result.stdout, result.stdout + result.stderr
    else:
        assert "ACCEPTED" not in result.stdout
        assert result.returncode == 1
