from pathlib import Path

from osx_proxmox_next import preflight
from osx_proxmox_next.preflight import run_preflight


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
    assert len(checks) >= 11


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
