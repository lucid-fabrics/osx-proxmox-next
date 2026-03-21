"""Unit tests for download_service.run_download_worker."""
from __future__ import annotations

from pathlib import Path

import pytest

from osx_proxmox_next.assets import AssetCheck
from osx_proxmox_next.domain import VmConfig
from osx_proxmox_next.downloader import DownloadError
from osx_proxmox_next.services.download_service import run_download_worker


def _make_config(iso_dir: str = "/tmp/iso") -> VmConfig:
    return VmConfig(
        vmid=901,
        name="macos-test",
        macos="sequoia",
        cores=8,
        memory_mb=16384,
        disk_gb=128,
        bridge="vmbr0",
        storage="local-lvm",
        iso_dir=iso_dir,
    )


def _asset(name: str, downloadable: bool = True) -> AssetCheck:
    return AssetCheck(
        name=name,
        path=Path("/tmp/not-exists.iso"),
        ok=False,
        hint="",
        downloadable=downloadable,
    )


def _noop_progress(phase: str, pct: int) -> None:
    pass


# ---------------------------------------------------------------------------
# Happy path — downloads succeed
# ---------------------------------------------------------------------------


def test_run_download_worker_empty_missing_returns_no_errors(monkeypatch) -> None:
    errors = run_download_worker(_make_config(), [], _noop_progress)
    assert errors == []


def test_run_download_worker_opencore_calls_download_opencore(monkeypatch) -> None:
    called = []

    def fake_download_opencore(macos, dest_dir, on_progress=None):
        called.append(("opencore", macos))

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_opencore",
        fake_download_opencore,
    )
    errors = run_download_worker(
        _make_config(), [_asset("OpenCore image")], _noop_progress
    )
    assert errors == []
    assert any(c[0] == "opencore" for c in called)


def test_run_download_worker_recovery_calls_download_recovery(monkeypatch) -> None:
    called = []

    def fake_download_recovery(macos, dest_dir, on_progress=None):
        called.append(("recovery", macos))

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_recovery",
        fake_download_recovery,
    )
    errors = run_download_worker(
        _make_config(), [_asset("recovery image")], _noop_progress
    )
    assert errors == []
    assert any(c[0] == "recovery" for c in called)


def test_run_download_worker_installer_calls_download_recovery(monkeypatch) -> None:
    called = []

    def fake_download_recovery(macos, dest_dir, on_progress=None):
        called.append("recovery")

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_recovery",
        fake_download_recovery,
    )
    errors = run_download_worker(
        _make_config(), [_asset("installer image")], _noop_progress
    )
    assert errors == []
    assert "recovery" in called


# ---------------------------------------------------------------------------
# Error handling — DownloadError is caught, not raised
# ---------------------------------------------------------------------------


def test_run_download_worker_opencore_download_error_returns_error_string(
    monkeypatch,
) -> None:
    def bad_download_opencore(macos, dest_dir, on_progress=None):
        raise DownloadError("network timeout")

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_opencore",
        bad_download_opencore,
    )
    errors = run_download_worker(
        _make_config(), [_asset("OpenCore image")], _noop_progress
    )
    assert len(errors) == 1
    assert "OpenCore" in errors[0]
    assert "network timeout" in errors[0]


def test_run_download_worker_recovery_download_error_returns_error_string(
    monkeypatch,
) -> None:
    def bad_download_recovery(macos, dest_dir, on_progress=None):
        raise DownloadError("server unreachable")

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_recovery",
        bad_download_recovery,
    )
    errors = run_download_worker(
        _make_config(), [_asset("recovery image")], _noop_progress
    )
    assert len(errors) == 1
    assert "Recovery" in errors[0]
    assert "server unreachable" in errors[0]


def test_run_download_worker_does_not_raise_on_download_error(monkeypatch) -> None:
    def bad_download_opencore(macos, dest_dir, on_progress=None):
        raise DownloadError("fail")

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_opencore",
        bad_download_opencore,
    )
    # Should not raise — errors are returned as list
    result = run_download_worker(
        _make_config(), [_asset("OpenCore image")], _noop_progress
    )
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Non-downloadable assets are skipped
# ---------------------------------------------------------------------------


def test_run_download_worker_skips_non_downloadable_assets(monkeypatch) -> None:
    called = []

    def fake_opencore(macos, dest_dir, on_progress=None):
        called.append("opencore")

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_opencore",
        fake_opencore,
    )
    errors = run_download_worker(
        _make_config(), [_asset("OpenCore image", downloadable=False)], _noop_progress
    )
    assert errors == []
    assert called == []


# ---------------------------------------------------------------------------
# Progress callback is invoked
# ---------------------------------------------------------------------------


def test_run_download_worker_progress_callback_called(monkeypatch) -> None:
    progress_calls = []

    def fake_download_opencore(macos, dest_dir, on_progress=None):
        from osx_proxmox_next.downloader import DownloadProgress
        if on_progress:
            on_progress(DownloadProgress(phase="opencore", downloaded=50, total=100))

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_opencore",
        fake_download_opencore,
    )

    def capture_progress(phase, pct):
        progress_calls.append((phase, pct))

    run_download_worker(
        _make_config(), [_asset("OpenCore image")], capture_progress
    )
    assert len(progress_calls) >= 1
    assert progress_calls[0][1] == 50


def test_run_download_worker_uses_config_iso_dir(monkeypatch) -> None:
    seen_dirs = []

    def fake_download_opencore(macos, dest_dir, on_progress=None):
        seen_dirs.append(dest_dir)

    monkeypatch.setattr(
        "osx_proxmox_next.services.download_service.download_opencore",
        fake_download_opencore,
    )
    cfg = _make_config(iso_dir="/custom/iso/path")
    run_download_worker(cfg, [_asset("OpenCore image")], _noop_progress)
    assert seen_dirs[0] == Path("/custom/iso/path")
