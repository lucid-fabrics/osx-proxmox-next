"""Which argument counts every command of the four embedded engines accepts. The table is written out here on purpose: it is the
contract the bash installer calls (`python3 - <command> ...`), so it must not be read back from the dispatch tables under test.
A count is accepted when the command runs (even if it then fails on the dummy arguments) and rejected when the engine prints its
usage line and exits 64. The golden test in test_engine_golden.py pins what each command prints."""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from osx_proxmox_next import driver_engine, host_setup, igpu_engine, nullmoth_engine

ENGINES = {"host_setup": host_setup, "driver_engine": driver_engine, "igpu_engine": igpu_engine, "nullmoth_engine": nullmoth_engine}
USAGE = {"host_setup": "usage: host_setup.py ", "driver_engine": "usage: driver_engine.py ", "igpu_engine": "usage: igpu_engine.py ",
         "nullmoth_engine": "usage: nullmoth_engine.py "}
ANY = "any"


def at_least(n):
    return ("at least", n)


# command -> the values of len(argv) that run it (argv is the program name, the command, then its arguments)
ACCEPTS = {
    "host_setup": {cmd: ANY for cmd in (
        "facts", "target", "identity", "mappings", "holds", "lxc", "record-write", "vm-restore", "vm-stale", "step", "plan", "preview",
        "apply", "undo", "verify", "state", "state-save", "reboot-pending", "rebooted", "mark-reboot", "hint-set", "hint-clear")},
    "driver_engine": {
        "record": (3,), "write": (5, 6, 8, 9, 10), "revert": (3,), "disk-read": (4,), "component": (4, 7), "judge": (4,),
        "changes": (5, 6), "facts": at_least(4), "todo-steps": (5, 6), "finish-facts": at_least(3), "steps": (4, 5), "fingerprint": ANY,
        "trusted": (4,), "stamp": (4,), "stale-maps": ANY, "maps-held": at_least(4), "first-boot-text": (5,), "snapshot-name": ANY,
        "snapshot-argv": (5,), "tool-snapshots": ANY, "snapshot-support": (3,), "text": (3,), "from-igpu": (3,), "compare": (4,),
        "unfinished": (3,),
        **{cmd: ANY for cmd in (
            "update-init", "update-resume", "update-begin", "update-end", "update-fail", "update-set", "update-clear", "update-get",
            "update-add-disk", "update-forget-disk", "pending-kind", "failure-text", "card-changed", "kind", "replace-names",
            "snapshot-date", "newer-than", "all-snapshots")}},
    "igpu_engine": {
        "check": (2, 3), "verdict": (3, 4), "lookup": (3, 4), "devices": (2, 3), "settings": (4,), "merge-args": ANY, "strip-args": ANY,
        "fetch": (4,), "assets": (3, 4, 5), "releases": (3,), "state-write": (3,), "state-record": (4,), "state-show": (3,),
        "payload": (4,), "result": (3,), "accel-check": (2, 3), "stage-command": ANY, "pins": ANY},
    "nullmoth_engine": {
        "releases": ANY, "resolve": (3,), "fetch": (4,), "fw-pins": (3,), "fw-fetch": (4,), "payload": (5, 7), "result": (3,),
        "parse-result": (3,), "vm-mac": ANY, "find-ip": (3,), "port": (4,)},
}
# the first argument of these two commands must be one the engine knows, or the command is a usage error whatever the count
FIRST_ARGUMENT = {("driver_engine", "text"): "UP_TO_DATE", ("igpu_engine", "releases"): "graphics"}
LENGTHS = range(2, 13)


def accepted(spec, length):
    if spec == ANY:
        return True
    if isinstance(spec, tuple) and spec and spec[0] == "at least":
        return length >= spec[1]
    return length in spec


@pytest.fixture(autouse=True)
def _sealed(monkeypatch, tmp_path):
    for name in ("GPU_ROOT", "SYSFS_ROOT", "GPU_SYSFS"):
        monkeypatch.setenv(name, str(tmp_path / "root"))
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path / "driver"))
    monkeypatch.setenv("IGPU_STATE_DIR", str(tmp_path / "igpu"))
    monkeypatch.setenv("IGPU_DEVICES_OFFLINE", "1")
    monkeypatch.setenv("IGPU_DEVICES_CACHE", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)

    def refuse(*_a, **_k):
        raise OSError("sealed")
    for target, name in ((subprocess, "run"), (subprocess, "Popen"), (subprocess, "check_output"), (subprocess, "call"),
                         (urllib.request, "urlopen"), (socket, "create_connection"), (time, "sleep")):
        monkeypatch.setattr(target, name, refuse if name != "sleep" else (lambda *_a, **_k: None))


