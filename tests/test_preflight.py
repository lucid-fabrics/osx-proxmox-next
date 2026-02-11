from pathlib import Path

from osx_proxmox_next import preflight
from osx_proxmox_next.preflight import run_preflight


def test_preflight_has_expected_checks() -> None:
    checks = run_preflight()
    names = [check.name for check in checks]
    assert "qm available" in names
    assert "pvesm available" in names
    assert "/dev/kvm present" in names
    assert len(checks) >= 6


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
