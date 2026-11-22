#!/usr/bin/env python3
"""Driver records, version rules, plan lines and snapshot rules for "update the driver". Standard library only.

The Python package imports this module and the bash installer embeds it verbatim (tests/test_driver_parity.py
diffs the two), so the record format, the plan lines and the refusal texts have one implementation.

One record per Mac: STATE_DIR/<vmid>.json with the kind (nvidia, intel or amd) and the installed components.
Next to it, while an update runs: <vmid>.lock (the process that runs it) and <vmid>.update.json (what an
interrupted run needs to finish or to go back).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

STATE_DIR = "/var/lib/osx-proxmox-next/drivers"
IGPU_DIR = "/var/lib/osx-proxmox-next/igpu"
KINDS = ("nvidia", "intel", "amd")
SNAPSHOT_PREFIX = "osx-next-driver-"
SNAPSHOT_RE = re.compile(r"^osx-next-driver-\d{8}-\d{4}$")
NO_SNAPSHOT_PHRASE = "no snapshot"
CHECK_MAX_AGE = 24 * 3600
KEXT_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")

# what each kind installs, in the order the plan lists it
COMPONENTS = {"nvidia": ("NullMoth",), "intel": ("WhateverXe", "WhateverXe Graphics"), "amd": ("Lilu", "WhateverGreen")}
CARD_NAME = {"nvidia": "NVIDIA", "intel": "Intel", "amd": "AMD"}
MINUTES = {"nvidia": "about 10 minutes", "intel": "about 10 minutes", "amd": "about 1 minute"}
STEPS = {
    "nvidia": (("stop", "Stop the Mac", "Stopped the Mac"), ("snapshot", "Save a snapshot", "Saved a snapshot"),
               ("takeoff", "Take the card off", "Took the card off"), ("stage", "Stage {name}", "Staged {name}"),
               ("putback", "Put the card back", "Put the card back"), ("start", "Start the Mac", "Started the Mac"),
               ("verify", "Install the driver", "Driver installed")),
    "amd": (("stop", "Stop the Mac", "Stopped the Mac"), ("snapshot", "Save a snapshot", "Saved a snapshot"),
            ("replace", "Replace the kexts", "Replaced the kexts"), ("start", "Start the Mac", "Started the Mac")),
}
STEPS["intel"] = STEPS["nvidia"]

REFUSE_INSTALLING = "This Mac is still being installed. Let that finish first."
REFUSE_CARD_SETUP = "A graphics card setup for this Mac is waiting to finish. Finish it first, or put everything back."
REFUSE_LOCKED = "Another driver update is running for this Mac."
REFUSE_NO_CARD = "This Mac has no graphics card attached, so there is no driver to update."
REFUSE_UNKNOWN_KIND = "This tool cannot tell which kind of card this Mac has, so it cannot pick a driver."
REFUSE_NOT_MAC = "VM %s is not a macOS VM made by this tool."
REFUSE_NOTHING_TO_UNDO = "There is no safety snapshot from this tool for this Mac, so there is nothing to put back."
UP_TO_DATE = "Up to date: nothing to do."
NO_SNAPSHOT_WARNING = ("The storage of this Mac cannot take snapshots, so nothing can put it back if the update goes wrong. "
                       "Type '%s' to go on without one." % NO_SNAPSHOT_PHRASE)
TEXTS = {"REFUSE_INSTALLING": REFUSE_INSTALLING, "REFUSE_CARD_SETUP": REFUSE_CARD_SETUP, "REFUSE_LOCKED": REFUSE_LOCKED,
         "REFUSE_NO_CARD": REFUSE_NO_CARD, "REFUSE_UNKNOWN_KIND": REFUSE_UNKNOWN_KIND, "REFUSE_NOT_MAC": REFUSE_NOT_MAC,
         "REFUSE_NOTHING_TO_UNDO": REFUSE_NOTHING_TO_UNDO, "UP_TO_DATE": UP_TO_DATE,
         "NO_SNAPSHOT_WARNING": NO_SNAPSHOT_WARNING}
SNAPSHOT_TYPES = ("zfspool", "lvmthin", "rbd", "cephfs", "btrfs")
SNAPSHOT_FILE_TYPES = ("dir", "nfs", "cifs", "glusterfs")


# ── records ──────────────────────────────────────────────────────────

def state_dir(base=None):
    return Path(base or os.environ.get("DRIVER_STATE_DIR") or STATE_DIR)


def record_path(vmid, base=None):
    return state_dir(base) / ("%s.json" % int(vmid))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def component(name, version, tag="", sha256="", source="", installed_at=None):
    """One installed component. *source* is latest, chosen or pinned."""
    return {"name": name, "version": str(version), "tag": tag or str(version), "sha256": sha256, "source": source,
            "installed_at": installed_at or now_iso()}


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def from_igpu_state(vmid, state):
    """The driver record an older iGPU record (igpu_engine.write_state) stands for, or {}."""
    parts = []
    for key, name in (("whateverxe", "WhateverXe"), ("graphics", "WhateverXe Graphics"), ("amfipass", "AMFIPass")):
        version = state.get(key + "_version")
        if version:
            parts.append(component(name, version, state.get(key + "_tag") or version, state.get(key + "_sha256", ""),
                                   state.get(key + "_source") or "pinned", state.get("updated")))
    if not parts:
        return {}
    return {"vmid": int(vmid), "kind": "intel", "components": parts, "migrated": True, "updated": state.get("updated", "")}


def read_record(vmid, base=None, igpu_base=None):
    """The Mac's driver record; for a Mac that only has an iGPU record, that record read as a driver record."""
    data = _read_json(record_path(vmid, base))
    if data.get("kind") in KINDS and isinstance(data.get("components"), list):
        return data
    old = _read_json(Path(igpu_base or os.environ.get("IGPU_STATE_DIR") or IGPU_DIR) / ("%s.json" % int(vmid)))
    return from_igpu_state(vmid, old) if old else {}