def invoke(monkeypatch, capsys, engine, argv, stdin="{}"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    ticks = iter(range(0, 10**9, 1000))      # a clock that jumps, so a command that waits for something gives up at once
    with monkeypatch.context() as patch:
        patch.setattr(time, "monotonic", lambda: next(ticks))
        try:
            code = ENGINES[engine].main(["prog.py", *argv])
        except Exception:    # noqa: BLE001  the command ran and failed on the dummy arguments
            code = "raised"
    out = capsys.readouterr()
    return code, out.out, out.err


CASES = [(engine, cmd, length) for engine, table in ACCEPTS.items() for cmd in table for length in LENGTHS]


@pytest.mark.parametrize("engine", sorted(ACCEPTS))
def test_every_command_accepts_exactly_the_argument_counts_the_installer_passes(monkeypatch, capsys, engine):
    wrong = []
    for cmd, spec in ACCEPTS[engine].items():
        for length in LENGTHS:
            args = [FIRST_ARGUMENT.get((engine, cmd), "x")] + ["x"] * (length - 3) if length > 2 else []
            code, out, err = invoke(monkeypatch, capsys, engine, [cmd, *args])
            usage = code == 64 and err.startswith(USAGE[engine])
            if usage:
                assert out == "" and err.endswith("\n") and err.count("\n") >= 1
            if usage == accepted(spec, length):
                wrong.append((cmd, length, "usage line" if usage else "ran"))
    assert wrong == []


@pytest.mark.parametrize("engine,argv", [
    ("driver_engine", ["text", "NO_SUCH_TEXT"]), ("igpu_engine", ["releases", "no-such-kind"]), ("igpu_engine", ["releases", "graphics", "x"]),
    ("nullmoth_engine", ["Releases"]), ("host_setup", ["Facts"]), ("host_setup", ["no-such-command"]),
])
def test_a_command_the_engine_does_not_know_is_a_usage_error(monkeypatch, capsys, engine, argv):
    code, out, err = invoke(monkeypatch, capsys, engine, argv)
    assert code == 64 and out == "" and err.startswith(USAGE[engine])


def test_the_table_names_every_command_of_every_engine():
    from osx_proxmox_next.driver_engine import COMMANDS as driver
    from osx_proxmox_next.host_setup import COMMANDS as host
    from osx_proxmox_next.igpu_engine import COMMANDS as igpu
    from osx_proxmox_next.nullmoth_engine import COMMANDS as nullmoth
    assert {"host_setup": set(host), "driver_engine": set(driver), "igpu_engine": set(igpu), "nullmoth_engine": set(nullmoth)} == \
        {engine: set(table) for engine, table in ACCEPTS.items()}


# ── host_setup reads its payload before it looks the command up ──────

@pytest.mark.parametrize("cmd", ["facts", "state", "hint-clear", "undo", "reboot-pending", "mark-reboot", "rebooted"])
def test_host_setup_commands_that_take_no_payload_never_read_stdin(monkeypatch, capsys, cmd):
    code, _out, _err = invoke(monkeypatch, capsys, "host_setup", [cmd], stdin="this is not json")
    assert code in (0, 1)


@pytest.mark.parametrize("cmd", ["plan", "step", "target", "no-such-command", "preview"])
def test_host_setup_reads_and_parses_the_payload_of_every_other_command_first(monkeypatch, capsys, cmd):
    monkeypatch.setattr(sys, "stdin", io.StringIO("[1"))
    with pytest.raises(ValueError):
        host_setup.main(["prog.py", cmd])


def test_host_setup_treats_an_empty_payload_as_an_empty_object(monkeypatch, capsys):
    code, out, _err = invoke(monkeypatch, capsys, "host_setup", ["mappings"], stdin="")
    assert code == 0 and json.loads(out) == {}


# ── the exit codes the bash installer branches on ────────────────────

def test_undo_and_apply_exit_one_when_they_report_not_ok(monkeypatch, capsys):
    host_setup.save_state(host_setup.new_state(), os.environ["GPU_ROOT"])
    monkeypatch.setattr(host_setup, "undo_host", lambda state, root: {"ok": False, "errors": ["x"]})
    code, out, _err = invoke(monkeypatch, capsys, "host_setup", ["undo"])
    assert code == 1 and json.loads(out) == {"errors": ["x"], "ok": False}
    monkeypatch.setattr(host_setup, "undo_host", lambda state, root: {"ok": True})
    assert invoke(monkeypatch, capsys, "host_setup", ["undo"])[0] == 0
    monkeypatch.setattr(host_setup, "apply_plan", lambda plan, state, root, confirmed=False: {"ok": False})
    assert invoke(monkeypatch, capsys, "host_setup", ["apply"], stdin='{"plan": {}}')[0] == 1
    monkeypatch.setattr(host_setup, "apply_plan", lambda plan, state, root, confirmed=False: {"ok": True})
    assert invoke(monkeypatch, capsys, "host_setup", ["apply"], stdin='{"plan": {}}')[0] == 0


def test_undo_without_a_saved_state_succeeds_and_says_nothing_was_restored(monkeypatch, capsys):
    code, out, _err = invoke(monkeypatch, capsys, "host_setup", ["undo"])
    assert code == 0 and json.loads(out) == {"ok": True, "restored": [], "errors": [], "kept": [], "reboot_required": False}
