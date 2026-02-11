from osx_proxmox_next.domain import VmConfig
from osx_proxmox_next.planner import build_plan, render_script


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
    assert "Set boot order" in titles
    assert any(step.command.startswith("qm start") for step in steps)


def test_build_plan_marks_tahoe_preview() -> None:
    cfg = _cfg("tahoe")
    cfg.installer_path = "/tmp/tahoe.iso"
    steps = build_plan(cfg)
    assert steps[0].title == "Preview warning"
    assert steps[0].risk == "warn"


def test_render_script_contains_metadata() -> None:
    cfg = _cfg("sequoia")
    script = render_script(cfg, build_plan(cfg))
    assert "#!/usr/bin/env bash" in script
    assert "macOS Sequoia 15" in script
    assert "qm create 901" in script


def test_build_plan_boot_order_is_shell_safe() -> None:
    steps = build_plan(_cfg("sequoia"))
    boot = next(step for step in steps if step.title == "Set boot order")
    assert "--boot 'order=ide2;ide3;sata0'" in boot.command


def test_build_plan_sets_applesmc_args() -> None:
    steps = build_plan(_cfg("sequoia"))
    profile = next(step for step in steps if step.title == "Apply macOS hardware profile")
    assert "isa-applesmc" in profile.command
    assert "--vga std" in profile.command


def test_build_plan_includes_smbios_step() -> None:
    steps = build_plan(_cfg("sequoia"))
    titles = [step.title for step in steps]
    assert "Set SMBIOS identity" in titles
    smbios_step = next(step for step in steps if step.title == "Set SMBIOS identity")
    assert "--smbios1" in smbios_step.command
    assert "Apple Inc." in smbios_step.command
    assert "family=Mac" in smbios_step.command


def test_build_plan_skips_smbios_when_disabled() -> None:
    cfg = _cfg("sequoia")
    cfg.no_smbios = True
    steps = build_plan(cfg)
    titles = [step.title for step in steps]
    assert "Set SMBIOS identity" not in titles


def test_build_plan_uses_provided_smbios() -> None:
    cfg = _cfg("sequoia")
    cfg.smbios_serial = "TESTSERIAL12"
    cfg.smbios_uuid = "12345678-1234-1234-1234-123456789ABC"
    cfg.smbios_model = "MacPro7,1"
    steps = build_plan(cfg)
    smbios_step = next(step for step in steps if step.title == "Set SMBIOS identity")
    assert "TESTSERIAL12" in smbios_step.command
    assert "12345678-1234-1234-1234-123456789ABC" in smbios_step.command
    assert "MacPro7,1" in smbios_step.command


def test_build_plan_uses_storage_iso_refs(monkeypatch) -> None:
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
    opencore = next(step for step in steps if step.title == "Attach OpenCore ISO")
    installer = next(step for step in steps if step.title == "Attach macOS recovery ISO")
    assert "wd2tb:iso/opencore-tahoe.iso,media=cdrom" in opencore.command
    assert "wd2tb:iso/macos-tahoe-full.iso" in installer.command