def write_record(vmid, kind, components, base=None, previous=None, snapshot=""):
    """Write the record. *previous* (the components before an update) and *snapshot* let "put it back" restore them."""
    if kind not in KINDS:
        raise ValueError("unknown kind %r" % kind)
    data = {"vmid": int(vmid), "kind": kind, "components": list(components), "updated": now_iso()}
    if previous is not None:
        data["previous"] = list(previous)
    if snapshot:
        data["snapshot"] = snapshot
    _write_json(record_path(vmid, base), data)
    return data


def revert_record(vmid, base=None):
    """"Put it back": the components before the last update become the installed ones again. False without a previous."""
    data = _read_json(record_path(vmid, base))
    if not isinstance(data.get("previous"), list):
        return False
    data["components"] = data.pop("previous")
    data.pop("snapshot", None)
    data["updated"] = now_iso()
    _write_json(record_path(vmid, base), data)
    return True


def installed_versions(record):
    return {c["name"]: c.get("version", "") for c in record.get("components", []) if "name" in c}


# ── versions ─────────────────────────────────────────────────────────

def version_key(text):
    """Numbers of a version, so v2.10 is newer than v2.9. Text without numbers sorts first."""
    return tuple(int(n) for n in re.findall(r"\d+", str(text or "")))


def compare(a, b):
    ka, kb = version_key(a), version_key(b)
    return (ka > kb) - (ka < kb)


def is_newer(target, installed):
    return bool(target) and bool(installed) and compare(target, installed) > 0


def judge(installed, target):
    """{"state": unknown|update|current, "rows": [(name, installed, target, state)]} for the components of *target*
    (a {name: version} mapping) against *installed* (the same shape). unknown: nothing is known about the Mac;
    update: a target differs; current: every target is what the Mac has. An installed version that is newer than the
    pinned one is current: the tool never goes back on its own."""
    rows, states = [], []
    for name, want in target.items():
        have = installed.get(name, "")
        if not have:
            state = "unknown"
        elif compare(have, want) == 0 or compare(have, want) > 0:
            state = "current"
        else:
            state = "update"
        rows.append((name, have, want, state))
        states.append(state)
    if states and all(s == "unknown" for s in states):
        overall = "unknown"
    elif any(s in ("update", "unknown") for s in states):
        overall = "update"
    else:
        overall = "current"
    return {"state": overall, "rows": rows}


def differs(installed, target):
    """True when a plain update has something to do: some target is not what the Mac has (an unknown install counts)."""
    return any(installed.get(n, "") != v for n, v in target.items())


def driver_line(kind, installed):
    """`NullMoth v2.8.1`, `WhateverXe v1.2 and WhateverXe Graphics v0.9`, `Lilu 1.7.2 and WhateverGreen 1.7.0`."""
    names = COMPONENTS[kind]
    parts = ["%s %s" % (n, installed[n]) for n in names if installed.get(n)]
    return " and ".join(parts)


# ── plan lines ───────────────────────────────────────────────────────

def change_lines(kind, installed, target):
    """One line per component: what changes, in plain words."""
    lines = []
    for name, want in target.items():
        have = installed.get(name, "")
        if not have:
            lines.append("%s: installed version unknown, to %s" % (name, want))
        elif have == want:
            lines.append("%s: %s, reinstalled" % (name, want))
        else:
            lines.append("%s: %s to %s" % (name, have, want))
    return lines


