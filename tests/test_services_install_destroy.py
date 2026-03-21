"""Unit tests for install_service and destroy_service."""
from __future__ import annotations

from pathlib import Path

import pytest

from osx_proxmox_next.domain import PlanStep
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.rollback import RollbackSnapshot
from osx_proxmox_next.services.install_service import run_dry_apply, run_live_install
from osx_proxmox_next.services.destroy_service import run_destroy_worker


def _make_step(title: str = "Test step") -> PlanStep:
    return PlanStep(title=title, argv=["echo", "hello"])


def _make_apply_result(ok: bool = True) -> ApplyResult:
    return ApplyResult(
        ok=ok,
        results=[],
        log_path=Path("/tmp/test.log"),
    )


def _make_snapshot(vmid: int = 901) -> RollbackSnapshot:
    return RollbackSnapshot(vmid=vmid, path=Path("/tmp/snapshots") / f"vm-{vmid}.conf")


# ---------------------------------------------------------------------------
# run_dry_apply
# ---------------------------------------------------------------------------


def test_run_dry_apply_calls_apply_plan_with_execute_false(monkeypatch) -> None:
    captured = {}

    def fake_apply_plan(steps, execute=False, on_step=None):
        captured["execute"] = execute
        return _make_apply_result()

    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        fake_apply_plan,
    )
    steps = [_make_step()]
    run_dry_apply(steps)
    assert captured["execute"] is False


def test_run_dry_apply_returns_apply_result(monkeypatch) -> None:
    expected = _make_apply_result(ok=True)

    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        lambda steps, execute=False, on_step=None: expected,
    )
    result = run_dry_apply([_make_step()])
    assert isinstance(result, ApplyResult)
    assert result.ok is True


def test_run_dry_apply_passes_steps_to_apply_plan(monkeypatch) -> None:
    captured = {}

    def fake_apply_plan(steps, execute=False, on_step=None):
        captured["steps"] = steps
        return _make_apply_result()

    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        fake_apply_plan,
    )
    steps = [_make_step("Alpha"), _make_step("Beta")]
    run_dry_apply(steps)
    assert len(captured["steps"]) == 2


def test_run_dry_apply_passes_on_step_callback(monkeypatch) -> None:
    captured = {}

    def fake_apply_plan(steps, execute=False, on_step=None):
        captured["on_step"] = on_step
        return _make_apply_result()

    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        fake_apply_plan,
    )
    callback = lambda i, n, s, r: None
    run_dry_apply([_make_step()], on_step=callback)
    assert captured["on_step"] is callback


def test_run_dry_apply_empty_steps(monkeypatch) -> None:
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(),
    )
    result = run_dry_apply([])
    assert isinstance(result, ApplyResult)


# ---------------------------------------------------------------------------
# run_live_install
# ---------------------------------------------------------------------------


def test_run_live_install_calls_apply_plan_with_execute_true(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.create_snapshot",
        lambda vmid: _make_snapshot(vmid),
    )

    def fake_apply_plan(steps, execute=False, on_step=None):
        captured["execute"] = execute
        return _make_apply_result()

    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        fake_apply_plan,
    )
    run_live_install(901, [_make_step()])
    assert captured["execute"] is True


def test_run_live_install_returns_tuple(monkeypatch) -> None:
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.create_snapshot",
        lambda vmid: _make_snapshot(vmid),
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(),
    )
    result = run_live_install(901, [_make_step()])
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_run_live_install_first_element_is_apply_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.create_snapshot",
        lambda vmid: _make_snapshot(vmid),
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(ok=True),
    )
    apply_result, _ = run_live_install(901, [_make_step()])
    assert isinstance(apply_result, ApplyResult)
    assert apply_result.ok is True


def test_run_live_install_second_element_is_snapshot(monkeypatch) -> None:
    snapshot = _make_snapshot(901)
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.create_snapshot",
        lambda vmid: snapshot,
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(),
    )
    _, returned_snapshot = run_live_install(901, [_make_step()])
    assert returned_snapshot is snapshot


def test_run_live_install_creates_snapshot_with_correct_vmid(monkeypatch) -> None:
    captured = {}

    def fake_create_snapshot(vmid):
        captured["vmid"] = vmid
        return _make_snapshot(vmid)

    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.create_snapshot",
        fake_create_snapshot,
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.install_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(),
    )
    run_live_install(555, [_make_step()])
    assert captured["vmid"] == 555


# ---------------------------------------------------------------------------
# run_destroy_worker
# ---------------------------------------------------------------------------


def test_run_destroy_worker_returns_tuple(monkeypatch) -> None:
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.create_snapshot",
        lambda vmid: _make_snapshot(vmid),
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.build_destroy_plan",
        lambda vmid, purge=False: [_make_step()],
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(),
    )
    result = run_destroy_worker(901)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_run_destroy_worker_calls_apply_plan_with_execute_true(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.create_snapshot",
        lambda vmid: _make_snapshot(vmid),
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.build_destroy_plan",
        lambda vmid, purge=False: [_make_step()],
    )

    def fake_apply_plan(steps, execute=False, on_step=None):
        captured["execute"] = execute
        return _make_apply_result()

    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.apply_plan",
        fake_apply_plan,
    )
    run_destroy_worker(901)
    assert captured["execute"] is True


def test_run_destroy_worker_passes_purge_to_build_destroy_plan(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.create_snapshot",
        lambda vmid: _make_snapshot(vmid),
    )

    def fake_build_destroy_plan(vmid, purge=False):
        captured["purge"] = purge
        return [_make_step()]

    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.build_destroy_plan",
        fake_build_destroy_plan,
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(),
    )
    run_destroy_worker(901, purge=True)
    assert captured["purge"] is True


def test_run_destroy_worker_snapshot_has_correct_vmid(monkeypatch) -> None:
    captured = {}

    def fake_create_snapshot(vmid):
        captured["vmid"] = vmid
        return _make_snapshot(vmid)

    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.create_snapshot",
        fake_create_snapshot,
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.build_destroy_plan",
        lambda vmid, purge=False: [_make_step()],
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(),
    )
    run_destroy_worker(777)
    assert captured["vmid"] == 777


def test_run_destroy_worker_result_is_apply_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.create_snapshot",
        lambda vmid: _make_snapshot(vmid),
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.build_destroy_plan",
        lambda vmid, purge=False: [_make_step()],
    )
    monkeypatch.setattr(
        "osx_proxmox_next.services.destroy_service.apply_plan",
        lambda steps, execute=False, on_step=None: _make_apply_result(ok=False),
    )
    apply_result, snapshot = run_destroy_worker(901)
    assert isinstance(apply_result, ApplyResult)
    assert apply_result.ok is False
