"""What a driver update leaves on disk: the step list of a run, the lock that keeps two runs of one Mac apart, and the
rules for continuing a run that was cut off. Standard library only.

driver_engine.py imports this module (the package) and the bash installer embeds it verbatim next to driver_engine.py
(tests/test_driver_parity.py diffs the two), so both twins have one implementation.

Per Mac, in STATE_DIR: <vmid>.lock (held with flock for the life of a run), <vmid>.update.json (the steps, what the
run changed, what it needs to continue or to go back).
"""
import fcntl
import json
import os
import time
from pathlib import Path

STATE_DIR = "/var/lib/osx-proxmox-next/drivers"
STEPS = {
    "nvidia": (("stop", "Stop the Mac", "Stopped the Mac"), ("snapshot", "Save a snapshot", "Saved a snapshot"),
               ("takeoff", "Take the card off", "Took the card off"), ("stage", "Stage {name}", "Staged {name}"),
               ("putback", "Put the card back", "Put the card back"), ("start", "Start the Mac", "Started the Mac"),
               ("verify", "Install the driver", "Driver installed")),
    "amd": (("stop", "Stop the Mac", "Stopped the Mac"), ("snapshot", "Save a snapshot", "Saved a snapshot"),
            ("replace", "Replace the kexts", "Replaced the kexts"), ("start", "Start the Mac", "Started the Mac")),
}
STEPS["intel"] = STEPS["nvidia"]
# "Undo the driver update": the same list shape, so the screen draws it like an update
UNDO_STEPS = (("stop", "Stop the Mac", "Stopped the Mac"), ("release", "Release the disks", "Released the disks"),
              ("rollback", "Put the snapshot back", "Put the snapshot back"), ("start", "Start the Mac", "Started the Mac"))
_HELD = {}


def state_dir(base=None):
    return Path(base or os.environ.get("DRIVER_STATE_DIR") or STATE_DIR)


def read_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def step_titles(kind, name="the driver", done=False):
    """(id, title) for each step of an update, in order. *done* gives the past-tense line the run list shows."""
    return [(sid, (past if done else now).format(name=name)) for sid, now, past in STEPS[kind]]


def undo_titles(done=False):
    """(id, title) for each step of an undo, in order."""
    return [(sid, past if done else now) for sid, now, past in UNDO_STEPS]


def plan_steps(kind, name="the driver", running=True):
    """The steps a plan lists: "Stop the Mac" only when the Mac is running (a stopped one has nothing to stop)."""
    return [(sid, title) for sid, title in step_titles(kind, name) if running or sid != "stop"]


# ── the lock ─────────────────────────────────────────────────────────

def lock_path(vmid, base=None):
    return state_dir(base) / ("%s.lock" % int(vmid))


def _boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _started(pid):
    try:
        return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def acquire_lock(vmid, base=None, pid=None):
    """True when this process now holds the lock of this Mac. The lock is an flock on the lock file, kept open until
    release_lock or the end of the process, so a crash or a host restart frees it and a reused pid cannot hold it.
    The file also says who holds it (pid, start time, boot id), for display only."""
    path = lock_path(vmid, base)
    if str(path) in _HELD:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    me = int(pid or os.getpid())
    handle.seek(0)
    handle.truncate()
    handle.write("%d\n%s\n%s\n" % (me, _started(me), _boot_id()))
    handle.flush()
    _HELD[str(path)] = handle
    return True


def release_lock(vmid, base=None):
    path = lock_path(vmid, base)
    handle = _HELD.pop(str(path), None)
    try:
        path.unlink()
    except OSError:
        pass
    if handle is not None:
        handle.close()


def lock_holder(vmid, base=None):
    """The pid of the process that holds the lock of this Mac, or 0 when nobody does (a lock file nobody holds, left
    by a crash or a restart, counts as free)."""
    path = lock_path(vmid, base)
    if str(path) in _HELD:
        return os.getpid()
    try:
        handle = open(path)
    except OSError:
        return 0
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                return max(1, int(handle.readline().strip()))
            except ValueError:
                return 1
        return 0


# ── the update state ─────────────────────────────────────────────────

def update_path(vmid, base=None):
    return state_dir(base) / ("%s.update.json" % int(vmid))


def read_update(vmid, base=None):
    """What an unfinished update left: kind, steps, snapshot, the settings it changed. {} when none."""
    return read_json(update_path(vmid, base))


def write_update(vmid, data, base=None):
    write_json(update_path(vmid, base), dict(data, vmid=int(vmid), updated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))


def clear_update(vmid, base=None):
    try:
        update_path(vmid, base).unlink()
    except OSError:
        pass


def unfinished(vmid, base=None):
    """The unfinished update of this Mac with nobody running it, or {}. A finished one (phase done) is only waiting for
    the first boot question to be answered."""
    data = read_update(vmid, base)
    return data if data and data.get("phase") != "done" and not lock_holder(vmid, base) else {}


def new_update(kind, name, label, step_name, choice, previous, card="", running=True):
    """The state of a run that has not started: every step pending. *card* is the hostpci0 the Mac has now. The run lists
    the steps its plan listed (plan_steps): no "Stop the Mac" row for a Mac that is stopped already."""
    shown = {sid for sid, _t in plan_steps(kind, step_name, running)}
    steps = [{"id": sid, "title": title, "done": done, "state": "pending"}
             for (sid, title), (_s, done) in zip(step_titles(kind, step_name), step_titles(kind, step_name, done=True))
             if sid in shown]
    return {"kind": kind, "name": name, "label": label, "steps": steps, "snapshot": "", "original": None, "events": [],
            "choice": choice, "previous": previous, "card": card, "resume_at": 0, "phase": "running", "error": ""}


