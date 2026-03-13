from pathlib import Path

from osx_proxmox_next import preflight
from osx_proxmox_next.preflight import (
    PreflightCheck,
    run_preflight,
    has_missing_build_deps,
    find_missing_packages,
    install_missing_packages,
)


def test_preflight_has_expected_checks() -> None:
    checks = run_preflight()
    names = [check.name for check in checks]
    assert "qm available" in names
    assert "pvesm available" in names
    assert "/dev/kvm present" in names
    assert "dmg2img available" in names
    assert "sgdisk available" in names
    assert "partprobe available" in names
    assert "losetup available" in names
    assert "mkfs.fat available" in names
    assert "blkid available" in names
    assert "KVM ignore_msrs" in names
    assert "IOMMU enabled" in names
    assert "initcall_blacklist" in names
    assert len(checks) >= 15


def test_find_binary_checks_common_system_paths(monkeypatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: str(self) == "/usr/sbin/qm",
    )
    assert preflight._find_binary("qm") == "/usr/sbin/qm"


def test_is_root_uses_effective_uid(monkeypatch) -> None:
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    assert preflight._is_root() is True


def test_is_root_non_root(monkeypatch):
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 1000)
    assert preflight._is_root() is False


def test_is_root_attribute_error(monkeypatch):
    def raise_attr():
        raise AttributeError("no geteuid")
    monkeypatch.setattr(preflight.os, "geteuid", raise_attr)
    assert preflight._is_root() is False


