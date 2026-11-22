"""Run an update of one Mac's driver, undo it, finish an interrupted one. Every path ends by releasing the lock."""

from __future__ import annotations

from . import driver_amd, driver_disks, driver_engine as engine, driver_intel, driver_nvidia, driver_snapshot
from .driver_core import DriverError, RunEnv, RunResult, Updater, stop_mac
from .driver_plan import DriverPlan, prepare
from .driver_targets import Choice
from .gpu import parse_config
from .igpu_finish import FinishError, _drop_disk

__all__ = ["finish_interrupted", "put_back", "remove_safety_snapshot", "run_update", "undo_target"]

RUNNERS = {"nvidia": driver_nvidia.run, "intel": driver_intel.run, "amd": driver_amd.run}
FINISH_HINT = " Finish continues it (osx-next-cli driver-update --vmid %d --finish)."
UNDO_HINT = " driver-undo goes back to the snapshot of %s (%s)."


def _record(u: Updater) -> None:
    """The new components go into the record; what the Mac had before is kept for "undo the driver update"."""
    now = engine.now_iso()
    kept = {c["name"]: c for c in engine.read_record(u.vmid, u.env.base).get("components", [])}
    for name, comp in {**u.result.installed, **u.plan.target.components}.items():
        if name in u.result.kept:     # newer than the pin: the Mac keeps what it has
            if name not in kept and u.plan.installed.get(name):
                kept[name] = engine.component(name, u.plan.installed[name], source="disk", installed_at=now)
            continue
        kept[name] = engine.component(name, comp["version"], comp["tag"], comp.get("sha256", ""),
                                      comp.get("source", ""), now)
    previous = [engine.component(n["name"], n["version"]) for n in u.state.get("previous", [])]
    engine.write_record(u.vmid, u.plan.kind, list(kept.values()), u.env.base, previous, u.state.get("snapshot", ""),
                        u.state.get("card", ""), latest=u.plan.target.versions() if u.plan.target else None,
                        fingerprint=driver_disks.fingerprint(u.pve, u.live_cfg()))


def _execute(u: Updater) -> RunResult:
    result = u.result
    try:
        if u.resumed:
            drop_leftover_disks(u.pve, u.vmid, u.state, u.say)
        RUNNERS[u.plan.kind](u)
        _record(u)
        if u.state.get("snapshot"):        # one snapshot of this tool per Mac (also after the snapshot step): none left over
            u.replace_older_snapshots(u.state["snapshot"])
        u.state.update(phase="done", error="")
        if u.env.ask_first_boot:
            u.persist()   # stays until the first boot question is answered: a reopened screen asks it again
        else:
            engine.clear_update(u.vmid, u.env.base)   # a command line run asks nothing: nothing waits for an answer
        result.ok = True
    except Exception as exc:  # noqa: BLE001 - every failure ends the same way: the run is kept and says what it left
        result.error = _abandon(u, exc)
    return result


def drop_leftover_disks(pve, vmid: int, state: dict, say=lambda _m: None) -> None:
    """The recovery or payload disk of a run that a manual `qm rollback` left as an unused disk goes, and only those
    two: they are found by the volume ids the run wrote down, never by their look. A disk that a device map still holds
    stays and is told with the exact command: it is the Mac's wasted space, not a reason to stop what the Mac is about."""
    try:
        gone = driver_disks.drop_unused(pve, vmid, driver_disks.own_disks(state))
    except driver_disks.MapsHeld as exc:
        say(f"Could not remove the leftover disk of the update. {exc}")
        return
    if gone:
        say("Removed the leftover disk of the update: " + ", ".join(gone))


def _leftover_disks(u: Updater) -> None:
    """A failed staging leaves its recovery and payload disks on a stopped Mac; they would be orphaned by a rollback."""
    stage = next((r["state"] for r in u.state["steps"] if r["id"] == "stage"), "")
    if stage in ("active", "fail") and not u.running():
        for slot in ("sata1", "sata2"):
            _drop_disk(u.pve, u.vmid, slot)


