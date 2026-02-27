from osx_proxmox_next.defaults import CpuInfo
from osx_proxmox_next.domain import VmConfig
from osx_proxmox_next.planner import build_plan, render_script, _cpu_args, VmInfo, fetch_vm_info, build_destroy_plan
from osx_proxmox_next.infrastructure import CommandResult


def _cpu(vendor="Intel", model_name="", family=6, model=85, needs_emulated=False):
    """Helper to build CpuInfo for tests."""
    return CpuInfo(vendor=vendor, model_name=model_name, family=family,
                   model=model, needs_emulated_cpu=needs_emulated)


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
    assert "Stamp recovery with Apple icon flavour" in titles
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
    assert "--boot 'order=ide2;virtio0;ide0'" in boot.command


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
    assert "base64=1," in smbios_step.command
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
    assert f"product={base64.b64encode(b'MacPro7,1').decode()}" in smbios_step.command


def test_build_plan_includes_build_oc_step() -> None:
    steps = build_plan(_cfg("sequoia"))
    titles = [step.title for step in steps]
    assert "Build OpenCore boot disk" in titles
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert "losetup" in build.command
    assert "blkid" in build.command
    assert "vfat" in build.command
    assert "SRC_PART" in build.command
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
    """Smbios1 values must be Base64-encoded for Proxmox with base64=1 flag."""
    import base64
    cfg = _cfg("tahoe")
    cfg.installer_path = "/tmp/tahoe.iso"
    cfg.smbios_serial = "TESTSERIAL12"
    cfg.smbios_uuid = "12345678-1234-1234-1234-123456789ABC"
    cfg.smbios_model = "MacPro7,1"
    steps = build_plan(cfg)
    smbios_step = next(step for step in steps if step.title == "Set SMBIOS identity")
    encoded = base64.b64encode(b"MacPro7,1").decode()
    assert "base64=1," in smbios_step.command
    assert f"product={encoded}," in smbios_step.command
    assert "MacPro7,1" not in smbios_step.command


def test_render_script_simple() -> None:
    cfg = _cfg("sequoia")
    script = render_script(cfg, build_plan(cfg))
    assert "#!/usr/bin/env bash" in script
    assert "qm create 901" in script
    assert "Build OpenCore boot disk" in script


# ── CPU Detection Tests ───────────────────────────────────────────────


def test_cpu_args_intel() -> None:
    """Legacy Intel → -cpu host passthrough."""
    cpu = _cpu(vendor="Intel", needs_emulated=False)
    args = _cpu_args(cpu)
    assert "-cpu host," in args
    assert "vendor=GenuineIntel" in args
    assert "+kvm_pv_unhalt" in args
    assert "vmware-cpuid-freq=on" in args


def test_cpu_args_amd() -> None:
    """AMD → Cascadelake-Server emulation."""
    cpu = _cpu(vendor="AMD", needs_emulated=True)
    args = _cpu_args(cpu)
    assert "Cascadelake-Server" in args
    assert "vendor=GenuineIntel" in args
    assert "vmware-cpuid-freq=on" in args
    assert "-avx512f" in args
    assert "-pcid" in args
    assert "host" not in args


def test_cpu_args_intel_hybrid() -> None:
    """Hybrid Intel (12th gen+) → Cascadelake-Server, same as AMD."""
    cpu = _cpu(vendor="Intel", model=151, needs_emulated=True)
    args = _cpu_args(cpu)
    assert "Cascadelake-Server" in args
    assert "-avx512f" in args
    assert "-pcid" in args
    assert "host" not in args


def test_cpu_args_override() -> None:
    """CLI --cpu-model override takes precedence."""
    cpu = _cpu(vendor="Intel", needs_emulated=False)
    args = _cpu_args(cpu, override="Skylake-Server-IBRS")
    assert "Skylake-Server-IBRS" in args
    assert "kvm=on" in args
    assert "vendor=GenuineIntel" in args
    assert "Cascadelake" not in args
    assert "host" not in args


def test_build_plan_amd_uses_cascadelake(monkeypatch) -> None:
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="AMD", needs_emulated=True))
    steps = build_plan(_cfg("sequoia"))
    profile = next(step for step in steps if step.title == "Apply macOS hardware profile")
    assert "Cascadelake-Server" in profile.command
    assert "vendor=GenuineIntel" in profile.command


def test_build_plan_intel_uses_host(monkeypatch) -> None:
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    profile = next(step for step in steps if step.title == "Apply macOS hardware profile")
    assert "-cpu host," in profile.command
    assert "vendor=GenuineIntel" in profile.command


