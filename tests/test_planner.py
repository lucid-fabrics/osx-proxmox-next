from osx_proxmox_next.domain import VmConfig
from osx_proxmox_next.planner import build_plan, render_script, VmInfo, fetch_vm_info, build_destroy_plan
from osx_proxmox_next.infrastructure import CommandResult


def _cfg(macos: str) -> VmConfig:
    return VmConfig(
        vmid=901,
        name="macos-test",
        macos=macos,
        cores=8,
        memory_mb=16384,
        disk_gb=128,
        bridge="vmbr0",
        storage="local-lvm",
        installer_path="",
    )


def test_build_plan_includes_core_steps() -> None:
    steps = build_plan(_cfg("sequoia"))
    titles = [step.title for step in steps]
    assert "Create VM shell" in titles
    assert "Apply macOS hardware profile" in titles
    assert "Build OpenCore boot disk" in titles
    assert "Import and attach OpenCore disk" in titles
    assert "Import and attach macOS recovery" in titles
    assert "Set boot order" in titles
    assert any(step.command.startswith("qm start") for step in steps)


def test_build_plan_tahoe_no_preview_warning() -> None:
    cfg = _cfg("tahoe")
    cfg.installer_path = "/tmp/tahoe.iso"
    steps = build_plan(cfg)
    assert steps[0].title != "Preview warning"


def test_render_script_contains_metadata() -> None:
    cfg = _cfg("sequoia")
    script = render_script(cfg, build_plan(cfg))
    assert "#!/usr/bin/env bash" in script
    assert "macOS Sequoia 15" in script
    assert "qm create 901" in script


def test_build_plan_boot_order_is_shell_safe() -> None:
    steps = build_plan(_cfg("sequoia"))
    boot = next(step for step in steps if step.title == "Set boot order")
    assert "--boot 'order=ide2;sata0;ide0'" in boot.command


def test_build_plan_sets_applesmc_args() -> None:
    steps = build_plan(_cfg("sequoia"))
    profile = next(step for step in steps if step.title == "Apply macOS hardware profile")
    assert "isa-applesmc" in profile.command
    assert "--vga std" in profile.command


def test_build_plan_includes_smbios_step() -> None:
    import base64
    steps = build_plan(_cfg("sequoia"))
    titles = [step.title for step in steps]
    assert "Set SMBIOS identity" in titles
    smbios_step = next(step for step in steps if step.title == "Set SMBIOS identity")
    assert "--smbios1" in smbios_step.command
    assert f"manufacturer={base64.b64encode(b'Apple Inc.').decode()}" in smbios_step.command
    assert f"family={base64.b64encode(b'Mac').decode()}" in smbios_step.command


def test_build_plan_skips_smbios_when_disabled() -> None:
    cfg = _cfg("sequoia")
    cfg.no_smbios = True
    steps = build_plan(cfg)
    titles = [step.title for step in steps]
    assert "Set SMBIOS identity" not in titles


def test_build_plan_uses_provided_smbios() -> None:
    import base64
    cfg = _cfg("sequoia")
    cfg.smbios_serial = "TESTSERIAL12"
    cfg.smbios_uuid = "12345678-1234-1234-1234-123456789ABC"
    cfg.smbios_model = "MacPro7,1"
    steps = build_plan(cfg)
    smbios_step = next(step for step in steps if step.title == "Set SMBIOS identity")
    assert f"serial={base64.b64encode(b'TESTSERIAL12').decode()}" in smbios_step.command
    assert "12345678-1234-1234-1234-123456789ABC" in smbios_step.command
    assert f"product={base64.b64encode(b'MacPro7,1').decode()}" in smbios_step.command


def test_build_plan_uses_importdisk_for_opencore(monkeypatch) -> None:
    from pathlib import Path
    import osx_proxmox_next.planner as planner

    monkeypatch.setattr(planner, "resolve_opencore_path", lambda _macos: Path("/mnt/pve/wd2tb/template/iso/opencore-tahoe.iso"))
    monkeypatch.setattr(
        planner,
        "resolve_recovery_or_installer_path",
        lambda _cfg: Path("/mnt/pve/wd2tb/template/iso/macos-tahoe-full.iso"),
    )
    cfg = _cfg("tahoe")
    cfg.installer_path = ""
    steps = build_plan(cfg)
    oc = next(step for step in steps if step.title == "Import and attach OpenCore disk")
    assert "qm importdisk" in oc.command
    assert "opencore-tahoe-vm901.img" in oc.command
    assert "media=disk" in oc.command
    assert "pvesm path" in oc.command
    assert "dd if=" in oc.command


def test_build_plan_uses_importdisk_for_recovery(monkeypatch) -> None:
    from pathlib import Path
    import osx_proxmox_next.planner as planner

    monkeypatch.setattr(
        planner,
        "resolve_recovery_or_installer_path",
        lambda _cfg: Path("/var/lib/vz/template/iso/sonoma-recovery.img"),
    )
    cfg = _cfg("sonoma")
    steps = build_plan(cfg)
    recovery = next(step for step in steps if step.title == "Import and attach macOS recovery")
    assert "qm importdisk" in recovery.command
    assert "sonoma-recovery.img" in recovery.command
    assert "media=disk" in recovery.command


def test_smbios_model_fallback():
    import base64
    cfg = _cfg("sequoia")
    cfg.smbios_serial = "TESTSERIAL12"
    cfg.smbios_uuid = "12345678-1234-1234-1234-123456789ABC"
    cfg.smbios_model = ""  # empty model triggers fallback
    steps = build_plan(cfg)
    smbios_step = next(step for step in steps if step.title == "Set SMBIOS identity")
    assert f"product={base64.b64encode(b'iMacPro1,1').decode()}" in smbios_step.command


