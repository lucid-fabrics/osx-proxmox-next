"""osx-next-cli driver-status, driver-update and driver-undo."""
from __future__ import annotations

import argparse
import json

import pytest

from driver_fakes import DPve, comps, mac
from osx_proxmox_next import cli, driver_check, driver_cli, driver_core, driver_engine as engine, driver_plan, driver_spawn
from osx_proxmox_next.driver_core import RunResult
from osx_proxmox_next.driver_targets import Choice, Target

NEW_SNAP = "osx-next-driver-20261121-1030"


@pytest.fixture
def host(monkeypatch):
    state = {"pve": DPve(snapshots=[NEW_SNAP]), "ran": [], "result": RunResult(True, notes=["n1"], snapshot=NEW_SNAP, detail="5 extensions loaded"),
             "tty": False}

    def adapter():
        return state["pve"]
    monkeypatch.setattr(driver_plan, "get_proxmox_adapter", adapter)
    monkeypatch.setattr(driver_cli, "get_proxmox_adapter", adapter)
    monkeypatch.setattr(driver_plan, "resolve", lambda kind, choice, get=None: Target(kind, {
        "NullMoth": {"version": choice.nullmoth or "v2.9", "tag": choice.nullmoth or "v2.9", "sha256": "", "source": "latest"}}))
    monkeypatch.setattr(driver_cli, "run_update", lambda plan, env: state["ran"].append((plan, env)) or state["result"])
    monkeypatch.setattr(driver_cli, "QmConsole", lambda vmid: None)
    monkeypatch.setattr(driver_cli.sys.stdin, "isatty", lambda: state["tty"], raising=False)
    engine.write_record(109, "nvidia", comps(NullMoth="v2.8.1"))
    return state


def run(*argv):
    return cli.run_cli(list(argv))


def test_status_shows_installed_latest_and_snapshots(host, capsys):
    assert run("driver-status", "--vmid", "109") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "VM 109 macos-sequoia: NVIDIA card" and out[1] == "Installed: NullMoth v2.8.1" and out[2] == "Latest:    NullMoth v2.9"
    assert out[3] == "A newer driver is available." and out[4] == "Driver snapshots: " + NEW_SNAP


def test_status_for_a_mac_with_no_record_and_one_that_is_up_to_date_or_cut_off(host, capsys):
    engine.record_path(109).unlink()
    from osx_proxmox_next import gpu
    gpu.record_path(109).parent.mkdir(parents=True, exist_ok=True)
    gpu.record_path(109).write_text('{"vmid": 109, "bdf": "x", "vendor": "nvidia"}')
    assert run("driver-status", "--vmid", "109") == 0
    out = capsys.readouterr().out
    assert "Installed: installed version unknown" in out and "The installed version is not known." in out
    engine.write_record(109, "nvidia", comps(NullMoth="v2.9"))
    engine.write_update(109, {"kind": "nvidia", "phase": "failed"})
    host["pve"].snapshots.clear()
    assert run("driver-status", "--vmid", "109") == 0
    out = capsys.readouterr().out
    assert "Up to date." in out and "Driver snapshots: none" in out and "driver-update --vmid 109 --finish --yes" in out
    assert "The driver update stopped before it finished." in out and "driver-undo --vmid 109" in out