def test_build_plan_intel_hybrid_uses_cascadelake(monkeypatch) -> None:
    """Hybrid Intel gets Cascadelake-Server but NOT AMD kernel patches."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", model=151, needs_emulated=True))
    steps = build_plan(_cfg("sequoia"))
    profile = next(step for step in steps if step.title == "Apply macOS hardware profile")
    assert "Cascadelake-Server" in profile.command
    # Must NOT have AMD kernel patches
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert "AppleCpuPmCfgLock" not in build.command
    assert "AppleXcpmCfgLock" not in build.command


def test_build_plan_amd_config(monkeypatch) -> None:
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="AMD", needs_emulated=True))
    steps = build_plan(_cfg("sequoia"))
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    # Power management locks flipped for AMD
    assert "AppleCpuPmCfgLock" in build.command
    assert "AppleXcpmCfgLock" in build.command
    # SecureBootModel=Disabled so shipped PENRYN patches apply
    assert 'SecureBootModel\"]=\"Disabled\"' in build.command
    # No full AMD_Vanilla patches (Cascadelake-Server handles CPUID)
    assert "cpuid_cores_per_package" not in build.command


def test_build_plan_default_no_verbose(monkeypatch) -> None:
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    cfg = _cfg("sequoia")
    steps = build_plan(cfg)
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert " -v" not in build.command


def test_build_plan_verbose_boot(monkeypatch) -> None:
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    cfg = _cfg("sequoia")
    cfg.verbose_boot = True
    steps = build_plan(cfg)
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert "-v" in build.command


def test_build_plan_intel_no_amd_config(monkeypatch) -> None:
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert "AppleCpuPmCfgLock" not in build.command


def test_build_plan_oc_disk_hides_opencore_entry(monkeypatch) -> None:
    """OC ESP must have .contentVisibility=Auxiliary to hide from picker."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert ".contentVisibility" in build.command
    assert "Auxiliary" in build.command
    assert "HideAuxiliary" in build.command


def test_build_plan_stamps_recovery_flavour(monkeypatch) -> None:
    """Recovery must be stamped with custom name and volume icon."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    stamp = next(step for step in steps if step.title == "Stamp recovery with Apple icon flavour")
    assert ".contentDetails" in stamp.command
    assert "InstallAssistant.icns" in stamp.command
    assert ".VolumeIcon.icns" in stamp.command
    assert "hfsplus" in stamp.command
    # Stamp must come before import
    titles = [s.title for s in steps]
    assert titles.index("Stamp recovery with Apple icon flavour") < titles.index("Import and attach macOS recovery")


def test_build_plan_cpu_model_override(monkeypatch) -> None:
    """--cpu-model override is used in hardware profile."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", model=151, needs_emulated=True))
    cfg = _cfg("sequoia")
    cfg.cpu_model = "Skylake-Server-IBRS"
    steps = build_plan(cfg)
    profile = next(step for step in steps if step.title == "Apply macOS hardware profile")
    assert "Skylake-Server-IBRS" in profile.command
    assert "Cascadelake" not in profile.command


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


def test_build_plan_apple_services_patches_platforminfo(monkeypatch) -> None:
    """When apple_services=True, PlatformInfo fields must appear in OC build script."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    cfg = _cfg("sequoia")
    cfg.apple_services = True
    steps = build_plan(cfg)
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert "PlatformInfo" in build.command
    assert "SystemSerialNumber" in build.command
    assert "MLB" in build.command
    assert "ROM" in build.command
    assert "UpdateSMBIOS" in build.command
    assert "UpdateDataHub" in build.command


def test_build_plan_no_apple_services_no_platforminfo(monkeypatch) -> None:
    """When apple_services=False, PlatformInfo must NOT appear in OC build script."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    cfg = _cfg("sequoia")
    cfg.apple_services = False
    steps = build_plan(cfg)
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert "PlatformInfo" not in build.command


def test_build_plan_apple_services_rom_derived_from_mac(monkeypatch) -> None:
    """When apple_services=True, ROM must be derived from static MAC."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    cfg = _cfg("sequoia")
    cfg.apple_services = True
    steps = build_plan(cfg)
    # ROM should equal MAC without colons
    expected_rom = cfg.static_mac.replace(":", "")[:12].upper()
    assert cfg.smbios_rom == expected_rom
    # Build script should contain the derived ROM
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    assert expected_rom in build.command


def test_build_plan_apple_services_mac_propagated_from_smbios(monkeypatch) -> None:
    """_smbios_steps propagates MAC to config.static_mac so _apple_services_steps reuses it."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    cfg = _cfg("sequoia")
    cfg.apple_services = True
    steps = build_plan(cfg)
    # MAC set by _smbios_steps should be reused — verify ROM matches MAC
    mac_hex = cfg.static_mac.replace(":", "").upper()
    assert cfg.smbios_rom == mac_hex
    # Verify the MAC appears in the net0 step
    net_step = next(step for step in steps if step.title == "Configure static MAC for Apple services")
    assert cfg.static_mac in net_step.command