def test_build_plan_includes_build_oc_step() -> None:
    steps = build_plan(_cfg("sequoia"))
    titles = [step.title for step in steps]
    assert "Build OpenCore boot disk" in titles
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert "losetup" in build.command
    assert "ScanPolicy" in build.command
    assert "DmgLoading" in build.command
    assert "sgdisk" in build.command
    # Verify ordering: build comes before import OC, before recovery
    build_idx = titles.index("Build OpenCore boot disk")
    oc_idx = titles.index("Import and attach OpenCore disk")
    recovery_idx = titles.index("Import and attach macOS recovery")
    assert build_idx < oc_idx < recovery_idx


def test_build_plan_recovery_uses_importdisk(monkeypatch) -> None:
    """Recovery images (.img and .iso) are always imported as disk."""
    from pathlib import Path
    import osx_proxmox_next.planner as planner

    for suffix in (".iso", ".img"):
        monkeypatch.setattr(
            planner,
            "resolve_recovery_or_installer_path",
            lambda _cfg, s=suffix: Path(f"/var/lib/vz/template/iso/sonoma-recovery{s}"),
        )
        cfg = _cfg("sonoma")
        steps = build_plan(cfg)
        titles = [s.title for s in steps]
        assert "Import and attach macOS recovery" in titles
        recovery = next(s for s in steps if s.title == "Import and attach macOS recovery")
        assert "qm importdisk" in recovery.command
        assert "media=disk" in recovery.command


def test_smbios_values_are_base64_encoded():
    """Smbios1 values must be Base64-encoded for Proxmox."""
    import base64
    cfg = _cfg("tahoe")
    cfg.installer_path = "/tmp/tahoe.iso"
    cfg.smbios_serial = "TESTSERIAL12"
    cfg.smbios_uuid = "12345678-1234-1234-1234-123456789ABC"
    cfg.smbios_model = "MacPro7,1"
    steps = build_plan(cfg)
    smbios_step = next(step for step in steps if step.title == "Set SMBIOS identity")
    encoded = base64.b64encode(b"MacPro7,1").decode()
    assert f"product={encoded}," in smbios_step.command
    assert "MacPro7,1" not in smbios_step.command


def test_render_script_simple() -> None:
    cfg = _cfg("sequoia")
    script = render_script(cfg, build_plan(cfg))
    assert "#!/usr/bin/env bash" in script
    assert "qm create 901" in script
    assert "Build OpenCore boot disk" in script


# ── Destroy Plan Tests ─────────────────────────────────────────────


def test_build_destroy_plan_basic() -> None:
    steps = build_destroy_plan(106)
    assert len(steps) == 2
    assert steps[0].title == "Stop VM"
    assert steps[1].title == "Destroy VM"
    assert "qm stop 106" in steps[0].command
    assert "qm destroy 106" in steps[1].command
    assert "--purge" not in steps[1].command


def test_build_destroy_plan_with_purge() -> None:
    steps = build_destroy_plan(106, purge=True)
    assert "--purge" in steps[1].command


def test_build_destroy_plan_risk_levels() -> None:
    steps = build_destroy_plan(200)
    assert steps[0].risk == "warn"
    assert steps[1].risk == "warn"


def test_build_destroy_plan_vmid_in_commands() -> None:
    steps = build_destroy_plan(42)
    assert "42" in steps[0].command
    assert "42" in steps[1].command


def test_fetch_vm_info_exists() -> None:
    class FakeAdapter:
        def run(self, argv):
            if argv[1] == "status":
                return CommandResult(ok=True, returncode=0, output="status: running")
            if argv[1] == "config":
                return CommandResult(ok=True, returncode=0, output="name: macos-test\ncores: 8\nmemory: 16384")
            return CommandResult(ok=False, returncode=1, output="")

    info = fetch_vm_info(106, adapter=FakeAdapter())
    assert info is not None
    assert info.vmid == 106
    assert info.name == "macos-test"
    assert info.status == "running"
    assert "cores: 8" in info.config_raw


def test_fetch_vm_info_stopped() -> None:
    class FakeAdapter:
        def run(self, argv):
            if argv[1] == "status":
                return CommandResult(ok=True, returncode=0, output="status: stopped")
            if argv[1] == "config":
                return CommandResult(ok=True, returncode=0, output="name: macos-vm\ncores: 4")
            return CommandResult(ok=False, returncode=1, output="")

    info = fetch_vm_info(200, adapter=FakeAdapter())
    assert info is not None
    assert info.status == "stopped"
    assert info.name == "macos-vm"


def test_fetch_vm_info_not_found() -> None:
    class FakeAdapter:
        def run(self, argv):
            return CommandResult(ok=False, returncode=255, output="Configuration file not found")

    info = fetch_vm_info(999, adapter=FakeAdapter())
    assert info is None


def test_fetch_vm_info_config_failure() -> None:
    class FakeAdapter:
        def run(self, argv):
            if argv[1] == "status":
                return CommandResult(ok=True, returncode=0, output="status: stopped")
            return CommandResult(ok=False, returncode=1, output="")

    info = fetch_vm_info(300, adapter=FakeAdapter())
    assert info is not None
    assert info.config_raw == ""
    assert info.name == ""


def test_fetch_vm_info_no_name_in_config() -> None:
    class FakeAdapter:
        def run(self, argv):
            if argv[1] == "status":
                return CommandResult(ok=True, returncode=0, output="status: stopped")
            if argv[1] == "config":
                return CommandResult(ok=True, returncode=0, output="cores: 4\nmemory: 8192")
            return CommandResult(ok=False, returncode=1, output="")

    info = fetch_vm_info(400, adapter=FakeAdapter())
    assert info is not None
    assert info.name == ""
