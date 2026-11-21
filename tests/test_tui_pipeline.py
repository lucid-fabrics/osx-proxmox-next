"""The Build pipeline: every phase, every failure path, the detached driver."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from osx_proxmox_next.assets import AssetCheck
from osx_proxmox_next.domain import PlanStep, VmConfig
from osx_proxmox_next.preflight import PreflightCheck
from osx_proxmox_next.rollback import RollbackSnapshot
from osx_proxmox_next.services.pipeline import (
    ACTIVE, FAILED, OK, PENDING, SKIPPED, BuildPipeline, PipelineDeps, phase_title)
from osx_proxmox_next.services.unattended_service import DriverState

CONFIG = VmConfig(vmid=105, name="macos-sequoia", macos="sequoia", cores=8, memory_mb=16384, disk_gb=128,
                  bridge="vmbr0", storage="local-lvm")
STEPS = [PlanStep("one", ["true"]), PlanStep("two", ["true"])]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 1
        return self.now


def result(ok=True):
    return SimpleNamespace(ok=ok, log_path=Path("/tmp/x.log"))


def make(**over):
    calls: list[str] = []
    deps = PipelineDeps(
        preflight=lambda: [PreflightCheck("qm available", True, "")],
        assets=lambda cfg: [AssetCheck("OpenCore", Path("/x"), True, "")],
        download=lambda cfg, todo, on_progress, force_opencore=False: calls.append("download") or [],
        plan=lambda cfg: STEPS,
        dry_run=lambda steps, on_step=None: (on_step and on_step(1, 2, steps[0], None), result())[1],
        live=lambda vmid, steps, on_step=None: (on_step and on_step(1, 2, steps[0], SimpleNamespace(ok=True, returncode=0)),
                                                (result(), RollbackSnapshot(vmid, Path("/tmp/s"))))[1],
        vm_exists=lambda vmid: False,
        destroy=lambda vmid, purge=False: calls.append("destroy") or (result(), None),
        spawn=lambda vmid, disk: "/var/log/osx-next-unattended-105.log",
        clock=Clock(),
    )
    for key, value in over.items():
        setattr(deps, key, value)
    return deps, calls


def statuses(pipe):
    return {p.id: p.status for p in pipe.phases}


def test_a_hands_off_build_runs_to_the_detached_driver():
    deps, _ = make()
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    changes = []
    pipe.on_change = lambda: changes.append(1)
    pipe.run()
    assert statuses(pipe) == {"check": OK, "download": OK, "rehearse": OK, "create": OK, "install": ACTIVE, "ready": PENDING}
    assert pipe.safe_to_close and not pipe.running and pipe.driver_log.endswith("105.log")
    assert pipe.plan_steps == STEPS and changes and pipe.elapsed() > 0
    assert any("Rehearsing all 2 steps" in line for line in pipe.log_lines)
    assert pipe.phase("download").note == "already here"


def test_a_manual_install_ends_after_create_vm():
    deps, _ = make()
    pipe = BuildPipeline(CONFIG, False, deps=deps)
    pipe.run()
    st = statuses(pipe)
    assert st["install"] == SKIPPED and st["ready"] == OK and pipe.done and not pipe.safe_to_close
    assert any("Proxmox console" in line for line in pipe.log_lines)


def test_a_failed_host_check_stops_at_phase_one():
    deps, calls = make(preflight=lambda: [PreflightCheck("qm available", False, ""), PreflightCheck("dmg2img available", False, "")])
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    assert statuses(pipe)["check"] == FAILED and pipe.host_failed and pipe.can_retry
    assert pipe.failure == "2 host checks failed: qm is not installed, dmg2img is not installed"
    assert statuses(pipe)["download"] == PENDING and not pipe.running
    deps2, _ = make(preflight=lambda: [PreflightCheck("qm available", False, "")])
    one = BuildPipeline(CONFIG, True, deps=deps2)
    one.run()
    assert one.failure == "qm is not installed" and "available" not in one.failure


def test_missing_assets_are_downloaded_and_the_plan_is_rebuilt():
    missing = AssetCheck("OpenCore image", Path("/x"), False, "", downloadable=True)
    stale = AssetCheck("recovery", Path("/y"), True, "", downloadable=True, stale=True)
    seen = {}

    def download(cfg, todo, on_progress, force_opencore=False):
        seen["todo"] = [a.name for a in todo]
        on_progress("OpenCore", 40)
        assert "Downloading OpenCore... 40%" == pipe.download_text
        on_progress("OpenCore", 100)
        assert pipe.download_text == "Finalizing OpenCore..."
        return []

    deps, _ = make(assets=lambda cfg: [missing, stale], download=download)
    pipe = BuildPipeline(CONFIG, True, force_download=True, deps=deps)
    pipe.run()
    assert seen["todo"] == ["OpenCore image", "recovery"] and statuses(pipe)["download"] == OK
    assert pipe.download_text == ""


def test_force_download_refreshes_assets_that_are_present():
    seen = {}
    deps, _ = make(assets=lambda cfg: [AssetCheck("OpenCore", Path("/x"), True, "", downloadable=True)],
                   download=lambda cfg, todo, on_progress, force_opencore=False: seen.update(f=force_opencore, n=len(todo)) or [])
    BuildPipeline(CONFIG, True, force_download=True, deps=deps).run()
    assert seen == {"f": True, "n": 1}


def test_a_download_that_cannot_happen_names_the_missing_file():
    deps, _ = make(assets=lambda cfg: [AssetCheck("installer", Path("/x"), False, "", downloadable=False)])
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    assert pipe.failed == "download" and "Missing assets: installer" in pipe.failure and pipe.can_retry


def test_download_errors_fail_the_phase():
    deps, _ = make(assets=lambda cfg: [AssetCheck("OpenCore", Path("/x"), False, "", downloadable=True)],
                   download=lambda cfg, todo, on_progress, force_opencore=False: ["OpenCore: network down"])
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    assert pipe.failed == "download" and "OpenCore: network down" in pipe.failure


def test_a_plan_that_cannot_be_built_fails_before_anything_changes():
    def bad(cfg):
        raise ValueError("no such macOS")

    deps, _ = make(plan=bad)
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    assert pipe.failed == "download" and "could not be built" in pipe.failure
    assert statuses(pipe)["create"] == PENDING


def test_a_failed_rehearsal_changes_nothing_on_the_host():
    lived = []
    deps, _ = make(dry_run=lambda steps, on_step=None: result(False),
                   live=lambda *a, **k: lived.append(1))
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    assert pipe.failed == "rehearse" and "The rehearsal failed" in pipe.failure and not lived


def test_a_failed_create_shows_the_rollback_hints_and_retry_removes_the_half_built_vm():
    snapshot = RollbackSnapshot(105, Path("/tmp/snap"))
    state = {"exists": False, "fail": True}
    destroyed = []

    def live(vmid, steps, on_step=None):
        state["exists"] = True
        return result(not state["fail"]), snapshot

    deps, _ = make(live=live, vm_exists=lambda vmid: state["exists"],
                   destroy=lambda vmid, purge=False: destroyed.append((vmid, purge)) or (result(), None))
    pipe = BuildPipeline(CONFIG, False, deps=deps)
    pipe.run()
    assert pipe.failed == "create" and pipe.rollback and "Creating the VM failed" in pipe.failure
    assert pipe.live_log == Path("/tmp/x.log")
    state["fail"] = False
    pipe.run("create")
    assert destroyed == [(105, True)] and pipe.done and pipe.failed == ""


def test_an_existing_vm_id_is_never_touched():
    destroyed = []
    deps, _ = make(vm_exists=lambda vmid: True, destroy=lambda *a, **k: destroyed.append(1))
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    assert pipe.failed == "create" and "already exists" in pipe.failure and not destroyed


def test_a_rebuilt_pipeline_cannot_run():
    pipe = BuildPipeline.from_driver(105, DriverState(seen=["recovery"], alive=True, exists=True, started_at=5.0))
    with pytest.raises(RuntimeError):
        pipe.run()


def test_the_driver_log_finishes_the_install_phase():
    deps, _ = make()
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    pipe.apply_driver_state(DriverState(seen=["recovery", "erase"], alive=True, exists=True))
    assert statuses(pipe)["install"] == ACTIVE
    pipe.apply_driver_state(DriverState(seen=["recovery", "ready"], finished=True, alive=False, exists=True))
    assert statuses(pipe)["install"] == OK and pipe.done


def test_a_driver_error_fails_the_install_phase_and_is_not_retryable_by_the_screen():
    deps, _ = make()
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    pipe.apply_driver_state(DriverState(seen=["recovery"], error="Timed out after 900s", exists=True))
    assert pipe.failed == "install" and pipe.failure == "Timed out after 900s"


def test_a_driver_that_vanished_fails_after_a_grace_period():
    clock = Clock()
    deps, _ = make(clock=clock)
    pipe = BuildPipeline(CONFIG, True, deps=deps)
    pipe.run()
    gone = DriverState(seen=["recovery"], events=[("13:00:00", "waiting")], alive=False, exists=True)
    pipe.apply_driver_state(gone)
    assert pipe.failed == ""  # still inside the grace period
    clock.now += 100
    pipe.apply_driver_state(gone)
    assert pipe.failed == "install" and "Last line: waiting" in pipe.failure
    other, _ = make(clock=Clock())
    quiet = BuildPipeline(CONFIG, True, deps=other)
    quiet.run()
    quiet.deps.clock.now += 100
    quiet.apply_driver_state(DriverState(alive=False, exists=True))
    assert "no output yet" in quiet.failure


def test_apply_driver_state_does_nothing_outside_the_install_phase():
    deps, _ = make()
    pipe = BuildPipeline(CONFIG, False, deps=deps)
    pipe.apply_driver_state(DriverState(finished=True, exists=True))
    assert not pipe.done


def test_a_pipeline_rebuilt_from_the_log_shows_the_earlier_phases_done():
    state = DriverState(seen=["recovery", "erase", "installer"], started_at=1234.0, alive=True, exists=True,
                        events=[("13:40:02", "x")])
    pipe = BuildPipeline.from_driver(105, state)
    assert [p.status for p in pipe.phases[:4]] == [OK] * 4 and pipe.phase("install").status == ACTIVE
    assert pipe.started_at == 1234.0 and pipe.safe_to_close and pipe.vmid == 105
    assert not pipe.can_retry and pipe.driver is state


@pytest.mark.parametrize("pid,status,expect", [
    ("check", "active", "Check this host"), ("check", "ok", "Checked this host"),
    ("download", "ok", "Downloaded macOS"), ("rehearse", "active", "Rehearse all 14 steps"),
    ("rehearse", "ok", "Rehearsed all 14 steps"), ("create", "ok", "Created VM 105"),
    ("create", "pending", "Create VM 105"), ("install", "active", "Installing macOS"),
    ("install", "ok", "Installed macOS"), ("install", "pending", "Install macOS"), ("ready", "ok", "Ready"),
])
def test_phase_titles(pid, status, expect):
    assert phase_title(pid, status, 105, 14) == expect


def test_phase_title_without_a_step_count():
    assert phase_title("rehearse", "active", 105, 0) == "Rehearse all steps"


def test_phase_duration_runs_while_active():
    from osx_proxmox_next.services.pipeline import PhaseState
    ph = PhaseState("check")
    assert ph.duration(50) == 0.0
    ph.started = 10.0
    assert ph.duration(50) == 40.0
    ph.ended = 20.0
    assert ph.duration(50) == 10.0