def test_build_plan_disables_balloon() -> None:
    """macOS doesn't support balloon driver — must be disabled."""
    steps = build_plan(_cfg("sequoia"))
    create = next(step for step in steps if step.title == "Create VM shell")
    assert "--balloon 0" in create.command


def test_build_plan_enables_guest_agent() -> None:
    """QEMU guest agent should be enabled for graceful shutdown."""
    steps = build_plan(_cfg("sequoia"))
    create = next(step for step in steps if step.title == "Create VM shell")
    assert "--agent enabled=1" in create.command


def test_build_plan_uses_vmxnet3_nic() -> None:
    """NIC must be vmxnet3 (native macOS driver) with firewall=0."""
    steps = build_plan(_cfg("sequoia"))
    create = next(step for step in steps if step.title == "Create VM shell")
    assert "vmxnet3" in create.command
    assert "firewall=0" in create.command
    assert "virtio,bridge" not in create.command


def test_build_plan_uses_virtio0_disk() -> None:
    """Main disk must be virtio0 for better I/O performance."""
    steps = build_plan(_cfg("sequoia"))
    disk = next(step for step in steps if step.title == "Create main disk")
    assert "--virtio0" in disk.command
    assert "--sata0" not in disk.command


def test_build_plan_import_detects_pve_version() -> None:
    """Import steps must detect PVE 9.x 'qm disk import' vs legacy 'qm importdisk'."""
    steps = build_plan(_cfg("sequoia"))
    oc_import = next(step for step in steps if step.title == "Import and attach OpenCore disk")
    assert "IMPORT_CMD" in oc_import.command
    assert "qm disk import" in oc_import.command
    rec_import = next(step for step in steps if step.title == "Import and attach macOS recovery")
    assert "IMPORT_CMD" in rec_import.command


def test_build_plan_apple_services_uses_vmxnet3(monkeypatch) -> None:
    """Apple Services static MAC step must also use vmxnet3."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    cfg = _cfg("sequoia")
    cfg.apple_services = True
    steps = build_plan(cfg)
    net_step = next(step for step in steps if step.title == "Configure static MAC for Apple services")
    assert "vmxnet3" in net_step.command
    assert "firewall=0" in net_step.command


def test_build_plan_oc_validates_losetup(monkeypatch) -> None:
    """OC build script must validate losetup output and retry partprobe."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    cmd = build.command
    assert '[ -b "$SRC_LOOP" ]' in cmd
    assert '[ -b "$DEST_LOOP" ]' in cmd
    assert "for _i in" in cmd
    assert "mountpoint -q" in cmd


def test_build_plan_oc_cleans_stale_dest_loops(monkeypatch) -> None:
    """OC build must clean stale loops for both source ISO and destination disk."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    cmd = build.command
    # Must have at least 2 losetup -j calls (source + dest stale cleanup)
    assert cmd.count("losetup -j") >= 2


def test_build_plan_recovery_validates_losetup(monkeypatch) -> None:
    """Recovery stamp step must validate losetup, check partitions, and verify mount."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    stamp = next(step for step in steps if step.title == "Stamp recovery with Apple icon flavour")
    cmd = stamp.command
    assert '[ -b "$RLOOP" ]' in cmd
    assert "mountpoint -q" in cmd
    assert "losetup -j" in cmd


def test_build_plan_oc_error_messages_actionable(monkeypatch) -> None:
    """Error paths must include diagnostic hints (modprobe loop or losetup -a)."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    cmd = build.command
    assert "modprobe loop" in cmd or "losetup -a" in cmd


def test_build_plan_blkid_fallback_warns(monkeypatch) -> None:
    """When blkid finds no vfat partition, a WARN must be emitted before raw mount."""
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "detect_cpu_info", lambda: _cpu(vendor="Intel", needs_emulated=False))
    steps = build_plan(_cfg("sequoia"))
    build = next(step for step in steps if step.title == "Build OpenCore boot disk")
    cmd = build.command
    assert "WARN" in cmd


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
