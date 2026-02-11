from pathlib import Path

from osx_proxmox_next.diagnostics import build_health_status, recovery_guide, export_log_bundle


def test_recovery_guide_contains_core_steps() -> None:
    lines = recovery_guide("boot failed")
    assert any("Preflight" in line or "preflight" in line for line in lines)


def test_health_status_has_counts() -> None:
    status = build_health_status()
    assert status.total >= status.score


def test_recovery_guide_boot_reason():
    lines = recovery_guide("boot problem")
    assert any("boot order" in line.lower() for line in lines)


def test_recovery_guide_asset_reason():
    lines = recovery_guide("asset missing")
    assert any("installer" in line.lower() or "recovery" in line.lower() for line in lines)


def test_recovery_guide_iso_reason():
    lines = recovery_guide("iso not found")
    assert any("installer" in line.lower() or "recovery" in line.lower() for line in lines)


def test_recovery_guide_generic():
    lines = recovery_guide("unknown error")
    assert len(lines) == 3
    assert all("boot order" not in line.lower() for line in lines)
    assert all("re-stage" not in line.lower() for line in lines)


def test_export_log_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    logs_dir = tmp_path / "generated" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "test.log").write_text("log content")
    bundle = export_log_bundle()
    assert bundle.exists()
    assert bundle.suffix == ".gz"
    assert "support-bundle" in bundle.name
