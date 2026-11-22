#!/usr/bin/env python3
"""Driver records, version rules, plan lines and snapshot rules for "update the driver". Standard library only.

The Python package imports this module and the bash installer embeds it verbatim (tests/test_driver_parity.py
diffs the two), so the record format, the plan lines and the refusal texts have one implementation.

One record per Mac: STATE_DIR/<vmid>.json with the kind (nvidia, intel or amd) and the installed components.
The lock and the state of a run in progress live in driver_state.py, which the bash installer embeds next to this file.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    from .driver_state import (  # noqa: F401 - re-exported: the package reaches these through this module
        STATE_DIR, STEPS, acquire_lock, begin_step, card_changed, clear_update, end_step, fail_update, failure_text,
        lock_holder, lock_path, new_update, read_json as _read_json, read_update, release_lock, resume_index, state_dir,
        step_titles, unfinished, update_path, write_json as _write_json, write_update,
    )
except ImportError:      # run as a script by the bash installer: driver_state.py sits next to this file
    from driver_state import (  # noqa: F401
        STATE_DIR, STEPS, acquire_lock, begin_step, card_changed, clear_update, end_step, fail_update, failure_text,
        lock_holder, lock_path, new_update, read_json as _read_json, read_update, release_lock, resume_index, state_dir,
        step_titles, unfinished, update_path, write_json as _write_json, write_update,
    )

IGPU_DIR = "/var/lib/osx-proxmox-next/igpu"
KINDS = ("nvidia", "intel", "amd")
SNAPSHOT_PREFIX = "osx-next-driver-"
SNAPSHOT_RE = re.compile(r"^osx-next-driver-\d{8}-\d{4}(?:\d{2})?$")
NO_SNAPSHOT_PHRASE = "no snapshot"
CHECK_MAX_AGE = 24 * 3600

# what each kind installs, in the order the plan lists it
COMPONENTS = {"nvidia": ("NullMoth",), "intel": ("WhateverXe", "WhateverXe Graphics"), "amd": ("Lilu", "WhateverGreen")}
CARD_NAME = {"nvidia": "NVIDIA", "intel": "Intel", "amd": "AMD"}
MINUTES = {"nvidia": "about 10 minutes", "intel": "about 10 minutes", "amd": "about 1 minute"}

REFUSE_INSTALLING = "This Mac is still being installed. Let that finish first."
REFUSE_CARD_SETUP = "A graphics card setup for this Mac is waiting to finish. Finish it first, or put everything back."
REFUSE_LOCKED = "Another driver update is running for this Mac."
REFUSE_NO_CARD = "This Mac has no graphics card attached, so there is no driver to update."
REFUSE_UNKNOWN_KIND = "This tool cannot tell which kind of card this Mac has, so it cannot pick a driver."
REFUSE_NOT_MAC = "VM %s is not a macOS VM made by this tool."
REFUSE_NOTHING_TO_UNDO = "There is no driver snapshot from this tool for this Mac, so there is nothing to undo."
REFUSE_KIND_CHANGED = "The card in this Mac is not the one its driver record was made for. Set the card up again first."
REFUSE_CARD_CHANGED = ("The card setup of this Mac changed after the snapshot was taken, so putting the snapshot back would "
                       "point the Mac at a card the host no longer prepares. Nothing was changed.")
REFUSE_NEWER_SNAPSHOTS = "Newer snapshots exist (%s). Proxmox cannot roll back past them. Nothing was changed."
REFUSE_NOT_STOPPED = "The Mac is not stopped, so its OpenCore disk is not touched."
UP_TO_DATE = "Up to date: nothing to do."
NO_SNAPSHOT_WARNING = ("The storage of this Mac cannot take snapshots, so nothing can undo the update if it goes wrong. "
                       "Type '%s' to go on without one." % NO_SNAPSHOT_PHRASE)
TEXTS = {"REFUSE_INSTALLING": REFUSE_INSTALLING, "REFUSE_CARD_SETUP": REFUSE_CARD_SETUP, "REFUSE_LOCKED": REFUSE_LOCKED,
         "REFUSE_NO_CARD": REFUSE_NO_CARD, "REFUSE_UNKNOWN_KIND": REFUSE_UNKNOWN_KIND, "REFUSE_NOT_MAC": REFUSE_NOT_MAC,
         "REFUSE_NOTHING_TO_UNDO": REFUSE_NOTHING_TO_UNDO, "UP_TO_DATE": UP_TO_DATE,
         "NO_SNAPSHOT_WARNING": NO_SNAPSHOT_WARNING, "REFUSE_KIND_CHANGED": REFUSE_KIND_CHANGED,
         "REFUSE_CARD_CHANGED": REFUSE_CARD_CHANGED, "REFUSE_NEWER_SNAPSHOTS": REFUSE_NEWER_SNAPSHOTS,
         "REFUSE_NOT_STOPPED": REFUSE_NOT_STOPPED}
VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
SNAPSHOT_TYPES = ("zfspool", "lvmthin", "rbd", "cephfs", "btrfs")
SNAPSHOT_FILE_TYPES = ("dir", "nfs", "cifs", "glusterfs")


# ── records ──────────────────────────────────────────────────────────

def record_path(vmid, base=None):
    return state_dir(base) / ("%s.json" % int(vmid))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def component(name, version, tag="", sha256="", source="", installed_at=None):
    """One installed component. *source* is latest, chosen or pinned."""
    return {"name": name, "version": str(version), "tag": tag or str(version), "sha256": sha256, "source": source,
            "installed_at": installed_at or now_iso()}


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


def write_record(vmid, kind, components, base=None, previous=None, snapshot="", card=""):
    """Write the record. *previous* (the components before an update), *snapshot* and *card* (the hostpci0 the snapshot
    was taken with) let "undo the driver update" restore the versions and refuse when the card setup changed."""
    if kind not in KINDS:
        raise ValueError("unknown kind %r" % kind)
    data = {"vmid": int(vmid), "kind": kind, "components": list(components), "updated": now_iso()}
    if previous is not None:
        data["previous"] = list(previous)
    if snapshot:
        data["snapshot"] = snapshot
        data["snapshot_card"] = card
    _write_json(record_path(vmid, base), data)
    return data


def revert_record(vmid, base=None):
    """"Put it back": the components before the last update become the installed ones again. False without a previous."""
    data = _read_json(record_path(vmid, base))
    if not isinstance(data.get("previous"), list):
        return False
    data["components"] = data.pop("previous")
    data.pop("snapshot", None)
    data.pop("snapshot_card", None)
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
        elif compare(have, want) > 0:
            lines.append("%s: %s is newer than %s, kept" % (name, have, want))
        else:
            lines.append("%s: %s to %s" % (name, have, want))
    return lines


def retention_line(older):
    """The sentence about the snapshot an update replaces: at most one snapshot of this tool is kept per Mac."""
    if not older:
        return ""
    return "The previous driver snapshot%s %s %s removed after a successful update." % (
        "s" if len(older) > 1 else "", ", ".join(older), "are" if len(older) > 1 else "is")


def replace_names(installed, target, reinstall=False):
    """The components an AMD update replaces: those that are behind or unknown. A component newer than the pin is never
    replaced (that would go back); an explicit reinstall of an up to date Mac replaces them all."""
    todo = [n for n, _h, _w, state in judge(installed, target)["rows"] if state != "current"]
    return todo or (list(target) if reinstall else [])


def before_facts(kind, snapshot, supported=True, older=()):
    """The facts "Before you continue" and the CLI plan state: how long, what comes off, the snapshot."""
    lines = ["The Mac stops for %s." % MINUTES[kind]]
    if kind != "amd":
        lines.append("The card comes off and goes back on.")
    lines.append("If macOS does not shut down within 2 minutes the Mac is stopped by force.")
    lines.append("A snapshot called %s is saved first." % snapshot if supported else NO_SNAPSHOT_WARNING)
    lines.append("Afterwards r undoes the driver update (osx-next-cli driver-undo).")
    if older:
        lines.append(retention_line(older))
    return lines


# ── snapshots ────────────────────────────────────────────────────────

def snapshot_name(when=None):
    return SNAPSHOT_PREFIX + time.strftime("%Y%m%d-%H%M%S", when or time.localtime())


def snapshot_date(name):
    """`2026-11-21 10:30` out of osx-next-driver-20261121-1030 (the name says when it was taken)."""
    m = re.match(r"^osx-next-driver-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", name or "")
    return "%s-%s-%s %s:%s" % m.groups() if m else ""


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
    return sorted(set(re.findall(r"(?<![\w-])(osx-next-driver-\d{8}-\d{4}(?:\d{2})?)(?![\w-])", listing or "")))


def all_snapshots(listing):
    """Every snapshot in `qm listsnapshot` output, oldest first (the line that says "current" is not one)."""
    return [m for m in re.findall(r"->\s+(\S+)", listing or "") if m != "current"]


def newer_than(listing, name):
    """The snapshots taken after *name*, which a rollback to it would have to get past."""
    names = all_snapshots(listing)
    return names[names.index(name) + 1:] if name in names else []


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


# ── which card the Mac has ───────────────────────────────────────────

def live_kind(cfg, sysfs_root="/sys"):
    """(kind, readable): the vendor of the passed-through device of PCI class 0x03 (a graphics card) on this host.
    readable is False when no passed-through device could be looked up here. A USB controller or a NIC is no card."""
    seen = False
    for key, value in sorted(cfg.items()):
        if not key.startswith("hostpci"):
            continue
        bdf = value.split(",", 1)[0].split(";", 1)[0].replace("host=", "")
        bdf = bdf if bdf.count(":") == 2 else "0000:" + bdf
        folder = Path(sysfs_root) / "bus/pci/devices" / (bdf if "." in bdf else bdf + ".0")
        try:
            pci_class = (folder / "class").read_text().strip().lower()
            vendor = (folder / "vendor").read_text().strip().lower()
        except OSError:
            continue
        seen = True
        if pci_class.startswith("0x03"):
            return VENDORS.get(vendor, ""), True
    return "", seen


def resolve_kind(recorded, live, readable):
    """(kind, conflict) from the kind the records say and the card found on the host. The live card wins; a record
    that names another kind is a conflict. Without a readable device the record decides."""
    if live:
        return live, bool(recorded) and recorded != live
    return ("" if readable else recorded), False


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
    elif cmd == "write" and len(argv) in (5, 6, 8):
        _emit(write_record(argv[2], argv[3], json.loads(argv[4]), previous=json.loads(argv[5]) if len(argv) > 5 else None,
                           snapshot=argv[6] if len(argv) == 8 else "", card=argv[7] if len(argv) == 8 else ""))
    elif cmd == "revert" and len(argv) == 3:
        return 0 if revert_record(argv[2]) else 1
    elif cmd == "component" and len(argv) in (4, 7):
        _emit(component(argv[2], argv[3], *(argv[4:7] if len(argv) == 7 else ())))
    elif cmd == "judge" and len(argv) == 4:
        _emit(judge(json.loads(argv[2]), json.loads(argv[3])))
    elif cmd == "changes" and len(argv) == 5:
        sys.stdout.write("\n".join(change_lines(argv[2], json.loads(argv[3]), json.loads(argv[4]))) + "\n")
    elif cmd == "facts" and len(argv) >= 4:
        flag = argv[4] if len(argv) > 4 and argv[4] in ("supported", "unsupported") else ""
        sys.stdout.write("\n".join(before_facts(argv[2], argv[3], flag != "unsupported", argv[5 if flag else 4:])) + "\n")
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
    elif cmd == "unfinished" and len(argv) == 3:
        _emit(unfinished(argv[2]))
    elif cmd in ("update-init", "update-resume", "update-begin", "update-end", "update-fail", "update-set", "update-clear",
                 "update-get", "pending-kind", "failure-text", "card-changed", "kind", "replace-names", "snapshot-date",
                 "newer-than", "all-snapshots"):
        return run_update_command(cmd, argv[2:])
    else:
        sys.stderr.write("usage: driver_engine.py record|write|revert|component|judge|changes|facts|steps|snapshot-name|"
                         "snapshot-argv|tool-snapshots|snapshot-support|text|from-igpu|compare|unfinished|update-*|failure-text|kind ...\n")
        return 64
    return 0


def run_update_command(cmd, args):
    """The commands that keep the state of a run for the bash installer; each reads, changes and writes one file."""
    if cmd == "snapshot-date":
        sys.stdout.write(snapshot_date(args[0]) + "\n")
    elif cmd == "replace-names":
        sys.stdout.write("\n".join(replace_names(json.loads(args[0]), json.loads(args[1]), args[2] == "yes")) + "\n")
    elif cmd == "newer-than":
        sys.stdout.write("\n".join(newer_than(sys.stdin.read(), args[0])) + "\n")
    elif cmd == "all-snapshots":
        sys.stdout.write("\n".join(all_snapshots(sys.stdin.read())) + "\n")
    elif cmd == "kind":
        cfg = dict(line.split(": ", 1) for line in sys.stdin.read().splitlines() if ": " in line)
        live, readable = live_kind(cfg, os.environ.get("SYSFS_ROOT") or "/sys")
        kind, conflict = resolve_kind(args[0], live, readable)
        _emit({"kind": kind, "conflict": conflict, "readable": readable})
    elif cmd == "card-changed":
        return 0 if card_changed(args[0], args[1], args[2] == "yes") else 1
    elif cmd == "update-clear":
        clear_update(args[0])
    else:
        return update_state_command(cmd, args)
    return 0


def update_state_command(cmd, args):
    vmid, state = args[0], read_update(args[0])
    if cmd == "update-get":                       # the value of one key as JSON (null when it is not there)
        sys.stdout.write(json.dumps(state.get(args[1])) + "\n")
        return 0
    if cmd == "pending-kind":                     # the kind of the run that is going or was cut off, else nothing
        sys.stdout.write((state.get("kind", "") if state.get("phase") != "done" else "") + "\n")
        return 0
    if cmd == "update-init":     # kind name label step-name choice-json previous-json card original-json snapshot
        state = new_update(args[1], args[2], args[3], args[4], json.loads(args[5]), json.loads(args[6]), args[7])
        state.update(original=json.loads(args[8]) if len(args) > 8 and args[8] else None)
    elif cmd == "update-resume":
        state.update(resume_at=resume_index(state), phase="running", error="")
    elif cmd == "update-begin":                   # exit 3: a resumed run finished this step before the point it goes on from
        if not begin_step(state, args[1]):
            return 3
    elif cmd == "update-end":
        end_step(state, args[1], args[2] if len(args) > 2 else "done")
    elif cmd == "update-fail":
        fail_update(state, args[1])
    elif cmd == "update-set":                     # key json-value
        state[args[1]] = json.loads(args[2])
    elif cmd == "failure-text":
        sys.stdout.write(failure_text(state, args[1] == "yes") + "\n")
        return 0
    write_update(vmid, state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
