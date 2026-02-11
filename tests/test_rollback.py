import subprocess
from pathlib import Path

from osx_proxmox_next.rollback import create_snapshot, rollback_hints, RollbackSnapshot


def test_create_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="boot: order=ide2\n", stderr=""),
    )
    snap = create_snapshot(900)
    assert snap.vmid == 900
    assert snap.path.exists()
    assert "boot: order=ide2" in snap.path.read_text()


def test_create_snapshot_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
    )
    snap = create_snapshot(900)
    assert snap.path.exists()
    assert "No existing VM config captured" in snap.path.read_text()


def test_rollback_hints_format():
    snap = RollbackSnapshot(vmid=900, path=Path("/tmp/snap.conf"))
    hints = rollback_hints(snap)
    assert len(hints) == 3
    assert "900" in hints[1]
    assert str(snap.path) in hints[0]