def _abandon(u: Updater, exc: Exception) -> str:
    """A failed run: the card goes back if it is off, the failure is recorded with what it left behind. Returns the
    message for the user: the error, what changed, whether the Mac runs, and the two ways on."""
    u.fail(str(exc))
    if u.state.get("original") and "hostpci0" not in u.live_cfg():
        try:
            u.restore_card()
        except Exception:  # noqa: BLE001 - reported by the failure itself; Finish or undo puts everything back
            pass
    try:
        _leftover_disks(u)
    except Exception:  # noqa: BLE001 - a rollback drops what is left
        pass
    snap = u.state.get("snapshot", "")
    message = f"{exc} {engine.failure_text(u.state, u.running())}" + FINISH_HINT % u.vmid
    if snap:
        message += UNDO_HINT % (engine.snapshot_date(snap), snap)
    u.state["error"] = message
    u.persist()
    return message


def run_update(plan: DriverPlan, env: RunEnv, resume: dict | None = None) -> RunResult:
    """Do the update *plan* describes. Refuses what the plan refused; never changes a Mac that is not its own."""
    if not plan.ok:
        raise DriverError(" ".join(plan.refusals))
    if plan.nothing_to_do and not resume:
        raise DriverError(engine.UP_TO_DATE)
    past_snapshot = bool(resume and (resume.get("snapshot") or resume.get("no_snapshot")))
    if not plan.snapshot_ok and not env.no_snapshot and not past_snapshot:
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
    """Continue the update a closed terminal, a lost connection or a failure left behind, from the step it stopped at.
    The Mac is looked at again: it is stopped and its card taken off before anything is staged."""
    state = engine.unfinished(vmid, env.base)
    if not state:
        raise DriverError("There is no unfinished driver update for this Mac.")
    choice = state.get("choice", {})
    plan = prepare(vmid, Choice(choice.get("NullMoth", ""), choice.get("WhateverXe", ""),
                                choice.get("WhateverXe Graphics", "")), env.pve, reinstall=True,
                   get=env.get, base=env.base, resume=state)
    return run_update(plan, env, resume=state)


def undo_target(vmid: int, env: RunEnv) -> str:
    """The snapshot to go back to. Refuses when there is none, when a newer snapshot of somebody else stands in the way
    or when the card setup changed since. Nothing is changed."""
    res = env.pve.run(["qm", "listsnapshot", str(vmid)])
    listing = res.output if res.ok else ""
    state = engine.read_update(vmid, env.base)
    record = engine.read_record(vmid, env.base)
    snaps = engine.tool_snapshots(listing)
    name = next((n for n in (state.get("snapshot"), record.get("snapshot")) if n in snaps), snaps[-1] if snaps else "")
    if not name:
        raise DriverError(engine.REFUSE_NOTHING_TO_UNDO)
    newer = engine.newer_than(listing, name)
    if newer:
        raise DriverError(engine.REFUSE_NEWER_SNAPSHOTS % ", ".join(newer))
    saved = state["card"] if state and "card" in state else record.get("snapshot_card", "")
    current = parse_config(env.pve.qm("config", str(vmid)).output).get("hostpci0", "")
    if engine.card_changed(saved, current, interrupted=bool(state) and state.get("phase") != "done"):
        raise DriverError(engine.REFUSE_CARD_CHANGED)
    return name


def _drop_added_disks(vmid: int, env: RunEnv, name: str) -> None:
    """The recovery and payload disks a cut-off run imported after the snapshot are not in it, so a rollback would orphan
    their volumes: they go first."""
    now = parse_config(env.pve.qm("config", str(vmid)).output)
    then = parse_config(env.pve.qm("config", str(vmid), "--snapshot", name).output)
    for slot in ("sata1", "sata2"):
        if slot in now and slot not in then:
            _drop_disk(env.pve, vmid, slot, strict=True)