def test_status_as_json(host, capsys):
    assert run("driver-status", "--vmid", "109", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["kind"] == "nvidia" and data["installed"] == {"NullMoth": "v2.8.1"} and data["latest"] == {"NullMoth": "v2.9"}
    assert data["state"] == "update" and data["snapshots"] == [NEW_SNAP] and data["unfinished"] is False and data["refused"] == []


def test_status_of_a_mac_that_is_refused(host, capsys):
    host["pve"] = DPve(cfg=mac(hostpci0=None))
    assert run("driver-status", "--vmid", "109") == 2
    assert "REFUSED: " + engine.REFUSE_NO_CARD in capsys.readouterr().out
    assert run("driver-status", "--vmid", "109", "--json") == 2
    assert json.loads(capsys.readouterr().out)["refused"] == [engine.REFUSE_NO_CARD]
    host["pve"] = DPve(cfg=mac(args="-smbios x"))
    assert run("driver-status", "--vmid", "109") == 0       # the kind is known, so the rest of the answer is still shown
    assert "REFUSED" in capsys.readouterr().out


def test_a_vm_id_out_of_range_is_refused_by_every_command(host, capsys):
    for cmd in ("driver-status", "driver-update", "driver-undo"):
        assert run(cmd, "--vmid", "5") == 2
        assert "VMID must be between" in capsys.readouterr().out


def test_a_dry_run_prints_the_plan_and_changes_nothing(host, capsys):
    assert run("driver-update", "--vmid", "109", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "Update the NVIDIA driver of macos-sequoia (VM 109)" in out and "  NullMoth: v2.8.1 to v2.9" in out
    assert "  4. Stage NullMoth v2.9" in out and "DRY RUN - nothing is changed." in out and host["ran"] == []
    assert run("driver-update", "--vmid", "109", "--dry-run", "--version", "v2.8.2") == 0
    assert "NullMoth: v2.8.1 to v2.8.2" in capsys.readouterr().out


def test_the_nullmoth_risk_must_be_accepted_to_apply(host, capsys):
    assert run("driver-update", "--vmid", "109", "--yes") == 2
    out = capsys.readouterr().out
    assert "NVIDIA GPU support (Beta)" in out and "re-run with --accept-nullmoth-risk to apply" in out and host["ran"] == []


def test_an_update_runs_after_confirmation(host, capsys):
    assert run("driver-update", "--vmid", "109", "--yes", "--accept-nullmoth-risk") == 0
    out = capsys.readouterr().out
    assert "NOTE: n1" in out and "Done. 5 extensions loaded." in out and "Driver snapshot " + NEW_SNAP + " is kept. driver-undo goes back to it." in out
    plan, env = host["ran"][0]
    assert plan.vmid == 109 and env.no_snapshot is False and str(env.iso_dir)


def test_without_yes_it_asks_and_a_no_changes_nothing(host, capsys, monkeypatch):
    assert run("driver-update", "--vmid", "109", "--accept-nullmoth-risk") == 2     # not a terminal: nobody to ask
    assert "Nothing was changed." in capsys.readouterr().out and host["ran"] == []
    host["tty"] = True
    answers = iter(["n", "y"])
    args = argparse.Namespace(vmid=109, version="", wex_version="", graphics_version="", dry_run=False, yes=False,
                              accept_risk=True, no_snapshot=False, reinstall=False, finish=False, macos="", iso_dir="")
    assert driver_cli.run_update_command(args, ask=lambda p: next(answers), say=lambda m: None) == 2
    assert host["ran"] == []
    assert driver_cli.run_update_command(args, ask=lambda p: next(answers), say=lambda m: None) == 0
    assert len(host["ran"]) == 1


def test_up_to_date_and_reinstall(host, capsys):
    engine.write_record(109, "nvidia", comps(NullMoth="v2.9"))
    assert run("driver-update", "--vmid", "109", "--yes", "--accept-nullmoth-risk") == 0
    assert capsys.readouterr().out.rstrip().endswith(engine.UP_TO_DATE) and host["ran"] == []
    assert run("driver-update", "--vmid", "109", "--yes", "--accept-nullmoth-risk", "--reinstall") == 0
    assert len(host["ran"]) == 1


def test_a_refused_plan_exits_2(host, capsys):
    host["pve"] = DPve(cfg=mac(hostpci0=None))
    assert run("driver-update", "--vmid", "109", "--yes") == 2
    assert "Refused: " + engine.REFUSE_NO_CARD in capsys.readouterr().out


def test_storage_without_snapshots_needs_the_phrase(host, capsys):
    host["pve"] = DPve(storage="Name Type\nlocal-lvm lvm\n")
    assert run("driver-update", "--vmid", "109", "--yes", "--accept-nullmoth-risk") == 2
    out = capsys.readouterr().out
    assert engine.NO_SNAPSHOT_WARNING in out and "Re-run with --no-snapshot" in out and host["ran"] == []
    assert run("driver-update", "--vmid", "109", "--yes", "--accept-nullmoth-risk", "--no-snapshot") == 0
    assert host["ran"][0][1].no_snapshot is True
    host["tty"] = True
    args = argparse.Namespace(vmid=109, version="", wex_version="", graphics_version="", dry_run=False, yes=True,
                              accept_risk=True, no_snapshot=False, reinstall=False, finish=False, macos="", iso_dir="/x")
    assert driver_cli.run_update_command(args, ask=lambda p: "no snapshot", say=lambda m: None) == 0
    assert args.no_snapshot is True
    args.no_snapshot = False
    assert driver_cli.run_update_command(args, ask=lambda p: "yes", say=lambda m: None) == 2


def test_a_failed_run_is_reported_with_its_reason(host, capsys):
    host["result"] = RunResult(False, error="The NullMoth driver is not confirmed.", notes=["n"])
    assert run("driver-update", "--vmid", "109", "--yes", "--accept-nullmoth-risk") == 1
    out = capsys.readouterr().out
    assert "The update did not finish: The NullMoth driver is not confirmed." in out and "NOTE: n" in out


def test_a_refusal_raised_by_the_run_is_an_error(host, capsys, monkeypatch):
    def refuse(plan, env):
        raise driver_core.DriverError("Another driver update is running for this Mac.")
    monkeypatch.setattr(driver_cli, "run_update", refuse)
    assert run("driver-update", "--vmid", "109", "--yes", "--accept-nullmoth-risk") == 2
    assert "ERROR: Another driver update" in capsys.readouterr().out


def test_finish_continues_an_interrupted_update(host, capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(driver_cli, "finish_interrupted", lambda vmid, env: seen.append(vmid) or host["result"])
    assert run("driver-update", "--vmid", "109", "--finish", "--yes") == 0 and seen == [109]

    def none(vmid, env):
        raise driver_core.DriverError("There is no unfinished driver update for this Mac.")
    monkeypatch.setattr(driver_cli, "finish_interrupted", none)
    assert run("driver-update", "--vmid", "109", "--finish") == 2
    assert "no unfinished driver update" in capsys.readouterr().out


def test_undo_asks_then_rolls_back(host, capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(driver_cli, "put_back", lambda vmid, env: seen.append(vmid) or NEW_SNAP)
    assert run("driver-undo", "--vmid", "109") == 2                     # not a terminal, no --yes
    assert seen == [] and f"its disks go back to the snapshot of 2026-11-21 10:30 ({NEW_SNAP})" in capsys.readouterr().out
    assert run("driver-undo", "--vmid", "109", "--yes") == 0
    assert seen == [109] and "VM 109 is back to " + NEW_SNAP in capsys.readouterr().out
    host["pve"].snapshots.clear()
    assert run("driver-undo", "--vmid", "109", "--yes") == 2
    assert "nothing to undo" in capsys.readouterr().out

    host["pve"].snapshots.append(NEW_SNAP)

    def refuse(vmid, env):
        raise driver_core.DriverError("Another driver update is running for this Mac.")
    monkeypatch.setattr(driver_cli, "put_back", refuse)
    assert run("driver-undo", "--vmid", "109", "--yes") == 1


def test_status_of_a_mac_whose_update_stopped_with_the_card_off_says_so_plainly(host, capsys):
    host["pve"] = DPve(cfg=mac(hostpci0=None), snapshots=[NEW_SNAP])
    engine.write_update(109, {"kind": "nvidia", "phase": "failed", "snapshot": NEW_SNAP})
    assert run("driver-status", "--vmid", "109") == 0
    out = capsys.readouterr().out
    assert "REFUSED" not in out and "NVIDIA card" in out
    assert "The driver update stopped before it finished. Finish it: osx-next-cli driver-update --vmid 109 --finish --yes" in out
    assert run("driver-status", "--vmid", "109", "--json") == 0
    assert json.loads(capsys.readouterr().out)["unfinished"] is True


def test_undo_is_refused_before_it_asks_when_the_card_setup_changed(host, capsys):
    engine.write_record(109, "nvidia", comps(NullMoth="v2.9"), previous=comps(NullMoth="v2.8.1"), snapshot=NEW_SNAP, card="0000:09:00")
    assert run("driver-undo", "--vmid", "109", "--yes") == 2
    assert "card setup of this Mac changed" in capsys.readouterr().out


def test_a_release_or_macos_that_is_not_one_is_refused(host, capsys):
    assert run("driver-update", "--vmid", "109", "--dry-run", "--macos", "x;rm") == 2
    assert "--macos must be one of" in capsys.readouterr().out
    assert run("driver-update", "--vmid", "109", "--dry-run", "--macos", "sequoia") == 0
    capsys.readouterr()
    assert run("driver-update", "--vmid", "109", "--dry-run", "--version", "v2.9; id") == 2
    assert "--version 'v2.9; id' is not a release tag" in capsys.readouterr().out
    assert run("driver-update", "--vmid", "109", "--dry-run", "--wex-version", "../x") == 2


def test_dispatch_ignores_other_commands():
    assert driver_cli.dispatch(argparse.Namespace(cmd="igpu-check")) is None


def test_the_detached_spawn_passes_only_checked_tags(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(driver_spawn, "log_path", lambda vmid: str(tmp_path / f"d{vmid}.log"))
    path = driver_spawn.spawn_update(109, Choice("v2.8.1", "", "", "sequoia"), finish=True, accept_risk=True, no_snapshot=True,
                                     popen=lambda argv, **kw: seen.update(argv=argv, kw=kw))
    assert path.endswith("d109.log")
    assert seen["argv"][7:] == ["--version", "v2.8.1", "--macos", "sequoia", "--finish", "--accept-nullmoth-risk", "--no-snapshot", "--reinstall"]
    assert seen["argv"][3:7] == ["driver-update", "--vmid", "109", "--yes"] and seen["kw"]["start_new_session"] is True
    with pytest.raises(ValueError):
        driver_spawn.spawn_update(109, Choice("v2.9; rm -rf /"), popen=lambda *a, **k: None)


def test_the_log_of_a_detached_update_is_in_var_log():
    assert driver_spawn.log_path(7) == "/var/log/osx-next-driver-7.log"


def test_the_home_marker_reads_local_files_and_checks_github_once_a_day(monkeypatch):
    engine.write_record(109, "nvidia", comps(NullMoth="v2.8.1"))
    engine.write_record(110, "amd", comps(Lilu="1.6.8", WhateverGreen="1.7.0"))
    engine.write_record(111, "amd", comps(Lilu="1.7.2", WhateverGreen="1.7.0"))
    assert not driver_check.has_update(109) and driver_check.has_update(110) and not driver_check.has_update(111)   # AMD pins need no network
    assert not driver_check.has_update(404)
    seen = []

    def latest(get=None):
        seen.append(1)
        return {"NullMoth": "v2.9"}
    monkeypatch.setattr(driver_check, "latest_known", latest)
    assert driver_check.refresh_if_due([404]) is False and seen == []                # no Mac with a record: no network
    assert driver_check.refresh_if_due([109], now=1000.0) is True and seen == [1]
    assert driver_check.has_update(109)
    assert driver_check.refresh_if_due([109], now=1100.0) is False and seen == [1]   # not due again for a day

    def offline(get=None):
        raise OSError("offline")
    monkeypatch.setattr(driver_check, "latest_known", offline)
    assert driver_check.refresh_if_due([109], now=1000.0 + 90000) is False
    assert driver_check.latest_map()["Lilu"] == "1.7.2"
