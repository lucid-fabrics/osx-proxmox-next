import subprocess

from osx_proxmox_next.executor import apply_plan
from osx_proxmox_next.infrastructure import ProxmoxAdapter
from osx_proxmox_next.planner import PlanStep


def test_apply_plan_dry_run_creates_success_results() -> None:
    steps = [PlanStep(title="Echo", argv=["echo", "hello"])]
    result = apply_plan(steps, execute=False)
    assert result.ok is True
    assert result.results[0].ok is True
    assert result.log_path.exists()


def test_apply_plan_live_with_callback(monkeypatch):
    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = ProxmoxAdapter()
    steps = [
        PlanStep("Step A", ["echo", "a"]),
        PlanStep("Step B", ["echo", "b"]),
    ]
    callback_log = []

    def on_step(idx, total, step, result):
        callback_log.append((idx, total, step.title, result))

    result = apply_plan(steps, execute=True, adapter=adapter, on_step=on_step)
    assert result.ok is True
    assert len(result.results) == 2
    # Each step fires callback twice: once before (result=None), once after
    assert len(callback_log) == 4
    assert callback_log[0][3] is None  # before first step
    assert callback_log[1][3] is not None  # after first step
    assert callback_log[1][3].ok is True


def test_apply_plan_live_failure_aborts(monkeypatch):
    call_count = [0]

    def fake_run(argv, **kw):
        call_count[0] += 1
        if call_count[0] == 2:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="error")
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = ProxmoxAdapter()
    steps = [
        PlanStep("Step OK", ["echo", "a"]),
        PlanStep("Step FAIL", ["false"]),
        PlanStep("Step Never", ["echo", "c"]),
    ]
    callback_log = []

    def on_step(idx, total, step, result):
        callback_log.append((idx, step.title, result))

    result = apply_plan(steps, execute=True, adapter=adapter, on_step=on_step)
    assert result.ok is False
    assert len(result.results) == 2
    assert result.results[0].ok is True
    assert result.results[1].ok is False


def test_apply_plan_dry_with_callback():
    steps = [PlanStep("Step X", ["echo", "x"])]
    callback_log = []

    def on_step(idx, total, step, result):
        callback_log.append((idx, result))

    result = apply_plan(steps, execute=False, on_step=on_step)
    assert result.ok is True
    # Dry: callback called twice per step (before + after)
    assert len(callback_log) == 2
    assert callback_log[0][1] is None
    assert callback_log[1][1] is not None


def test_apply_plan_dry_no_callback():
    """Cover branch where on_step is None during dry run (lines 50 and 57-59)."""
    steps = [PlanStep("Step Y", ["echo", "y"])]
    result = apply_plan(steps, execute=False, on_step=None)
    assert result.ok is True
    assert len(result.results) == 1
    assert result.results[0].ok is True


def test_apply_plan_live_no_callback(monkeypatch):
    """Cover branch where on_step is None during live run."""
    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = ProxmoxAdapter()
    steps = [PlanStep("Step Z", ["echo", "z"])]
    result = apply_plan(steps, execute=True, adapter=adapter, on_step=None)
    assert result.ok is True
    assert len(result.results) == 1
