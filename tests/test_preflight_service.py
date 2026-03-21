from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from osx_proxmox_next.preflight import PreflightCheck
from osx_proxmox_next.services import preflight_service as svc_module
from osx_proxmox_next.services.preflight_service import run_preflight_worker


def _make_check(name: str, ok: bool) -> PreflightCheck:
    return PreflightCheck(name=name, ok=ok, details="")


def test_all_checks_pass_no_install(monkeypatch):
    checks = [_make_check("qemu", True), _make_check("ovmf", True)]
    monkeypatch.setattr(svc_module, "run_preflight", lambda: checks)
    monkeypatch.setattr(svc_module, "has_missing_build_deps", lambda c: False)
    result = run_preflight_worker()
    assert result == checks


def test_missing_deps_triggers_install_and_reruns(monkeypatch):
    first = [_make_check("qemu", False)]
    second = [_make_check("qemu", True)]
    calls = {"run": 0}

    def _run():
        calls["run"] += 1
        return first if calls["run"] == 1 else second

    install_called = []
    monkeypatch.setattr(svc_module, "run_preflight", _run)
    monkeypatch.setattr(svc_module, "has_missing_build_deps", lambda c: True)
    monkeypatch.setattr(
        svc_module,
        "install_missing_packages",
        lambda on_output=None: (True, ["qemu"]),
    )

    result = run_preflight_worker()
    assert calls["run"] == 2
    assert result == second


def test_install_fails_returns_first_checks(monkeypatch):
    checks = [_make_check("qemu", False)]
    monkeypatch.setattr(svc_module, "run_preflight", lambda: checks)
    monkeypatch.setattr(svc_module, "has_missing_build_deps", lambda c: True)
    monkeypatch.setattr(
        svc_module,
        "install_missing_packages",
        lambda on_output=None: (False, []),
    )
    result = run_preflight_worker()
    assert result == checks


def test_install_succeeds_but_no_packages(monkeypatch):
    checks = [_make_check("qemu", False)]
    run_calls = {"n": 0}

    def _run():
        run_calls["n"] += 1
        return checks

    monkeypatch.setattr(svc_module, "run_preflight", _run)
    monkeypatch.setattr(svc_module, "has_missing_build_deps", lambda c: True)
    monkeypatch.setattr(
        svc_module,
        "install_missing_packages",
        lambda on_output=None: (True, []),
    )
    result = run_preflight_worker()
    assert run_calls["n"] == 1  # no re-run when pkgs list empty


def test_on_status_callback_called(monkeypatch):
    checks = [_make_check("qemu", False)]
    messages = []

    def _install(on_output=None):
        if on_output:
            on_output("installing qemu...")
        return True, ["qemu"]

    monkeypatch.setattr(svc_module, "run_preflight", lambda: checks)
    monkeypatch.setattr(svc_module, "has_missing_build_deps", lambda c: True)
    monkeypatch.setattr(svc_module, "install_missing_packages", _install)

    run_preflight_worker(on_status=messages.append)
    assert "installing qemu..." in messages


def test_no_on_status_does_not_crash(monkeypatch):
    checks = [_make_check("qemu", False)]

    def _install(on_output=None):
        if on_output:
            on_output("msg")
        return True, ["qemu"]

    monkeypatch.setattr(svc_module, "run_preflight", lambda: checks)
    monkeypatch.setattr(svc_module, "has_missing_build_deps", lambda c: True)
    monkeypatch.setattr(svc_module, "install_missing_packages", _install)
    run_preflight_worker(on_status=None)  # must not raise
