"""osx-next-cli arm64: plan, apply and uninstall of the runner VM."""
from __future__ import annotations

import json

import pytest

from osx_proxmox_next import arm64_cli
from osx_proxmox_next.cli import build_parser, run_cli
from osx_proxmox_next.executor import ApplyResult, StepResult

BASE = ["arm64", "plan", "--vmid", "478", "--name", "macos27-runner", "--storage", "sn850x"]
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc runner@host"


@pytest.fixture(autouse=True)
def _private_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)    # a dry run writes its log under ./generated


def test_the_command_is_registered():
    cmds = build_parser()._subparsers._group_actions[0].choices
    assert "arm64" in cmds


def test_plan_prints_every_step_without_running_anything(capsys):
    assert run_cli(BASE) == 0
    out = capsys.readouterr().out
    assert " 4. Create the runner VM\n    qm create 478 --name macos27-runner" in out
    assert "Fetch the Debian 13 cloud image\n    bash script (3 arguments)" in out
    assert "qm start 478" in out


def test_plan_json_is_machine_readable(capsys):
    assert run_cli([*BASE, "--json", "--cpuunits", "100", "--gpu-pci", "03:00.0"]) == 0
    steps = json.loads(capsys.readouterr().out)
    assert steps[0]["title"] == "Check snippet storage" and steps[-1]["argv"] == ["qm", "start", "478"]
    assert any(s["title"] == "Pass the GPU to the runner" and s["risk"] == "warn" for s in steps)


def test_apply_without_execute_is_a_dry_run(capsys, tmp_path):
    assert run_cli(["arm64", "apply", "--vmid", "478", "--name", "macos27-runner", "--storage", "sn850x"]) == 0
    out = capsys.readouterr().out
    assert "ok   Create the runner VM" in out and "Log: " in out
    log = next((tmp_path / "generated" / "logs").glob("apply-*.log")).read_text()
    assert "[DRY-RUN] Create the runner VM: qm create 478" in log


def test_apply_execute_runs_the_plan_and_reports_the_first_failure(monkeypatch, capsys):
    seen = []

    def fake_apply(steps, execute=False, adapter=None, on_step=None):
        seen.append((len(steps), execute))
        return ApplyResult(False, [StepResult("Check snippet storage", "x", True, 0, ""),
                                   StepResult("Create the runner VM", "x", False, 2, "storage is full")],
                           log_path=__import__("pathlib").Path("/tmp/x.log"))

    monkeypatch.setattr(arm64_cli, "apply_plan", fake_apply)
    assert run_cli(["arm64", "apply", "--vmid", "478", "--name", "macos27-runner", "--storage", "sn850x",
                    "--execute"]) == 1
    out = capsys.readouterr().out
    assert seen == [(9, True)] and "FAIL Create the runner VM\nstorage is full" in out


def test_the_ssh_key_file_is_read_into_the_plan(tmp_path, capsys):
    key = tmp_path / "id.pub"
    key.write_text(KEY + "\n")
    assert run_cli([*BASE, "--ssh-key-file", str(key), "--json"]) == 0
    steps = json.loads(capsys.readouterr().out)
    cloud = next(s for s in steps if s["title"] == "Set up cloud-init")["argv"]
    assert "--sshkeys" in cloud
    assert next(s for s in steps if s["title"] == "Write the SSH key file")["argv"][4] == KEY


def test_a_missing_key_file_is_an_error_not_a_traceback(capsys):
    assert run_cli([*BASE, "--ssh-key-file", "/nonexistent/id.pub"]) == 2
    assert "cannot read the key file" in capsys.readouterr().out


def test_invalid_values_are_listed_and_nothing_runs(capsys):
    assert run_cli(["arm64", "plan", "--vmid", "5", "--name", "a;b", "--storage", "x y"]) == 2
    out = capsys.readouterr().out
    assert "ERROR: VMID" in out and "ERROR: VM name" in out and "ERROR: Storage" in out


def test_uninstall_dry_run_lists_the_destroy_steps(capsys, tmp_path):
    assert run_cli(["arm64", "uninstall", "--vmid", "478"]) == 0
    out = capsys.readouterr().out
    assert "ok   Destroy VM" in out and "ok   Remove the cloud-init files" in out
    log = next((tmp_path / "generated" / "logs").glob("apply-*.log")).read_text()
    assert "qm destroy 478 --purge" in log


def test_uninstall_refuses_an_out_of_range_vmid(capsys):
    assert run_cli(["arm64", "uninstall", "--vmid", "5"]) == 2
    assert "VMID must be between" in capsys.readouterr().out
