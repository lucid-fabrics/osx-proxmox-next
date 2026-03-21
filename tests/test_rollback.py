import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from osx_proxmox_next.infrastructure import CommandResult, ProxmoxAdapter
from osx_proxmox_next.rollback import create_snapshot, rollback_hints, RollbackSnapshot


def _fake_adapter(ok: bool, output: str = "") -> ProxmoxAdapter:
    adapter = MagicMock(spec=ProxmoxAdapter)
    adapter.run.return_value = CommandResult(ok=ok, returncode=0 if ok else 1, output=output)
    return adapter


# --- create_snapshot ---

def test_create_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    adapter = _fake_adapter(ok=True, output="boot: order=ide2\n")
    snap = create_snapshot(900, adapter=adapter)
    assert snap.vmid == 900
    assert snap.path.exists()
    assert "boot: order=ide2" in snap.path.read_text()


def test_create_snapshot_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    adapter = _fake_adapter(ok=False, output="")
    snap = create_snapshot(900, adapter=adapter)
    assert snap.path.exists()
    assert "No existing VM config captured" in snap.path.read_text()


def test_create_snapshot_writes_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = "cores: 4\nmemory: 8192\n"
    adapter = _fake_adapter(ok=True, output=config)
    snap = create_snapshot(101, adapter=adapter)
    assert snap.path.read_text() == config


def test_create_snapshot_different_vmids(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    adapter = _fake_adapter(ok=True, output="cfg")
    snap1 = create_snapshot(100, adapter=adapter)
    snap2 = create_snapshot(200, adapter=adapter)
    assert snap1.vmid == 100
    assert snap2.vmid == 200
    assert snap1.path != snap2.path


def test_create_snapshot_path_contains_vmid(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    adapter = _fake_adapter(ok=True, output="x")
    snap = create_snapshot(555, adapter=adapter)
    assert "555" in snap.path.name


def test_create_snapshot_creates_parent_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    adapter = _fake_adapter(ok=True, output="x")
    snap = create_snapshot(100, adapter=adapter)
    assert snap.path.parent.is_dir()


def test_create_snapshot_calls_qm_config(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    adapter = _fake_adapter(ok=True, output="cfg")
    create_snapshot(777, adapter=adapter)
    adapter.run.assert_called_once_with(["qm", "config", "777"])


def test_create_snapshot_uses_proxmox_service_when_no_adapter(monkeypatch, tmp_path):
    import osx_proxmox_next.services.proxmox_service as svc_module
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    mock_adapter = _fake_adapter(ok=True, output="fallback config")
    monkeypatch.setattr(svc_module, "_pve", mock_adapter)
    snap = create_snapshot(888)
    assert snap.path.exists()
    assert "fallback config" in snap.path.read_text()


# --- rollback_hints ---

def test_rollback_hints_format():
    snap = RollbackSnapshot(vmid=900, path=Path("/tmp/snap.conf"))
    hints = rollback_hints(snap)
    assert len(hints) == 3
    assert "900" in hints[1]
    assert str(snap.path) in hints[0]


def test_rollback_hints_all_strings():
    snap = RollbackSnapshot(vmid=42, path=Path("/tmp/x.conf"))
    hints = rollback_hints(snap)
    assert all(isinstance(h, str) for h in hints)


def test_rollback_hints_contain_destroy_command():
    snap = RollbackSnapshot(vmid=123, path=Path("/tmp/y.conf"))
    hints = rollback_hints(snap)
    combined = " ".join(hints)
    assert "destroy" in combined or "purge" in combined


def test_rollback_snapshot_fields():
    p = Path("/tmp/snap.conf")
    snap = RollbackSnapshot(vmid=500, path=p)
    assert snap.vmid == 500
    assert snap.path == p
