"""--dry-run never changes anything (Python side), and the Finish screen states what the Finish run will really do.

Live on v0.38.2 (pve-kids, VM 981): `driver-update --finish --dry-run` ran the whole Finish, and a run cut off before its
snapshot step was described as "No new snapshot is saved" while the run saved one and `r` undid it."""
from __future__ import annotations

import itertools

import pytest

from driver_fakes import DPve, comps
from osx_proxmox_next.infrastructure import CommandResult
from osx_proxmox_next import cli, driver_cli, driver_core, driver_engine as engine, driver_plan, driver_run, driver_state
from osx_proxmox_next.driver_core import RunEnv, RunResult, Updater
from osx_proxmox_next.driver_targets import Target
from test_driver_cli import NEW_SNAP, host, run  # noqa: F401 - fixture and helper

READ_ONLY = ("config", "status", "listsnapshot")


class ReadOnlyPve(DPve):
    """A Proxmox that fails the test on any call that could change something."""

    def qm(self, *a):
        assert a[0] in READ_ONLY, f"a dry run called qm {' '.join(a)}"
        return super().qm(*a)

    def run(self, argv):
        if argv[0] == "bash":      # the plan may read the versions off the OpenCore disk (a read only mount), never write
            calls = [l for l in argv[2].splitlines() if l.startswith("OC_KEXT") is False and '"$OC_MNT/EFI/OC"' in l]
            assert calls and all(" versions " in c or " marker " in c for c in calls), f"a dry run wrote to the OpenCore disk: {calls}"
            assert "-o ro" in argv[2], "a dry run mounted the OpenCore disk read write"
            return super().run(argv)
        assert argv[0] in ("qm", "pvesm", "kpartx", "dmsetup", "losetup", "lvs"), f"a dry run ran {argv[0]}"   # the release of read maps
        if argv[0] == "kpartx":
            assert argv[1] == "-d", f"a dry run ran {' '.join(argv)}"
            return CommandResult(True, 0, "")
        return super().run(argv)


def cut_off(before_snapshot: bool) -> dict:
    """The update of VM 109 that a SIGTERM stopped, before or after its snapshot step. The Mac is stopped."""
    state = driver_state.new_update("nvidia", "macos-sequoia", "v2.9", "NullMoth v2.9", {"NullMoth": "v2.9"}, [], "0000:01:00,pcie=0",
                                    running=False)
    state["phase"] = "failed"
    if not before_snapshot:
        state["snapshot"] = NEW_SNAP
        for row in state["steps"][:1]:
            row["state"] = "done"
    engine.write_update(109, state)
    return state


def tree():
    base = engine.record_path(109).parent
    return {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()}


@pytest.fixture
def strict(host, monkeypatch):
    host["pve"] = ReadOnlyPve(snapshots=[NEW_SNAP])
    monkeypatch.setattr(driver_cli, "finish_interrupted", lambda *a, **k: pytest.fail("a dry run finished the update"))
    monkeypatch.setattr(driver_cli, "put_back", lambda *a, **k: pytest.fail("a dry run undid the update"))
    return host


FLAGS = ("--yes", "--no-snapshot", "--accept-nullmoth-risk", "--reinstall")
COMBOS = [c for n in range(len(FLAGS) + 1) for c in itertools.combinations(FLAGS, n)]


@pytest.mark.parametrize("finish", [False, True])
@pytest.mark.parametrize("flags", COMBOS, ids=lambda c: "+".join(c) or "plain")
def test_driver_update_dry_run_changes_nothing_with_every_flag_combination(strict, capsys, finish, flags):
    if finish:
        cut_off(before_snapshot=True)
    before = tree()
    argv = ["driver-update", "--vmid", "109", "--dry-run", *flags] + (["--finish"] if finish else [])
    assert run(*argv) == 0
    out = capsys.readouterr().out
    assert "DRY RUN - nothing is changed." in out and ("Finish the NVIDIA driver update" in out) is finish
    assert strict["ran"] == [] and tree() == before


