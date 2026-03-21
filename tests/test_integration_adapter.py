import subprocess

import pytest

from osx_proxmox_next.executor import apply_plan, ApplyResult
from osx_proxmox_next.infrastructure import ProxmoxAdapter, CommandResult
from osx_proxmox_next.domain import PlanStep


def _fake_ok(argv, **kw):
    return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")


def _fake_fail(argv, **kw):
    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="error output")


# --- ProxmoxAdapter ---

def test_adapter_qm_wraps_binary(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = ProxmoxAdapter()
    result = adapter.qm("status", "900")
    assert result.ok is True
    assert calls[0][:2] == ["qm", "status"]


def test_adapter_pvesm_wraps_binary(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (calls.append(argv), _fake_ok(argv))[1])
    adapter = ProxmoxAdapter()
    result = adapter.pvesm("status")
    assert result.ok is True
    assert calls[0][:2] == ["pvesm", "status"]


def test_adapter_pvesh_wraps_binary(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (calls.append(argv), _fake_ok(argv))[1])
    result = ProxmoxAdapter().pvesh("get", "/nodes")
    assert calls[0][:2] == ["pvesh", "get"]


def test_adapter_run_nonzero_returns_ok_false(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_fail)
    result = ProxmoxAdapter().run(["qm", "start", "999"])
    assert result.ok is False
    assert result.returncode == 1
    assert "error output" in result.output


def test_adapter_run_file_not_found(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    result = ProxmoxAdapter().run(["no-such-bin"])
    assert result.ok is False
    assert result.returncode == 127
    assert "no-such-bin" in result.output


def test_adapter_run_timeout(monkeypatch) -> None:
    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["qm"], timeout=300)
    monkeypatch.setattr(subprocess, "run", _timeout)
    result = ProxmoxAdapter().run(["qm", "status", "100"])
    assert result.ok is False
    assert result.returncode == 124


def test_adapter_captures_both_stdout_and_stderr(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="out\n", stderr="err\n"),
    )
    result = ProxmoxAdapter().run(["qm", "config", "100"])
    assert "out" in result.output
    assert "err" in result.output


# --- apply_plan ---

def test_apply_plan_executes_argv_without_shell(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="done", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    steps = [
        PlanStep("Step 1", ["qm", "status", "901"]),
        PlanStep("Step 2", ["qm", "start", "901"]),
    ]
    result = apply_plan(steps, execute=True)
    assert result.ok is True
    assert calls == [["qm", "status", "901"], ["qm", "start", "901"]]


def test_apply_plan_dry_run_does_not_execute(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (calls.append(argv), _fake_ok(argv))[1])
    steps = [PlanStep("Step 1", ["qm", "start", "902"])]
    result = apply_plan(steps, execute=False)
    assert calls == []
    assert result.ok is True


def test_apply_plan_stops_on_failure(monkeypatch) -> None:
    calls: list[list[str]] = []

    def _selective(argv, **kw):
        calls.append(argv)
        rc = 1 if "start" in argv else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="fail" if rc else "")

    monkeypatch.setattr(subprocess, "run", _selective)
    steps = [
        PlanStep("Fail step", ["qm", "start", "903"]),
        PlanStep("Should not run", ["qm", "stop", "903"]),
    ]
    result = apply_plan(steps, execute=True)
    assert result.ok is False
    assert len(calls) == 1  # stopped after first failure


def test_apply_plan_empty_steps(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (calls.append(argv), _fake_ok(argv))[1])
    result = apply_plan([], execute=True)
    assert result.ok is True
    assert calls == []