def _remove_used_snapshot(env: RunEnv, vmid: int, name: str) -> None:
    """After a good undo the snapshot it went back to is spent: at most one driver snapshot of this tool per Mac, so
    none is left. A failure to remove it is told, never fatal: the Mac is back."""
    try:
        driver_snapshot.remove(env.pve, vmid, name)
        env.on_event(f"Removed the driver snapshot {name}")
    except driver_snapshot.SnapshotError as exc:
        env.on_event(f"Could not remove the driver snapshot {name} ({exc}). Remove it with: qm delsnapshot {vmid} {name}")


def _step(env: RunEnv, sid: str, state: str, titles: dict[str, tuple[str, str]]) -> None:
    env.on_step(sid, state)
    if state != "active":
        env.on_event(titles[sid][1] if state == "done" else titles[sid][0] + " failed")


def put_back(vmid: int, env: RunEnv) -> str:
    """Undo the driver update: the Mac's disks go back to the newest driver snapshot of this tool and the record goes
    back to the previous version. Reports each step through env.on_step (stop, release, rollback, start). Returns the
    snapshot. The disks are released (no device map or loop device holds them) before anything is removed or rolled back."""
    if not engine.acquire_lock(vmid, env.base):
        raise DriverError(engine.REFUSE_LOCKED)
    titles = {sid: (now, past) for (sid, now), (_s, past) in zip(engine.undo_titles(), engine.undo_titles(done=True))}
    current, was_running = "", False
    try:
        name = undo_target(vmid, env)
        was_running = "stopped" not in env.pve.qm("status", str(vmid)).output
        current = "stop"
        _step(env, "stop", "active", titles)
        stop_mac(env, vmid)
        _step(env, "stop", "done", titles)
        current = "release"
        _step(env, "release", "active", titles)
        driver_snapshot.release_disks(env.pve, vmid)
        _drop_added_disks(vmid, env, name)
        _step(env, "release", "done", titles)
        current = "rollback"
        _step(env, "rollback", "active", titles)
        was_update = engine.read_update(vmid, env.base)
        driver_snapshot.rollback(env.pve, vmid, name)
        drop_leftover_disks(env.pve, vmid, was_update, env.on_event)      # a disk the update added and the snapshot lacks
        _remove_used_snapshot(env, vmid, name)
        engine.revert_record(vmid, env.base)
        engine.stamp_record(vmid, driver_disks.fingerprint(env.pve, parse_config(env.pve.qm("config", str(vmid)).output)), env.base)
        engine.clear_update(vmid, env.base)
        _step(env, "rollback", "done", titles)
        current = "start"
        _step(env, "start", "active", titles)
        res = env.pve.qm("start", str(vmid))
        _step(env, "start", "done" if res.ok else "fail", titles)
        env.on_event(f"Undid the driver update: the Mac is back to {name}"
                     + ("" if res.ok else ". It did not start: start it from your Macs page"))
        return name
    except Exception as exc:    # noqa: BLE001 - whatever stops an undo ends it the same way: the failed step, the Mac running again
        if current:      # a step was under way: it failed, and a Mac this undo stopped runs again unless Proxmox locked it
            env.on_step(current, "fail")
            if was_running and current != "stop" and "lock: rollback" not in env.pve.qm("config", str(vmid)).output:
                env.pve.qm("start", str(vmid))
        if isinstance(exc, DriverError):
            raise
        raise DriverError(str(exc)) from exc
    finally:
        engine.release_lock(vmid, env.base)


def remove_safety_snapshot(vmid: int, env: RunEnv, name: str = "") -> str:
    """Remove the driver snapshot of this tool the screen named (the offer after a good first boot), else the newest.
    Returns its name."""
    snaps = driver_snapshot.list_tool(env.pve, vmid)
    if not snaps or (name and name not in snaps):
        raise DriverError(engine.REFUSE_NOTHING_TO_UNDO)
    try:
        driver_snapshot.remove(env.pve, vmid, name or snaps[-1])
    except driver_snapshot.SnapshotError as exc:
        raise DriverError(str(exc)) from exc
    return name or snaps[-1]