@pytest.mark.parametrize("yes", [False, True])
def test_driver_undo_dry_run_changes_nothing(strict, capsys, yes):
    before = tree()
    assert run("driver-undo", "--vmid", "109", "--dry-run", *(["--yes"] if yes else [])) == 0
    out = capsys.readouterr().out
    assert "snapshot of" in out and "DRY RUN - nothing is changed." in out and tree() == before
    assert strict["pve"].rollbacks == []


def test_a_dry_run_finish_of_nothing_unfinished_says_so_and_changes_nothing(strict, capsys):
    assert run("driver-update", "--vmid", "109", "--finish", "--dry-run") == 2
    assert "There is no unfinished driver update for this Mac." in capsys.readouterr().out


# ── the Finish says what the Finish does ─────────────────────────────

def finish_plan(host):
    plan, state = driver_run.finish_plan(109, RunEnv(host["pve"], base=None))
    return plan, state


def test_a_run_cut_off_before_its_snapshot_says_the_finish_saves_one(host):
    cut_off(before_snapshot=True)
    plan, _state = finish_plan(host)
    lines = plan.lines()
    assert f"A snapshot called {plan.snapshot} is saved first." in lines
    assert not [l for l in lines if "No new snapshot" in l or "ran without one" in l]
    assert "Afterwards r undoes the driver update (osx-next-cli driver-undo)." in lines and plan.snapshot_pending()
    assert "  1. Save a snapshot" in lines


def test_a_run_cut_off_after_its_snapshot_says_no_new_one_is_saved(host):
    cut_off(before_snapshot=False)
    plan, _state = finish_plan(host)
    lines = plan.lines()
    assert f"No new snapshot is saved: {NEW_SNAP} from before the update stays." in lines
    assert not [l for l in lines if "is saved first" in l or "Save a snapshot" in l] and not plan.snapshot_pending()


def test_a_run_that_went_on_without_a_snapshot_says_nothing_can_put_the_mac_back(host):
    state = cut_off(before_snapshot=False)
    state["snapshot"], state["no_snapshot"] = "", True
    engine.write_update(109, state)
    lines = finish_plan(host)[0].lines()
    assert "No new snapshot is saved: this update ran without one." in lines
    assert "Nothing can put this Mac back: this update has no driver snapshot." in lines
    assert not [l for l in lines if "Afterwards r undoes" in l]


def test_the_finish_warns_when_the_storage_cannot_take_the_snapshot_it_still_has_to_save(host):
    cut_off(before_snapshot=True)
    host["pve"] = DPve(storage="Name Type Status\nnfsdir dir active\n", snapshots=[])
    host["pve"].cfg["ide0"] = "nfsdir:109/vm-109-disk-1.raw,media=disk"
    plan, _state = finish_plan(host)
    if plan.snapshot_ok:
        pytest.skip("the fake storage can take a snapshot")
    assert engine.NO_SNAPSHOT_WARNING in plan.lines() and not [l for l in plan.lines() if "is saved first" in l]


@pytest.mark.parametrize("before", [True, False])
def test_the_lines_match_what_the_run_does(host, before):
    """The promise and the deed come from one step list: a snapshot is saved exactly when the lines say so."""
    state = cut_off(before_snapshot=before)
    plan, _state = finish_plan(host)
    said_saved = any("is saved first" in l for l in plan.lines())
    pve = host["pve"]
    Updater(plan, RunEnv(pve), resume=state).snapshot()
    assert (plan.snapshot in pve.snapshots) is said_saved is before


def test_the_screen_facts_and_the_cli_plan_are_the_same_lines(host):
    cut_off(before_snapshot=True)
    plan, _state = finish_plan(host)
    assert plan.finish_lines() == [l for l in plan.lines() if l in plan.finish_lines()] == plan.finish_lines()
    assert engine.finish_facts("nvidia", "", "", plan.snapshot)[2] == f"A snapshot called {plan.snapshot} is saved first."
    assert engine.finish_facts("amd", "", "", "x", False)[1] == engine.NO_SNAPSHOT_WARNING
