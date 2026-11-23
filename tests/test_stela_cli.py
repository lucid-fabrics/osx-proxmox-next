"""osx-next-cli stela: plan, apply, uninstall, the GPU list and the whole install call."""
from __future__ import annotations

import json

import pytest

from osx_proxmox_next import stela_cli
from osx_proxmox_next.cli import build_parser, run_cli
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.stela import STELA_RELEASE_URL, StelaConfig
from osx_proxmox_next.stela_gpus import HostGpu
from osx_proxmox_next.stela_install import InstallResult

BASE = ["stela", "plan", "--vmid", "478", "--name", "macos27-stela", "--storage", "local-lvm"]


@pytest.fixture(autouse=True)
def _private_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)    # a dry run writes its log under ./generated


def test_the_command_is_registered():
    assert "stela" in build_parser()._subparsers._group_actions[0].choices


def test_plan_prints_every_step_without_running_anything(capsys):
    assert run_cli(BASE) == 0
    out = capsys.readouterr().out
    assert " 2. Check snippet storage\n    bash script (1 arguments)" in out
    assert " 6. Create the VM\n    qm create 478 --name macos27-stela" in out
    assert "qm start 478" in out


def test_plan_json_lists_the_gpu_step_when_a_gpu_is_chosen(capsys):
    assert run_cli([*BASE, "--json", "--gpu-pci", "0000:0a:00", "--stela-url", "http://127.0.0.1:8915/"]) == 2
    capsys.readouterr()
    assert run_cli([*BASE, "--json", "--gpu-pci", "0000:0a:00"]) == 0
    steps = json.loads(capsys.readouterr().out)
    assert any(s["title"] == "Pass the GPU to the VM" and s["risk"] == "warn" for s in steps)


def test_a_bad_value_is_named_and_exits_2(capsys):
    assert run_cli([*BASE, "--cores", "1"]) == 2
    assert "ERROR: At least 4 CPU cores" in capsys.readouterr().out


def test_apply_without_execute_is_a_dry_run(capsys, tmp_path):
    assert run_cli(["stela", "apply", "--vmid", "478", "--name", "macos27-stela", "--storage", "local-lvm"]) == 0
    out = capsys.readouterr().out
    assert "ok   Create the VM" in out and "Log: " in out
    log = next((tmp_path / "generated" / "logs").glob("apply-*.log")).read_text()
    assert "[DRY-RUN] Create the VM: qm create 478" in log


def test_a_failed_dry_run_step_exits_1(monkeypatch, capsys):
    result = ApplyResult(False, [StepResult("Create the VM", "qm create", False, 1, "boom")], log_path=None)
    monkeypatch.setattr(stela_cli, "apply_plan", lambda steps, execute=False: result)
    assert run_cli(["stela", "apply", "--vmid", "478", "--name", "macos27-stela", "--storage", "local-lvm"]) == 1
    assert "FAIL Create the VM\nboom" in capsys.readouterr().out


def test_uninstall_dry_run_and_a_bad_vmid(capsys):
    assert run_cli(["stela", "uninstall", "--vmid", "478"]) == 0
    assert "ok   Remove the VM's tap" in capsys.readouterr().out
    assert run_cli(["stela", "uninstall", "--vmid", "7"]) == 2
    assert "VMID must be between" in capsys.readouterr().out


def test_gpus_lists_the_host_cards(monkeypatch, capsys):
    monkeypatch.setattr(stela_cli, "list_host_gpus", lambda: [HostGpu("0000:0a:00", "RTX 3060 Ti", "vfio-pci")])
    assert run_cli(["stela", "gpus"]) == 0
    assert capsys.readouterr().out == "0000:0a:00 RTX 3060 Ti (vfio-pci)\n"


# ── apply --execute: the plan, then the keys and the log ─────────────────────────────────────────────────

class FakeHost:
    def __init__(self, config, log_path):
        self.config, self.log_path = config, log_path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def wire(monkeypatch, plan_ok=True, outcome=None):
    stops, said = [], []
    monkeypatch.setattr(stela_cli, "PveHost", FakeHost)
    monkeypatch.setattr(stela_cli, "run_install", lambda host, say, resume=False: outcome or InstallResult(True, "done", {"menu": 1.0}))

    def fake_apply(steps, execute=False, adapter=None, on_step=None):
        for idx, step in enumerate(steps, start=1):
            on_step(idx, len(steps), step, None)
        if not plan_ok:
            on_step(3, len(steps), steps[2], StepResult(steps[2].title, "x", False, 1, "curl: (22) 404"))
        return ApplyResult(plan_ok, [], log_path=None)

    monkeypatch.setattr(stela_cli, "apply_plan", fake_apply)
    monkeypatch.setattr(stela_cli.subprocess, "run", lambda argv, **kw: stops.append(argv))
    return stops, said


CONFIG = StelaConfig(vmid=478, name="macos27-stela", storage="local-lvm")


def test_the_install_runs_the_plan_then_the_driver_and_reports_the_phase_times(monkeypatch):
    stops, _ = wire(monkeypatch)
    lines: list[str] = []
    assert stela_cli.install_vm(StelaConfig(vmid=478, name="macos27-stela", storage="local-lvm", gpu_pci="0a:00"),
                                lines.append) is True
    assert stops == []
    assert lines[0].endswith("[1/8] Check the VMID is free") and lines[0].startswith("[    0s]")
    assert any("Phase times" in line and "menu 1.0" in line for line in lines)


