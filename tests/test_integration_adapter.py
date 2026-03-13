import subprocess

from osx_proxmox_next.executor import apply_plan
from osx_proxmox_next.infrastructure import ProxmoxAdapter
from osx_proxmox_next.planner import PlanStep


def test_adapter_qm_wraps_binary(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, capture_output, text, check, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = ProxmoxAdapter()
    result = adapter.qm("status", "900")
    assert result.ok is True
    assert calls[0][:2] == ["qm", "status"]


def test_apply_plan_executes_argv_without_shell(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, capture_output, text, check, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="done", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    steps = [
        PlanStep("Step 1", ["qm", "status", "901"]),
        PlanStep("Step 2", ["qm", "start", "901"]),
    ]
    result = apply_plan(steps, execute=True, log_dir=tmp_path)
    assert result.ok is True
    assert calls == [["qm", "status", "901"], ["qm", "start", "901"]]
