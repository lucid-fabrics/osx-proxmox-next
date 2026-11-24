"""The command line of the four embedded engines: which command names and argument counts each one accepts, and what an unknown
command, a wrong count and no command do. The bash installer runs these as `python3 - <command> ...`, so the exit codes and the
usage line are a contract."""
from __future__ import annotations

import io
import sys

import pytest

from osx_proxmox_next import driver_engine, host_setup, igpu_engine, nullmoth_engine

ENGINES = {"host_setup": host_setup, "driver_engine": driver_engine, "igpu_engine": igpu_engine, "nullmoth_engine": nullmoth_engine}
USAGE_START = {"host_setup": "usage: host_setup.py facts|", "driver_engine": "usage: driver_engine.py record|",
               "igpu_engine": "usage: igpu_engine.py check", "nullmoth_engine": "usage: nullmoth_engine.py releases|"}


@pytest.fixture(autouse=True)
def _private_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("GPU_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path / "driver"))


def call(monkeypatch, capsys, engine, *argv, stdin="{}"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = ENGINES[engine].main([engine + ".py", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize("engine", sorted(ENGINES))
def test_no_command_and_an_unknown_command_print_the_usage_and_exit_64(monkeypatch, capsys, engine):
    for argv in ((), ("no-such-command",), ("no-such-command", "x", "y")):
        code, out, err = call(monkeypatch, capsys, engine, *argv)
        assert code == 64 and out == "" and err.startswith(USAGE_START[engine]) and err.endswith("\n")


@pytest.mark.parametrize("engine,argv", [
    ("driver_engine", ("record",)), ("driver_engine", ("record", "1", "2")), ("driver_engine", ("write", "1", "nvidia")),
    ("driver_engine", ("write", "1", "n", "[]", "[]", "s", "c", "{}", "f", "extra")), ("driver_engine", ("revert",)),
    ("driver_engine", ("judge", "{}")), ("driver_engine", ("changes", "nvidia", "{}")), ("driver_engine", ("facts", "nvidia")),
    ("driver_engine", ("steps", "nvidia")), ("driver_engine", ("steps", "a", "b", "c", "d")), ("driver_engine", ("text",)),
    ("driver_engine", ("text", "NO_SUCH_TEXT")), ("driver_engine", ("snapshot-argv", "1", "n")), ("driver_engine", ("from-igpu",)),
    ("driver_engine", ("compare", "1")), ("driver_engine", ("unfinished",)), ("driver_engine", ("Record", "1")),
    ("igpu_engine", ("check", "a", "b")), ("igpu_engine", ("verdict",)), ("igpu_engine", ("verdict", "1", "2", "3")),
    ("igpu_engine", ("settings", "1")), ("igpu_engine", ("fetch", "a")), ("igpu_engine", ("assets",)), ("igpu_engine", ("releases",)),
    ("igpu_engine", ("releases", "nope")), ("igpu_engine", ("state-write",)), ("igpu_engine", ("state-record", "1")),
    ("igpu_engine", ("state-show",)), ("igpu_engine", ("payload", "a")), ("igpu_engine", ("result",)), ("igpu_engine", ("accel-check", "1", "2")),
    ("nullmoth_engine", ("resolve",)), ("nullmoth_engine", ("resolve", "a", "b")), ("nullmoth_engine", ("fetch", "a")),
    ("nullmoth_engine", ("fw-pins",)), ("nullmoth_engine", ("fw-fetch", "a")), ("nullmoth_engine", ("payload", "a", "b")),
    ("nullmoth_engine", ("payload", "a", "b", "c", "d")), ("nullmoth_engine", ("result",)), ("nullmoth_engine", ("parse-result",)),
    ("nullmoth_engine", ("find-ip",)), ("nullmoth_engine", ("port", "1.2.3.4")), ("nullmoth_engine", ("Releases",)),
])
def test_a_known_command_with_an_argument_count_it_does_not_take_is_a_usage_error(monkeypatch, capsys, engine, argv):
    code, out, err = call(monkeypatch, capsys, engine, *argv)
    assert code == 64 and out == "" and err.startswith(USAGE_START[engine])


@pytest.mark.parametrize("engine,argv,out", [
    ("igpu_engine", ("stage-command",), igpu_engine.STAGE_COMMAND + "\n"),
    ("igpu_engine", ("stage-command", "any", "extra", "arguments"), igpu_engine.STAGE_COMMAND + "\n"),
    ("driver_engine", ("text", "UP_TO_DATE"), driver_engine.UP_TO_DATE + "\n"),
    ("driver_engine", ("fingerprint", "ignored", "extra"), "\n"),
    ("host_setup", ("state", "extra", "words"), None),
])
def test_commands_that_take_any_count_or_a_valid_key_return_zero_not_none(monkeypatch, capsys, engine, argv, out):
    code, text, err = call(monkeypatch, capsys, engine, *argv, stdin="")
    assert code == 0 and type(code) is int and err == ""
    if out is not None:
        assert text == out


def test_the_exit_codes_of_the_commands_that_report_a_state_by_it(monkeypatch, capsys, tmp_path):
    assert call(monkeypatch, capsys, "driver_engine", "revert", "9")[0] == 1
    assert call(monkeypatch, capsys, "driver_engine", "trusted", "9", "fp")[0] == 0
    assert call(monkeypatch, capsys, "driver_engine", "card-changed", "a", "a", "no")[0] == 1
    assert call(monkeypatch, capsys, "driver_engine", "update-add-disk", "9", "x")[0] == 0
    with pytest.raises(IndexError):      # the update commands take what they are given: a missing argument is the caller's error
        call(monkeypatch, capsys, "driver_engine", "update-get", "9")
    code, _out, err = call(monkeypatch, capsys, "igpu_engine", "fetch", "pin:whateverxe", str(tmp_path))
    assert code == 64 and err == "unknown download pin:whateverxe\n"
    code, _out, err = call(monkeypatch, capsys, "igpu_engine", "releases", "whateverxe", "extra")
    assert code == 64 and err.startswith(USAGE_START["igpu_engine"])


def test_host_setup_reads_its_payload_before_it_looks_at_the_command(monkeypatch, capsys):
    """Only the commands that need no payload skip the stdin read: an unknown command with broken JSON fails on the JSON."""
    with pytest.raises(ValueError):
        call(monkeypatch, capsys, "host_setup", "no-such-command", stdin="not json")
    with pytest.raises(ValueError):
        call(monkeypatch, capsys, "host_setup", stdin="[")
    assert call(monkeypatch, capsys, "host_setup", "undo", stdin="not json")[0] == 0     # no state: nothing to put back
    assert call(monkeypatch, capsys, "host_setup", "state", "x", stdin="not json")[0] == 0


def test_nullmoth_engine_reports_a_release_error_as_exit_1(monkeypatch, capsys):
    def boom(get=None):
        raise nullmoth_engine.ReleaseError("GitHub is down")
    monkeypatch.setattr(nullmoth_engine, "list_releases", boom)
    code, out, err = call(monkeypatch, capsys, "nullmoth_engine", "releases")
    assert code == 1 and out == "" and err == "ERROR: GitHub is down\n"
    monkeypatch.setattr(nullmoth_engine, "resolve", lambda choice, get=None: (_ for _ in ()).throw(OSError("disk")))
    code, _out, err = call(monkeypatch, capsys, "nullmoth_engine", "resolve", "latest")
    assert code == 1 and err == "ERROR: disk\n"