def test_a_failed_plan_step_stops_before_the_driver_and_shows_its_output(monkeypatch):
    wire(monkeypatch, plan_ok=False)
    lines: list[str] = []
    assert stela_cli.install_vm(CONFIG, lines.append) is False
    assert any("FAILED: Download and check Stela\ncurl: (22) 404" in line for line in lines)
    assert not any("Serial log" in line for line in lines)


def test_a_failed_install_stops_the_vm_and_names_the_way_out(monkeypatch):
    stops, _ = wire(monkeypatch, outcome=InstallResult(False, "Stela stopped: no network", {}))
    lines: list[str] = []
    assert stela_cli.install_vm(CONFIG, lines.append) is False
    assert stops == [["qm", "stop", "478"]]
    assert any("osx-next-cli stela uninstall --vmid 478 --execute" in line for line in lines)


def test_apply_execute_returns_the_install_result(monkeypatch, capsys):
    wire(monkeypatch)
    args = ["stela", "apply", "--execute", "--vmid", "478", "--name", "macos27-stela", "--storage", "local-lvm"]
    assert run_cli(args) == 0
    assert "Phase times" in capsys.readouterr().out
    wire(monkeypatch, outcome=InstallResult(False, "x", {}))
    assert run_cli(args) == 1


def test_the_default_download_folder_is_the_pinned_release(monkeypatch, capsys):
    assert run_cli([*BASE, "--json"]) == 0
    argv = next(s["argv"] for s in json.loads(capsys.readouterr().out) if s["title"] == "Download and check Stela")
    assert argv[4] == STELA_RELEASE_URL


# ── finish: add a GPU to an installed VM and boot it ─────────────────────────────────────────────────────

FINISH = ["stela", "finish", "--vmid", "478", "--gpu-pci", "0000:0a:00"]


def test_finish_dry_run_lists_the_steps(capsys, tmp_path):
    assert run_cli(FINISH) == 0
    assert "ok   Pass the GPU to the VM" in capsys.readouterr().out
    log = next((tmp_path / "generated" / "logs").glob("apply-*.log")).read_text()
    assert "[DRY-RUN] Pass the GPU to the VM: qm set 478 --vga none --hostpci0 0000:0a:00,pcie=1,x-vga=1" in log


def test_finish_refuses_a_bad_gpu_address(capsys):
    assert run_cli(["stela", "finish", "--vmid", "478", "--gpu-pci", "0a;00"]) == 2
    assert "ERROR: Invalid finish request" in capsys.readouterr().out


def test_finish_dry_run_failure_exits_1(monkeypatch, capsys):
    result = ApplyResult(False, [StepResult("Stop the VM", "qm stop", False, 1, "boom")], log_path=None)
    monkeypatch.setattr(stela_cli, "apply_plan", lambda steps, execute=False: result)
    assert run_cli(FINISH) == 1


def test_finish_execute_runs_the_plan_then_watches_in_resume_mode(monkeypatch, capsys):
    resumes = []
    wire(monkeypatch)
    monkeypatch.setattr(stela_cli, "run_install", lambda host, say, resume=False: resumes.append(resume) or InstallResult(True, "up", {}))
    assert run_cli([*FINISH, "--execute"]) == 0
    assert resumes == [True] and "Phase times" in capsys.readouterr().out


def test_finish_stops_when_a_plan_step_fails(monkeypatch):
    wire(monkeypatch, plan_ok=False)
    lines: list[str] = []
    assert stela_cli.finish_vm(478, "0000:0a:00", lines.append) is False
    assert not any("Serial log" in line for line in lines)


def test_a_vm_without_a_gpu_is_told_so_before_it_starts(monkeypatch):
    wire(monkeypatch)
    lines: list[str] = []
    stela_cli.install_vm(CONFIG, lines.append)
    assert "NOTE: no GPU is passed" in lines[0]
    lines.clear()
    stela_cli.install_vm(StelaConfig(vmid=478, name="macos27-stela", storage="local-lvm", gpu_pci="0a:00"), lines.append)
    assert not any("NOTE" in line for line in lines)


def test_the_no_gpu_outcome_does_not_suggest_removing_the_vm(monkeypatch):
    from osx_proxmox_next.stela_install import NEEDS_GPU
    wire(monkeypatch, outcome=InstallResult(False, NEEDS_GPU, {}))
    lines: list[str] = []
    assert stela_cli.install_vm(CONFIG, lines.append) is False
    assert not any("uninstall" in line for line in lines)


def test_a_key_that_cannot_be_sent_stops_the_vm_like_any_failure(monkeypatch):
    from osx_proxmox_next.stela_install import SendKeyError
    stops, _ = wire(monkeypatch)

    def boom(host, say, resume=False):
        raise SendKeyError("qm sendkey ret failed: timeout")

    monkeypatch.setattr(stela_cli, "run_install", boom)
    lines: list[str] = []
    assert stela_cli.install_vm(CONFIG, lines.append) is False
    assert stops == [["qm", "stop", "478"]] and any("qm sendkey ret failed" in line for line in lines)