def _row(state, sid):
    """The row of a step, None for a step this run does not list (the stop of a Mac that was stopped already)."""
    return next((r for r in state["steps"] if r["id"] == sid), None)


def resume_index(state):
    """Where a run that was cut off goes on: the first step that is not done."""
    return next((i for i, r in enumerate(state.get("steps", [])) if r["state"] != "done"), len(state.get("steps", [])))


CHANGES_DISKS = ("snapshot", "takeoff", "stage", "putback", "replace")     # the steps that need the Mac stopped


def changes_disks(state):
    """True while a step that changes the card or the disks is still to do: a run that only waits for the driver to
    install at the first boot has nothing to stop the Mac for."""
    return any(r["id"] in CHANGES_DISKS and r["state"] != "done" for r in state.get("steps", []))


def reset_for_resume(state, running=False):
    """A run that goes on from where it stopped starts its own list: the steps the earlier run finished stay done (and
    carry no time of this run), the others are pending again with no time, so nothing of the old run's clock shows.
    A Mac that runs now gets its "Stop the Mac" row even when the run was recorded without one (it was stopped at plan
    time or stopped by the run and started since), while a step that changes the card or the disks is still to do: the row goes in front of
    the first step still to do."""
    row = _row(state, "stop")
    if running and changes_disks(state) and (row is None or row["state"] == "done"):
        if row is not None:      # a stop that finished before the Mac was started again does not count: it goes in front of what is left
            state["steps"].remove(row)
        now = dict(step_titles(state["kind"]))["stop"]
        past = dict(step_titles(state["kind"], done=True))["stop"]
        state["steps"].insert(resume_index(state), {"id": "stop", "title": now, "done": past, "state": "pending"})
    resume_at = resume_index(state)
    for idx, row in enumerate(state.get("steps", [])):
        row.pop("started", None)
        row.pop("ended", None)
        if idx < resume_at:
            row["carried"] = True
        else:
            if row["state"] in ("active", "fail") and row["id"] in ("stage", "replace"):
                state["partial"] = row["id"]      # a step that was cut off half way: what it left is still on the Mac
            row["state"] = "pending"
            row.pop("carried", None)
    state["resume_at"] = resume_at


def add_disk(state, ref):
    """Remember a volume this run created (the recovery or payload disk), so a Mac that was rolled back by hand and
    kept it as an unused disk can have exactly that one removed."""
    disks = state.setdefault("disks", [])
    if ref and ref not in disks:
        disks.append(ref)


def forget_disk(state, ref):
    """The tool deleted this volume itself: it is no longer one of the run's disks. A volume id is reused by the next
    disk Proxmox makes, so an id that stays recorded after its volume is gone could one day name a disk of the user's."""
    state["disks"] = [r for r in state.get("disks", []) if r != ref]


def begin_step(state, sid):
    """Start a step. False, changing nothing, for a step that a resumed run finished before the point it goes on from."""
    row = _row(state, sid)
    if row is None:
        return False
    if row["state"] == "done" and state["steps"].index(row) < state.get("resume_at", 0):
        return False
    row.update(state="active", started=time.time())
    row.pop("ended", None)
    return True


def end_step(state, sid, st="done", title=""):
    row = _row(state, sid)
    if row is None:
        return
    row.update(state=st, ended=time.time())
    if title:
        row["done"] = title


def fail_update(state, error):
    """Every step that was running failed, and so did the run."""
    for row in state["steps"]:
        if row["state"] == "active":
            row.update(state="fail", ended=time.time())
    state.update(phase="failed", error=error)


def join_text(*parts):
    """Sentences put one after the other: each part that does not end in a full stop (or a question or exclamation mark)
    gets ". " before the next one, so a Proxmox error and our text never run together."""
    out = ""
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        if out and out[-1] == ":":
            out = out[:-1]
        if out and out[-1] not in ".!?":
            out += "."
        out += (" " if out else "") + part
    return out


def failure_text(state, running):
    """What a failed run left behind, from its steps: what changed on the Mac and whether it is running."""
    rows = {r["id"]: r["state"] for r in state.get("steps", [])}
    if state.get("partial") and rows.get(state["partial"]) != "done":
        rows[state["partial"]] = "fail"
    if state.get("kind") == "amd":
        what = {"done": "The kexts on the OpenCore disk were replaced.",
                "active": "The OpenCore disk may be half changed: Lilu and WhateverGreen may not match.",
                "fail": "The OpenCore disk may be half changed: Lilu and WhateverGreen may not match."}.get(rows.get("replace"))
    elif rows.get("stage") == "done" or state.get("staged"):
        what = "The driver is staged inside macOS and installs at the next start."
    else:
        what = "The driver was only partly staged." if rows.get("stage") in ("active", "fail") else None
    return (what or "Nothing on the Mac's disks was changed.") + (" The Mac is running." if running else " The Mac is stopped.")


def card_changed(saved, current, interrupted=False):
    """True when the card setup of the Mac is not the one a snapshot was taken with (putting the snapshot back would
    point the Mac at a card the host no longer prepared). A cut-off update took the card off itself."""
    if not saved:
        return False
    return not (interrupted and not current) and saved != current