def step_titles(kind, name="the driver", done=False):
    """(id, title) for each step of an update, in order. *done* gives the past-tense line the run list shows."""
    return [(sid, (past if done else now).format(name=name)) for sid, now, past in STEPS[kind]]


def before_facts(kind, snapshot, supported=True):
    """The facts "Before you continue" and the CLI plan state: how long, what comes off, the snapshot."""
    lines = ["The Mac stops for %s." % MINUTES[kind]]
    if kind != "amd":
        lines.append("The card comes off and goes back on.")
    lines.append("If macOS does not shut down within 2 minutes the Mac is stopped by force.")
    lines.append("A snapshot called %s is saved first." % snapshot if supported else NO_SNAPSHOT_WARNING)
    lines.append("Put it back any time with u (osx-next-cli driver-undo).")
    return lines


# ── snapshots ────────────────────────────────────────────────────────

def snapshot_name(when=None):
    return SNAPSHOT_PREFIX + time.strftime("%Y%m%d-%H%M", when or time.localtime())


def snapshot_description(version):
    clean = re.sub(r"[^A-Za-z0-9._+ -]", "", str(version))[:60]
    return "before driver update to %s" % clean


def snapshot_argv(vmid, name, version):
    return ["qm", "snapshot", str(int(vmid)), _checked(name), "--description", snapshot_description(version)]


def rollback_argv(vmid, name):
    return ["qm", "rollback", str(int(vmid)), _checked(name)]


def delete_snapshot_argv(vmid, name):
    return ["qm", "delsnapshot", str(int(vmid)), _checked(name)]


def _checked(name):
    if not SNAPSHOT_RE.match(name or ""):
        raise ValueError("%r is not a snapshot this tool made" % name)
    return name


def tool_snapshots(listing):
    """The snapshots of this tool in `qm listsnapshot` output, oldest first. Snapshots of anyone else are ignored."""
    return sorted(set(re.findall(r"(?<![\w-])(osx-next-driver-\d{8}-\d{4})(?![\w-])", listing or "")))


def _disk_slots(cfg):
    """(slot, storage, volume) of every disk that a snapshot would have to take: not CD images, not the swap-in disks."""
    out = []
    for key, value in cfg.items():
        if not re.match(r"^(virtio|sata|scsi|ide|efidisk|tpmstate)\d*$", key) or "media=cdrom" in value:
            continue
        m = re.match(r"^([^:,]+):([^,]*)", value)
        if m and not value.startswith("none"):
            out.append((key, m.group(1), m.group(2)))
    return out


def snapshot_support(cfg, storage_types):
    """(ok, reason). *storage_types* maps a storage id to its type (pvesm status). A disk on a storage whose type is
    unknown, or a plain file that is not qcow2, cannot be snapshotted."""
    for slot, storage, volume in _disk_slots(cfg):
        kind = storage_types.get(storage, "")
        if kind in SNAPSHOT_TYPES:
            continue
        if kind in SNAPSHOT_FILE_TYPES and volume.endswith(".qcow2"):
            continue
        why = "its type is unknown" if not kind else "type %s%s" % (kind, "" if kind not in SNAPSHOT_FILE_TYPES else " without qcow2")
        return False, "%s is on storage %s (%s)" % (slot, storage, why)
    return True, ""


