"""The bash twin of tests/test_driver_dry_run.py: --dry-run never changes anything, and the Finish plan says what the run does."""
from __future__ import annotations

import json

import pytest

from osx_proxmox_next import driver_engine as engine
from test_driver_parity import (  # noqa: F401 - fixtures and helpers
    NV, SNAP, STUB_NULLMOTH, host, needs_bash4, py_lines, write_record)

READ_ONLY = {"config", "status", "listsnapshot"}
FLAGS = ("--yes", "--no-snapshot", "--accept-nullmoth-risk", "--reinstall")
# every flag alone, none, and all together (each case starts a fresh bash with Python engine calls: the full product is on the Python side)
COMBOS = [(), *[(f,) for f in FLAGS], FLAGS]


def cut_off(host, before_snapshot):
    """The state of an update a SIGTERM stopped before or after its snapshot step (the Mac is stopped)."""
    rows = ["pending"] * 7
    if not before_snapshot:
        rows[:2] = ["done", "done"]
    steps = [{"id": i, "title": t, "done": d, "state": st} for (i, t), (_i, d), st in zip(
        engine.step_titles("nvidia", "NullMoth v2.9"), engine.step_titles("nvidia", "NullMoth v2.9", done=True), rows)]
    state = {"kind": "nvidia", "phase": "failed", "snapshot": "" if before_snapshot else SNAP, "steps": steps,
             "choice": {"NullMoth": "v2.9"}, "card": "0000:01:00,pcie=0"}
    engine.write_update(109, state, str(host.state))
    return state


def files(host):
    return {p.name: p.read_bytes() for p in host.state.iterdir() if p.is_file() and not p.name.endswith(".lock")}


def only_reads(host):
    bad = [c for c in host.calls() if c.split()[0] not in READ_ONLY]
    assert not bad, f"a dry run called qm {bad}"


@needs_bash4
@pytest.mark.parametrize("flags", COMBOS, ids=lambda c: "+".join(c) or "plain")
def test_bash_finish_dry_run_changes_nothing_with_every_flag_combination(host, flags):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    cut_off(host, before_snapshot=True)
    before = files(host)
    out = host.run(STUB_NULLMOTH + f"driver_update_command 109 --finish --dry-run {' '.join(flags)}\n").stdout
    assert "Finish the NVIDIA driver update" in out and "DRY RUN - nothing is changed." in out
    assert files(host) == before
    only_reads(host)


@needs_bash4
@pytest.mark.parametrize("flags", COMBOS, ids=lambda c: "+".join(c) or "plain")
def test_bash_update_dry_run_changes_nothing_with_every_flag_combination(host, flags):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    before = files(host)
    out = host.run(STUB_NULLMOTH + f"driver_update_command 109 --dry-run {' '.join(flags)}\n").stdout
    assert "DRY RUN - nothing is changed." in out
    assert files(host) == before
    only_reads(host)


@needs_bash4
@pytest.mark.parametrize("args", ["--dry-run", "--dry-run --yes", "--yes --dry-run", "-y --dry-run"])
def test_bash_undo_dry_run_changes_nothing(host, args):
    write_record(host, "nvidia", NullMoth="v2.8.1")
    (host.qm_dir / "snaps").write_text(SNAP + "\n")
    before = files(host)
    out = host.run(f"driver_undo_command 109 {args}\n").stdout
    assert "DRY RUN - nothing is changed." in out and SNAP in out
    assert files(host) == before
    only_reads(host)


@needs_bash4
def test_bash_undo_refuses_an_option_it_does_not_know_instead_of_ignoring_it(host):
    res = host.run("driver_undo_command 109 --dryrun\n", expect=1)
    assert "Unknown option: --dryrun" in res.stdout + res.stderr and host.calls() == []


def plan_lines(host, state):
    snippet = (STUB_NULLMOTH + "DRIVER_VERSIONS='{\"NullMoth\": \"v2.9\"}'\nDRIVER_TARGET=$(cat <<'J'\n" + json.dumps(NV) + "\nJ\n)\n"
               "driver_print_plan 109 macos-sequoia nvidia '{\"NullMoth\": \"v2.8.1\"}' \"$DRIVER_TARGET\" osx-next-driver-NEW yes no sequoia yes\n")
    out = host.run(snippet).stdout.splitlines()
    return out, py_lines("nvidia", {"NullMoth": "v2.8.1"}, NV, "osx-next-driver-NEW", resume=state)


@needs_bash4
def test_bash_finish_of_a_run_cut_off_before_its_snapshot_says_it_saves_one(host):
    out, py = plan_lines(host, cut_off(host, before_snapshot=True))
    assert out == py
    assert "A snapshot called osx-next-driver-NEW is saved first." in out and "  1. Save a snapshot" in out
    assert not [l for l in out if "No new snapshot" in l or "ran without one" in l]


@needs_bash4
def test_bash_finish_of_a_run_cut_off_after_its_snapshot_says_no_new_one_is_saved(host):
    out, py = plan_lines(host, cut_off(host, before_snapshot=False))
    assert out == py
    assert f"No new snapshot is saved: {SNAP} from before the update stays." in out
    assert not [l for l in out if "is saved first" in l or "Save a snapshot" in l]


@needs_bash4
def test_bash_finish_says_a_run_without_a_snapshot_cannot_be_undone(host):
    state = cut_off(host, before_snapshot=False)
    state.update(snapshot="", no_snapshot=True)
    engine.write_update(109, state, str(host.state))
    out, py = plan_lines(host, state)
    assert out == py and "Nothing can put this Mac back: this update has no driver snapshot." in out