def test_find_binary_not_found(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert preflight._find_binary("nonexistent") is None


def test_find_binary_which_found(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _cmd: "/usr/bin/qm")
    assert preflight._find_binary("qm") == "/usr/bin/qm"


def test_check_ignore_msrs_present(tmp_path):
    """When kvm.conf has ignore_msrs=Y, check must pass."""
    kvm_conf = tmp_path / "kvm.conf"
    kvm_conf.write_text("options kvm ignore_msrs=Y\n")
    check = preflight._check_ignore_msrs(kvm_conf=kvm_conf)
    assert check.name == "KVM ignore_msrs"
    assert check.ok is True
    assert "ignore_msrs=Y" in check.details


def test_check_ignore_msrs_missing(tmp_path):
    """When kvm.conf doesn't exist, check must fail."""
    check = preflight._check_ignore_msrs(kvm_conf=tmp_path / "nonexistent")
    assert check.name == "KVM ignore_msrs"
    assert check.ok is False
    assert "ignore_msrs=Y" in check.details


def test_check_ignore_msrs_present_but_wrong_value(tmp_path):
    """When kvm.conf exists but lacks ignore_msrs=Y, check must fail."""
    kvm_conf = tmp_path / "kvm.conf"
    kvm_conf.write_text("options kvm report_ignored_msrs=N\n")
    check = preflight._check_ignore_msrs(kvm_conf=kvm_conf)
    assert check.ok is False


def test_check_iommu_enabled(tmp_path):
    """When cmdline has intel_iommu=on, check reports it."""
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("BOOT_IMAGE=/boot/vmlinuz intel_iommu=on\n")
    check = preflight._check_iommu(cmdline_path=cmdline)
    assert check.name == "IOMMU enabled"
    assert check.ok is True
    assert "GPU passthrough" in check.details


def test_check_iommu_not_enabled(tmp_path):
    """When cmdline lacks IOMMU, check is still ok (informational)."""
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("BOOT_IMAGE=/boot/vmlinuz quiet\n")
    check = preflight._check_iommu(cmdline_path=cmdline)
    assert check.ok is True
    assert "not detected" in check.details


def test_check_initcall_blacklist_present(tmp_path):
    """When cmdline has initcall_blacklist=sysfb_init, check reports it."""
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("BOOT_IMAGE=/boot/vmlinuz initcall_blacklist=sysfb_init\n")
    check = preflight._check_initcall_blacklist(cmdline_path=cmdline)
    assert check.name == "initcall_blacklist"
    assert check.ok is True
    assert "sysfb_init" in check.details


def test_check_initcall_blacklist_absent(tmp_path):
    """When cmdline lacks initcall_blacklist, check is still ok (informational)."""
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("BOOT_IMAGE=/boot/vmlinuz quiet\n")
    check = preflight._check_initcall_blacklist(cmdline_path=cmdline)
    assert check.ok is True
    assert "not set" in check.details


def test_build_binary_missing_shows_install_hint(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    checks = run_preflight()
    dmg2img = [c for c in checks if c.name == "dmg2img available"][0]
    assert dmg2img.ok is False
    assert "apt install dmg2img" in dmg2img.details
    sgdisk = [c for c in checks if c.name == "sgdisk available"][0]
    assert sgdisk.ok is False
    assert "apt install gdisk" in sgdisk.details
    partprobe = [c for c in checks if c.name == "partprobe available"][0]
    assert partprobe.ok is False
    assert "apt install parted" in partprobe.details


# ── has_missing_build_deps tests ─────────────────────────────────


def test_has_missing_build_deps_none_missing():
    checks = [
        PreflightCheck("dmg2img available", True, "/usr/bin/dmg2img"),
        PreflightCheck("sgdisk available", True, "/usr/sbin/sgdisk"),
        PreflightCheck("KVM ignore_msrs", False, "missing"),
    ]
    assert has_missing_build_deps(checks) is False


def test_has_missing_build_deps_some_missing():
    checks = [
        PreflightCheck("dmg2img available", False, "Not found"),
        PreflightCheck("sgdisk available", True, "/usr/sbin/sgdisk"),
    ]
    assert has_missing_build_deps(checks) is True


def test_has_missing_build_deps_ignores_non_build_checks():
    checks = [
        PreflightCheck("qm available", False, "not found"),
        PreflightCheck("KVM ignore_msrs", False, "missing"),
    ]
    assert has_missing_build_deps(checks) is False


# ── Auto-install tests ──────────────────────────────────────────


def test_find_missing_packages_all_present(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    assert find_missing_packages() == []


def test_find_missing_packages_some_missing(monkeypatch):
    present = {"sgdisk", "losetup", "mkfs.fat", "blkid"}
    monkeypatch.setattr(
        preflight.shutil, "which",
        lambda cmd: f"/usr/bin/{cmd}" if cmd in present else None,
    )
    monkeypatch.setattr(Path, "exists", lambda self: False)
    missing = find_missing_packages()
    assert "dmg2img" in missing
    assert "parted" in missing
    assert "gdisk" not in missing


def test_find_missing_packages_no_duplicates(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    missing = find_missing_packages()
    assert len(missing) == len(set(missing))


def test_install_missing_packages_not_root(monkeypatch):
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 1000)
    ok, pkgs = install_missing_packages()
    assert ok is False
    assert pkgs == []


def test_install_missing_packages_none_missing(monkeypatch):
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    monkeypatch.setattr(preflight.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    ok, pkgs = install_missing_packages()
    assert ok is True
    assert pkgs == []


def test_install_missing_packages_success(monkeypatch):
    from osx_proxmox_next.infrastructure import CommandResult
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    present = {"sgdisk", "losetup", "mkfs.fat", "blkid"}
    monkeypatch.setattr(
        preflight.shutil, "which",
        lambda cmd: f"/usr/bin/{cmd}" if cmd in present else None,
    )
    monkeypatch.setattr(Path, "exists", lambda self: False)
    messages = []
    captured_argv = []

    class FakeAdapter:
        def run(self, argv):
            captured_argv.extend(argv)
            return CommandResult(ok=True, returncode=0, output="")

    ok, pkgs = install_missing_packages(on_output=messages.append, adapter=FakeAdapter())
    assert ok is True
    assert "dmg2img" in pkgs
    assert "parted" in pkgs
    assert any("Installing" in m for m in messages)
    assert captured_argv[0] == "apt-get"
    assert "install" in captured_argv
    assert "-y" in captured_argv


def test_install_missing_packages_apt_failure(monkeypatch):
    from osx_proxmox_next.infrastructure import CommandResult
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    monkeypatch.setattr(preflight.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    class FakeAdapter:
        def run(self, argv):
            return CommandResult(ok=False, returncode=1, output="E: Unable to locate package")

    ok, pkgs = install_missing_packages(adapter=FakeAdapter())
    assert ok is False
    assert pkgs == []


def test_install_missing_packages_command_not_found(monkeypatch):
    from osx_proxmox_next.infrastructure import CommandResult
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 0)
    monkeypatch.setattr(preflight.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    class FakeAdapter:
        def run(self, argv):
            return CommandResult(ok=False, returncode=127, output="Command not found: apt-get")

    ok, pkgs = install_missing_packages(adapter=FakeAdapter())
    assert ok is False
    assert pkgs == []