def parse_storage_types(status_text):
    """{storage: type} from `pvesm status` output (Name Type Status ...)."""
    out = {}
    for line in (status_text or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


# ── lock and unfinished run ──────────────────────────────────────────

def lock_path(vmid, base=None):
    return state_dir(base) / ("%s.lock" % int(vmid))


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True            # the process exists and belongs to somebody else
    except (OSError, ValueError):
        return False
    return True


def lock_holder(vmid, base=None):
    """The pid that holds the update lock of this Mac, or 0 (none, or a process that is gone)."""
    try:
        pid = int(lock_path(vmid, base).read_text().strip())
    except (OSError, ValueError):
        return 0
    return pid if _alive(pid) else 0


def acquire_lock(vmid, base=None, pid=None):
    """True when this process now holds the lock. False while another live process holds it."""
    path = lock_path(vmid, base)
    holder = lock_holder(vmid, base)
    mine = int(pid or os.getpid())
    if holder and holder != mine:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("%d\n" % mine)
    return True


def release_lock(vmid, base=None):
    try:
        lock_path(vmid, base).unlink()
    except OSError:
        pass


def update_path(vmid, base=None):
    return state_dir(base) / ("%s.update.json" % int(vmid))


def read_update(vmid, base=None):
    """What an unfinished update left: kind, target, snapshot, the settings it changed. {} when none."""
    return _read_json(update_path(vmid, base))


def write_update(vmid, data, base=None):
    _write_json(update_path(vmid, base), dict(data, vmid=int(vmid), updated=now_iso()))


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


# ── the daily check for newer releases (Home marker) ─────────────────

def check_path(base=None):
    return state_dir(base) / "latest.json"


def read_check(base=None):
    """{"checked": epoch, "latest": {component: version}} from the last check, or {}."""
    data = _read_json(check_path(base))
    return data if isinstance(data.get("latest"), dict) else {}


def check_due(now=None, base=None):
    data = read_check(base)
    return not data or (now if now is not None else time.time()) - float(data.get("checked", 0)) >= CHECK_MAX_AGE


def write_check(latest, now=None, base=None):
    _write_json(check_path(base), {"checked": now if now is not None else time.time(), "latest": dict(latest)})


# ── command line (used by the bash installer) ────────────────────────

def _emit(obj):
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "record" and len(argv) == 3:
        _emit(read_record(argv[2]))
    elif cmd == "write" and len(argv) in (5, 6):
        _emit(write_record(argv[2], argv[3], json.loads(argv[4]), previous=json.loads(argv[5]) if len(argv) == 6 else None,
                           snapshot=""))
    elif cmd == "revert" and len(argv) == 3:
        return 0 if revert_record(argv[2]) else 1
    elif cmd == "component" and len(argv) in (4, 7):
        _emit(component(argv[2], argv[3], *(argv[4:7] if len(argv) == 7 else ())))
    elif cmd == "judge" and len(argv) == 4:
        _emit(judge(json.loads(argv[2]), json.loads(argv[3])))
    elif cmd == "changes" and len(argv) == 5:
        sys.stdout.write("\n".join(change_lines(argv[2], json.loads(argv[3]), json.loads(argv[4]))) + "\n")
    elif cmd == "facts" and len(argv) in (4, 5):
        sys.stdout.write("\n".join(before_facts(argv[2], argv[3], len(argv) == 4)) + "\n")
    elif cmd == "steps" and len(argv) == 4:
        sys.stdout.write("\n".join("%s %s" % p for p in step_titles(argv[2], argv[3])) + "\n")
    elif cmd == "snapshot-name":
        sys.stdout.write(snapshot_name() + "\n")
    elif cmd == "snapshot-argv" and len(argv) == 5:
        sys.stdout.write("\n".join(snapshot_argv(argv[2], argv[3], argv[4])) + "\n")
    elif cmd == "tool-snapshots":
        sys.stdout.write("\n".join(tool_snapshots(sys.stdin.read())) + "\n")
    elif cmd == "snapshot-support" and len(argv) == 3:
        cfg = dict(line.split(": ", 1) for line in sys.stdin.read().splitlines() if ": " in line)
        ok, why = snapshot_support(cfg, parse_storage_types(Path(argv[2]).read_text()))
        sys.stdout.write(why + "\n")
        return 0 if ok else 1
    elif cmd == "text" and len(argv) == 3 and argv[2] in TEXTS:
        sys.stdout.write(TEXTS[argv[2]] + "\n")
    elif cmd == "from-igpu" and len(argv) == 3:
        record = from_igpu_state(argv[2], _read_json(Path(os.environ.get("IGPU_STATE_DIR") or IGPU_DIR) / ("%s.json" % int(argv[2]))))
        if record:
            write_record(argv[2], "intel", record["components"])
        return 0 if record else 1
    elif cmd == "compare" and len(argv) == 4:
        sys.stdout.write("%d\n" % compare(argv[2], argv[3]))
    elif cmd == "lock" and len(argv) == 4:
        return 0 if (acquire_lock(argv[2], pid=argv[3]) if argv[3].isdigit() else False) else 1
    elif cmd == "unlock" and len(argv) == 3:
        release_lock(argv[2])
    elif cmd == "unfinished" and len(argv) == 3:
        _emit(unfinished(argv[2]))
    elif cmd == "update-write" and len(argv) == 4:
        write_update(argv[2], json.loads(argv[3]))
    elif cmd == "update-clear" and len(argv) == 3:
        clear_update(argv[2])
    else:
        sys.stderr.write("usage: driver_engine.py record|write|revert|component|judge|changes|facts|steps|snapshot-name|"
                         "snapshot-argv|tool-snapshots|snapshot-support|text|from-igpu|compare|lock|unlock|unfinished|update-write|update-clear ...\n")
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
