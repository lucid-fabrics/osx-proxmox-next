"""igpu-check, igpu-finish, igpu-install and the oc_builder pins."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from osx_proxmox_next import cli, igpu_cli, igpu_engine, igpu_finish, oc_builder
from osx_proxmox_next.domain import PlanStep
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.unattended import UnattendedError


def report(ok=True, issues=(), layout=True):
    engine = {"info": {"device": 0x46A6, "model": "Intel Iris Xe Graphics", "family_label": "Alder Lake-P"},
              "layout": {"ok": True, "host_top": 0x4F800000, "max_ram_below_4g_mb": 1342} if layout else None}
    return SimpleNamespace(ok=ok, issues=list(issues), warnings=["careful"], hints=["hint"], engine=engine,
                           layout=engine["layout"], rom_file="x.rom")


def test_parsers_exist():
    p = cli.build_parser()
    assert p.parse_args(["igpu-check"]).cmd == "igpu-check"
    assert p.parse_args(["igpu-finish", "996", "--macos", "tahoe"]).macos == "tahoe"
    a = p.parse_args(["igpu-install", "--vmid", "996", "--name", "igpu-seq", "--macos", "sequoia", "--cores", "4", "--memory",
                      "8192", "--disk", "64", "--bridge", "vmbr0", "--storage", "local-lvm", "--yes"])
    assert a.yes and a.vmid == 996


def test_check_command(monkeypatch, capsys):
    monkeypatch.setattr(igpu_cli, "check_intel_gpu", lambda: report())
    assert cli.run_cli(["igpu-check"]) == 0
    out = capsys.readouterr().out
    assert "Intel Iris Xe Graphics (8086:46a6, Alder Lake-P)" in out and "max-ram-below-4g=1342M" in out and "The host is ready." in out
    monkeypatch.setattr(igpu_cli, "check_intel_gpu", lambda: report(False, ["driver: i915"], layout=False))
    assert cli.run_cli(["igpu-check"]) == 2
    assert "REFUSED: driver: i915" in capsys.readouterr().out
    bare = report(False, ["no device"], layout=False)
    bare.engine["info"] = None
    monkeypatch.setattr(igpu_cli, "check_intel_gpu", lambda: bare)
    assert cli.run_cli(["igpu-check"]) == 2


def test_finish_command(monkeypatch, capsys):
    done = igpu_finish.FinishResult("installed", True, ["note one"])
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: done)
    assert cli.run_cli(["igpu-finish", "996"]) == 0
    out = capsys.readouterr().out
    assert "NOTE: note one" in out and "Metal acceleration" in out and "BETA." in out
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: igpu_finish.FinishResult("not installed: x"))
    assert cli.run_cli(["igpu-finish", "996"]) == 1
    assert "not installed yet: not installed: x" in capsys.readouterr().out
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: igpu_finish.FinishResult(""))
    assert cli.run_cli(["igpu-finish", "996"]) == 1 and "reported nothing" in capsys.readouterr().out

    def boom(vmid, **k):
        raise igpu_finish.FinishError("no way")
    monkeypatch.setattr(igpu_finish, "finish", boom)
    assert cli.run_cli(["igpu-finish", "996"]) == 2 and "ERROR: no way" in capsys.readouterr().out
    assert cli.run_cli(["igpu-finish", "5"]) == 2 and "VMID must be between" in capsys.readouterr().out


class Pve:
    def __init__(self, start_ok=True):
        self.calls, self.start_ok = [], start_ok

    def qm(self, *a):
        self.calls.append(a)
        from osx_proxmox_next.infrastructure import CommandResult
        return CommandResult(not (a[0] == "start" and not self.start_ok), 0, "boom")


@pytest.fixture
def install(monkeypatch):
    s = SimpleNamespace(applied=[], unattended=None, finished=None, fetched=None, saved=None)
    monkeypatch.setattr(cli, "_validate_and_fetch_assets", lambda args, config: s.fetched)
    monkeypatch.setattr(igpu_cli, "check_intel_gpu", lambda: report())
    import osx_proxmox_next.planner as planner
    monkeypatch.setattr(planner, "build_plan", lambda config: [PlanStep("Create VM shell", ["true"]), PlanStep("Start VM", ["qm", "start", "9"])])
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: SimpleNamespace(path="snap"))
    monkeypatch.setattr("osx_proxmox_next.rollback.rollback_hints", lambda snap: ["restore it"])

    def apply(steps, execute=True, **k):
        s.applied = [x.title for x in steps]
        failed = getattr(s, "apply_fail", False)
        return ApplyResult(not failed, [StepResult("Create VM shell", "c", not failed, 1, "x")], Path("/log"))
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan", apply)
    s.pve = Pve()
    monkeypatch.setattr("osx_proxmox_next.services.get_proxmox_adapter", lambda: s.pve)

    class Console:
        def __init__(self, vmid):
            pass

        def save_frame(self, path):
            return "/shot.png"
    monkeypatch.setattr("osx_proxmox_next.unattended.QmConsole", Console)

    def unattended(console, disk, on_event=None, recovery_slot="ide2", **k):
        s.slot = recovery_slot
        if s.unattended:
            raise s.unattended
        return {"reboots": 2, "elapsed": 60}
    monkeypatch.setattr("osx_proxmox_next.unattended.run_unattended_install", unattended)
    monkeypatch.setattr(igpu_finish, "wait_stopped", lambda *a: True)

    def finish(vmid, **k):
        if s.finished:
            raise s.finished
        return igpu_finish.FinishResult("installed", True)
    monkeypatch.setattr(igpu_finish, "finish", finish)
    return s


ARGV = ["igpu-install", "--vmid", "996", "--name", "igpu-seq", "--macos", "sequoia", "--cores", "4", "--memory", "8192",
        "--disk", "64", "--bridge", "vmbr0", "--storage", "local-lvm", "--yes"]


def test_install_happy_path(install, capsys):
    assert cli.run_cli(ARGV) == 0
    assert install.applied[0] == "Create VM shell" and "Start VM" not in install.applied
    assert install.applied[-3:] == ["Use the i440fx machine type", "Move the recovery disk to sata1 (i440fx has no macOS driver for IDE)",
                                    "Set FadtEnableReset in OpenCore (restarts hang without it)"]
    assert install.slot == "sata1" and ("start", "996") in install.pve.calls and ("stop", "996") in install.pve.calls
    assert "Metal acceleration" in capsys.readouterr().out


def test_install_asks_unless_yes(install, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert cli.run_cli([a for a in ARGV if a != "--yes"]) == 2
    assert install.applied == [] and "Nothing was changed." in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert cli.run_cli([a for a in ARGV if a != "--yes"]) == 0


def test_install_stops_early(install, monkeypatch, capsys):
    install.fetched = 3
    assert cli.run_cli(ARGV) == 3
    install.fetched = None
    monkeypatch.setattr(igpu_cli, "check_intel_gpu", lambda: report(False, ["driver: i915"]))
    assert cli.run_cli(ARGV) == 2 and install.applied == [] and "Prepare the host first" in capsys.readouterr().out


def test_install_failures(install, capsys):
    install.apply_fail = True
    assert cli.run_cli(ARGV) == 4
    assert "ROLLBACK: restore it" in capsys.readouterr().out
    install.apply_fail = False
    install.pve = Pve(start_ok=False)
    assert cli.run_cli(ARGV) == 4 and "could not start VM" in capsys.readouterr().out
    install.pve = Pve()
    install.unattended = UnattendedError("stuck")
    assert cli.run_cli(ARGV) == 1
    out = capsys.readouterr().out
    assert "stuck" in out and "/shot.png" in out and "igpu-finish 996" in out
    install.unattended = None
    install.finished = igpu_finish.FinishError("no card")
    assert cli.run_cli(ARGV) == 2 and "igpu-finish 996" in capsys.readouterr().out


def test_pins_are_the_engines():
    assert oc_builder.WHATEVERXE.sha256 == igpu_engine.PINS["whateverxe"][2] and oc_builder.WHATEVERXE.version == "0.8.0"
    assert oc_builder.AMFIPASS_MOD.filename == "AMFIPass-v1.4.1-Mod-RELEASE.zip"
    assert oc_builder.AMFIPASS_MOD.sha256 != oc_builder.AMFIPASS.sha256  # not the NullMoth build
    assert oc_builder.GRAPHICS_PKG.filename == "WhateverXe-Graphics-0.4.1.pkg"
    assert set(oc_builder.IGPU_ROMS) == set(igpu_engine.ROMS)


def test_ensure_igpu_assets_fetches_and_verifies(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(oc_builder, "_fetch_component", lambda name, comp, dest, prog=None: seen.append(name) or dest / comp.filename)
    out = oc_builder.ensure_igpu_assets(tmp_path / "d", "adl-h")
    assert seen == ["whateverxe", "amfipass", "rom", "graphics"] and out["rom"].name == "ADL-H_RPL-H_GOPv21_igd.rom"
