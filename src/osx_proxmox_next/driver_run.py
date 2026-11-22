"""Run an update of one Mac's driver, put it back, finish an interrupted one. Every path ends by releasing the lock."""

from __future__ import annotations

from . import driver_amd, driver_engine as engine, driver_intel, driver_nvidia, driver_snapshot
from .driver_core import DriverError, RunEnv, RunResult, Updater, stop_mac
from .driver_plan import DriverPlan, prepare
from .driver_targets import Choice

__all__ = ["finish_interrupted", "put_back", "remove_safety_snapshot", "run_update"]

RUNNERS = {"nvidia": driver_nvidia.run, "intel": driver_intel.run, "amd": driver_amd.run}
FAILED_TAIL = " The Mac is as it was. u puts it back to the snapshot%s."


def _record(u: Updater) -> None:
    """The new components go into the record; what the Mac had before is kept for 'put it back'."""
    now = engine.now_iso()
    kept = {c["name"]: c for c in engine.read_record(u.vmid, u.env.base).get("components", [])}
    for name, comp in {**u.result.installed, **u.plan.target.components}.items():
        kept[name] = engine.component(name, comp["version"], comp["tag"], comp.get("sha256", ""),
                                      comp.get("source", ""), now)
    previous = [engine.component(n["name"], n["version"]) for n in u.state.get("previous", [])]
    engine.write_record(u.vmid, u.plan.kind, list(kept.values()), u.env.base, previous, u.state.get("snapshot", ""))


def _execute(u: Updater) -> RunResult:
    result = u.result
    try:
        RUNNERS[u.plan.kind](u)
        _record(u)
        if u.state.get("snapshot"):
            removed, failed = driver_snapshot.prune_older(u.pve, u.vmid, u.state["snapshot"])
            if removed:
                u.say("Removed older safety snapshots: " + ", ".join(removed))
            if failed:
                result.notes.append("Could not remove these older safety snapshots: " + ", ".join(failed))
        u.state.update(phase="done", error="")
        u.persist()   # stays until the first boot question is answered: a reopened screen asks it again
        result.ok = True
    except Exception as exc:  # noqa: BLE001 - every failure ends the same way: the Mac is put right, the run is kept
        _abandon(u, exc)
        result.error = str(exc) + (FAILED_TAIL % (" (" + u.state["snapshot"] + ")" if u.state.get("snapshot") else ""))
    return result


def _abandon(u: Updater, exc: Exception) -> None:
    """A failed run: the card goes back if it is off, the failure is recorded, the Mac is left for the user."""
    u.fail(str(exc))
    if u.state.get("original") and "hostpci0" not in u.pve.qm("config", str(u.vmid)).output:
        try:
            u.restore_card()
        except Exception:  # noqa: BLE001 - reported by the failure itself; u puts everything back
            pass


def run_update(plan: DriverPlan, env: RunEnv, resume: dict | None = None) -> RunResult:
    """Do the update *plan* describes. Refuses what the plan refused; never changes a Mac that is not its own."""
    if not plan.ok:
        raise DriverError(" ".join(plan.refusals))
    if plan.nothing_to_do:
        raise DriverError(engine.UP_TO_DATE)
    if not plan.snapshot_ok and not env.no_snapshot:
        raise DriverError(engine.NO_SNAPSHOT_WARNING)
    if not engine.acquire_lock(plan.vmid, env.base):
        raise DriverError(engine.REFUSE_LOCKED)
    try:
        updater = Updater(plan, env, resume)
        updater.persist()
        return _execute(updater)
    finally:
        engine.release_lock(plan.vmid, env.base)


def finish_interrupted(vmid: int, env: RunEnv) -> RunResult:
    """Continue the update a closed terminal or a lost connection left behind."""
    state = engine.unfinished(vmid, env.base)
    if not state:
        raise DriverError("There is no unfinished driver update for this Mac.")
    choice = state.get("choice", {})
    plan = prepare(vmid, Choice(choice.get("NullMoth", ""), choice.get("WhateverXe", ""),
                                choice.get("WhateverXe Graphics", "")), env.pve, reinstall=True,
                   get=env.get, base=env.base, resume=state)
    return run_update(plan, env, resume=state)


def put_back(vmid: int, env: RunEnv) -> str:
    """Roll the Mac back to the newest safety snapshot of this tool and put the record back. Returns the snapshot."""
    if not engine.acquire_lock(vmid, env.base):
        raise DriverError(engine.REFUSE_LOCKED)
    try:
        state = engine.read_update(vmid, env.base)
        snaps = driver_snapshot.list_tool(env.pve, vmid)
        name = state.get("snapshot") if state.get("snapshot") in snaps else (snaps[-1] if snaps else "")
        if not name:
            raise DriverError(engine.REFUSE_NOTHING_TO_UNDO)
        stop_mac(env, vmid)
        driver_snapshot.rollback(env.pve, vmid, name)
        engine.revert_record(vmid, env.base)
        engine.clear_update(vmid, env.base)
        res = env.pve.qm("start", str(vmid))
        env.on_event(f"Put the Mac back to {name}" + ("" if res.ok else ". It did not start: start it from your Macs page"))
        return name
    except driver_snapshot.SnapshotError as exc:
        raise DriverError(str(exc)) from exc
    finally:
        engine.release_lock(vmid, env.base)


def remove_safety_snapshot(vmid: int, env: RunEnv) -> str:
    """Remove the newest safety snapshot of this tool (the offer after a good first boot). Returns its name."""
    snaps = driver_snapshot.list_tool(env.pve, vmid)
    if not snaps:
        raise DriverError(engine.REFUSE_NOTHING_TO_UNDO)
    try:
        driver_snapshot.remove(env.pve, vmid, snaps[-1])
    except driver_snapshot.SnapshotError as exc:
        raise DriverError(str(exc)) from exc
    return snaps[-1]

