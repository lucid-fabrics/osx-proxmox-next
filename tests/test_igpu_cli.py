"""igpu-check, igpu-finish, igpu-install and the oc_builder pins."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from osx_proxmox_next import cli, igpu_attach, igpu_cli, igpu_engine, igpu_finish, oc_builder
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


def test_finish_asks_unless_yes(monkeypatch, capsys):
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: igpu_finish.FinishResult("installed", True, [], True, "RCS_TAIL=0x1"))
    assert cli.run_cli(["igpu-finish", "996"]) == 2  # no terminal, no --yes: nothing happens
    assert "Nothing was changed" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    answers = iter(["y", "", ""])  # the confirmation, then Enter (latest) on both version lists
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert cli.run_cli(["igpu-finish", "996"]) == 0


def test_finish_command(monkeypatch, capsys):
    done = igpu_finish.FinishResult("installed", True, ["note one"], True, "RCS_TAIL=0x1")
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: done)
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "NOTE: note one" in out and "Metal acceleration is active" in out and "BETA." in out
    unconfirmed = igpu_finish.FinishResult("installed", True, [], False, "BCS_TAIL=0x0, RCS_TAIL=0x0")
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: unconfirmed)
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "acceleration was not confirmed" in out and "Metal acceleration is active" not in out
    assert igpu_cli.TROUBLESHOOTING_URL in out and "BCS_TAIL=0x0" in out
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: igpu_finish.FinishResult("not installed: x"))
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 1
    assert "not installed yet: not installed: x" in capsys.readouterr().out
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: igpu_finish.FinishResult(""))
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 1 and "reported nothing" in capsys.readouterr().out

    def boom(vmid, **k):
        raise igpu_finish.FinishError("no way")
    monkeypatch.setattr(igpu_finish, "finish", boom)
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 2 and "ERROR: no way" in capsys.readouterr().out
    assert cli.run_cli(["igpu-finish", "5", "--yes"]) == 2 and "VMID must be between" in capsys.readouterr().out


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
        return igpu_finish.FinishResult("installed", True, [], True, "RCS_TAIL=0x1")
    monkeypatch.setattr(igpu_finish, "finish", finish)
    return s


ARGV = ["igpu-install", "--vmid", "996", "--name", "igpu-seq", "--macos", "sequoia", "--cores", "4", "--memory", "8192",
        "--disk", "64", "--bridge", "vmbr0", "--storage", "local-lvm", "--yes"]


def test_install_happy_path(install, capsys):
    assert cli.run_cli(ARGV) == 0
    assert install.applied[0] == "Create VM shell" and "Start VM" not in install.applied
    assert install.applied[-4:] == ["Note the macOS release in the VM description", "Use the i440fx machine type", "Move the recovery disk to sata1 (i440fx has no macOS driver for IDE)",
                                    "Set FadtEnableReset in OpenCore (restarts hang without it)"]
    assert install.slot == "sata1" and ("start", "996") in install.pve.calls and ("stop", "996") in install.pve.calls
    out = capsys.readouterr().out
    assert "Metal acceleration is active" in out
    # vga is none, so the Proxmox console is blank: Setup Assistant is finished on the host's monitor, never "in the console"
    assert out.rstrip().endswith(igpu_attach.SETUP_ASSISTANT_NOTE) and "Finish Setup Assistant in the VM console" not in out


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
    assert set(igpu_engine.PINS) == {"amfipass"}  # WhateverXe and its graphics package come from GitHub releases
    assert not hasattr(oc_builder, "WHATEVERXE") and not hasattr(oc_builder, "GRAPHICS_PKG")
    assert oc_builder.AMFIPASS_MOD.filename == "AMFIPass-v1.4.1-Mod-RELEASE.zip"
    assert oc_builder.AMFIPASS_MOD.sha256 != oc_builder.AMFIPASS.sha256  # not the NullMoth build
    assert set(oc_builder.IGPU_ROMS) == set(igpu_engine.ROMS)


def test_ensure_igpu_assets_fetches_and_verifies(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(oc_builder, "_fetch_component", lambda name, comp, dest, prog=None: seen.append(name) or dest / comp.filename)
    out = oc_builder.ensure_igpu_assets(tmp_path / "d", "adl-h")
    assert seen == ["whateverxe", "amfipass", "rom", "graphics"] and out["rom"].name == "ADL-H_RPL-H_GOPv21_igd.rom"


# ── release choice, igpu-versions, igpu-report ───────────────────────

def test_the_version_flags_are_parsed_for_finish_and_install():
    p = cli.build_parser()
    a = p.parse_args(["igpu-finish", "996", "--wex-version", "0.8.0", "--graphics-version", "v0.4.0"])
    assert a.wex_version == "0.8.0" and a.graphics_version == "v0.4.0"
    assert p.parse_args(["igpu-finish", "996"]).wex_version == ""
    b = p.parse_args(ARGV + ["--wex-version", "0.8.0"])
    assert b.wex_version == "0.8.0" and b.graphics_version == ""
    assert p.parse_args(["igpu-report", "996", "--out", "/x"]).out == "/x"


def test_finish_passes_the_chosen_releases_and_defaults_to_latest(monkeypatch):
    seen = []
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: seen.append(k) or igpu_finish.FinishResult("installed", True, [], True, "x"))
    assert cli.run_cli(["igpu-finish", "996", "--yes", "--wex-version", "0.8.0"]) == 0
    assert (seen[0]["wex"], seen[0]["graphics"]) == ("0.8.0", "latest")
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 0
    assert (seen[1]["wex"], seen[1]["graphics"]) == ("latest", "latest")


def test_finish_on_a_terminal_lists_the_releases_for_what_was_not_given(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: seen.append(k) or igpu_finish.FinishResult("installed", True, [], True, "x"))
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    answers = iter(["y", "2"])  # the confirmation, then the second WhateverXe release; Graphics comes from the flag
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert cli.run_cli(["igpu-finish", "996", "--graphics-version", "v0.4.0"]) == 0
    assert (seen[0]["wex"], seen[0]["graphics"]) == ("0.8.0", "v0.4.0")
    assert "WhateverXe releases on GitHub, newest first" in capsys.readouterr().out


def test_finish_reports_an_unreachable_github_plainly(monkeypatch, capsys):
    def boom(vmid, **k):
        raise igpu_engine.ReleaseError("Could not reach GitHub (x). Check the network and run the command again.")
    monkeypatch.setattr(igpu_finish, "finish", boom)
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 2
    assert "ERROR: Could not reach GitHub" in capsys.readouterr().out


def test_finish_prints_what_was_installed(monkeypatch, capsys):
    done = igpu_finish.FinishResult("installed", True, [], True, "RCS_TAIL=0x1", versions=["WhateverXe 0.9.0 (latest when installed)"])
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: done)
    assert cli.run_cli(["igpu-finish", "996", "--yes"]) == 0
    assert "Installed: WhateverXe 0.9.0 (latest when installed)" in capsys.readouterr().out


def test_install_checks_a_chosen_release_before_the_long_install(install, capsys):
    assert cli.run_cli(ARGV + ["--wex-version", "9.9.9"]) == 2
    assert "no release tagged 9.9.9" in capsys.readouterr().out and install.applied == []
    assert cli.run_cli(ARGV + ["--wex-version", "0.8.0"]) == 0 and install.applied  # a real tag goes through


def test_install_hands_the_choice_to_finish(install, monkeypatch):
    seen = []
    monkeypatch.setattr(igpu_finish, "finish", lambda vmid, **k: seen.append(k) or igpu_finish.FinishResult("installed", True, [], True, "x"))
    assert cli.run_cli(ARGV + ["--wex-version", "0.8.0", "--graphics-version", "latest"]) == 0
    assert (seen[0]["wex"], seen[0]["graphics"]) == ("0.8.0", "latest")


def test_versions_command_lists_both_newest_first_with_the_latest_marked(capsys):
    assert cli.run_cli(["igpu-versions"]) == 0
    out = capsys.readouterr().out
    assert out.index("0.9.0") < out.index("0.8.0") and "0.9.0  2026-11-01 (latest)" in out
    assert "WhateverXe Graphics releases, newest first:" in out and "v0.4.1  2026-10-02 (latest)" in out


def test_versions_command_shows_prereleases_and_reports_github_errors(monkeypatch, capsys):
    monkeypatch.setattr(igpu_engine, "list_releases", lambda kind, get=None: [
        {"tag": "1.0-rc1", "published": "2026-11-09T00:00:00Z", "prerelease": True},
        {"tag": "0.9.0", "published": "2026-11-01T00:00:00Z", "prerelease": False}])
    assert cli.run_cli(["igpu-versions"]) == 0
    assert "1.0-rc1  2026-11-09 (pre-release)" in capsys.readouterr().out

    def boom(kind, get=None):
        raise igpu_engine.ReleaseError("GitHub refused the request (rate limit reached). Wait a few minutes.")
    monkeypatch.setattr(igpu_engine, "list_releases", boom)
    assert cli.run_cli(["igpu-versions"]) == 2
    assert "rate limit" in capsys.readouterr().out


def test_report_command_prints_the_summary_and_the_bundle(monkeypatch, capsys, tmp_path):
    from osx_proxmox_next import igpu_report
    monkeypatch.setattr(igpu_report, "report", lambda vmid, out=None: (tmp_path / "r.tar.gz", f"summary for {vmid} {out}\n"))
    assert cli.run_cli(["igpu-report", "996", "--out", str(tmp_path)]) == 0
    assert f"summary for 996 {tmp_path}" in capsys.readouterr().out
    assert cli.run_cli(["igpu-report", "996"]) == 0
    assert "summary for 996 None" in capsys.readouterr().out

    def boom(vmid, out=None):
        raise OSError("read-only file system")
    monkeypatch.setattr(igpu_report, "report", boom)
    assert cli.run_cli(["igpu-report", "996"]) == 2
    assert "could not write the report: read-only file system" in capsys.readouterr().out
