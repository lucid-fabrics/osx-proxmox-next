from __future__ import annotations

import subprocess

import pytest

from osx_proxmox_next.infrastructure import ProxmoxAdapter
from osx_proxmox_next.services import proxmox_service as svc_module
from osx_proxmox_next.services.proxmox_service import get_proxmox_adapter


def _reset(monkeypatch):
    """Reset the module-level singleton before each test."""
    monkeypatch.setattr(svc_module, "_pve", None)


def test_returns_proxmox_adapter_instance(monkeypatch):
    _reset(monkeypatch)
    adapter = get_proxmox_adapter()
    assert isinstance(adapter, ProxmoxAdapter)


def test_singleton_same_instance_on_repeated_calls(monkeypatch):
    _reset(monkeypatch)
    a = get_proxmox_adapter()
    b = get_proxmox_adapter()
    assert a is b


def test_reset_creates_new_instance(monkeypatch):
    _reset(monkeypatch)
    a = get_proxmox_adapter()
    monkeypatch.setattr(svc_module, "_pve", None)
    b = get_proxmox_adapter()
    assert a is not b


def test_adapter_qm_delegates_to_run(monkeypatch):
    _reset(monkeypatch)
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = get_proxmox_adapter()
    result = adapter.qm("status", "100")
    assert result.ok is True
    assert calls[0] == ["qm", "status", "100"]


def test_adapter_run_returns_false_on_nonzero_exit(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="not found"),
    )
    result = get_proxmox_adapter().run(["qm", "status", "999"])
    assert result.ok is False
    assert result.returncode == 1


def test_adapter_run_handles_file_not_found(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    result = get_proxmox_adapter().run(["missing-binary"])
    assert result.ok is False
    assert result.returncode == 127


def test_adapter_run_handles_timeout(monkeypatch):
    _reset(monkeypatch)

    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["qm"], timeout=300)

    monkeypatch.setattr(subprocess, "run", _timeout)
    result = get_proxmox_adapter().run(["qm", "status", "100"])
    assert result.ok is False
    assert result.returncode == 124


def test_adapter_pvesm_delegates(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: (calls.append(argv), subprocess.CompletedProcess(argv, 0, stdout="", stderr=""))[1],
    )
    get_proxmox_adapter().pvesm("status")
    assert calls[0][:2] == ["pvesm", "status"]


def test_adapter_pvesh_delegates(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: (calls.append(argv), subprocess.CompletedProcess(argv, 0, stdout="", stderr=""))[1],
    )
    get_proxmox_adapter().pvesh("get", "/nodes")
    assert calls[0][:2] == ["pvesh", "get"]
