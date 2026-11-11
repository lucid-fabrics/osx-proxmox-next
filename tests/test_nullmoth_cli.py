"""nvidia-attach and nullmoth-versions on the command line: notice, release pick, dry run, report."""
from __future__ import annotations

import argparse

import pytest

from osx_proxmox_next import cli, nullmoth, nullmoth_cli, nullmoth_finish as nf
from osx_proxmox_next.domain import PlanStep
from osx_proxmox_next.nullmoth import AttachPlan

RELEASES = [{"tag": "v3.0-rc1", "prerelease": True, "published": "2026-11-05T00:00:00Z"},
            {"tag": "v2.9", "prerelease": False, "published": "2026-11-01T00:00:00Z"},
            {"tag": "v2.8", "prerelease": False, "published": "2026-10-01T00:00:00Z"}]


def ns(**kw):
    base = dict(vmid=100, gpu="01:00", accept_risk=True, yes=False, dry_run=False, macos="", iso_dir="",
                nullmoth_version="")
    base.update(kw)
    return argparse.Namespace(**base)


def test_version_lines_mark_the_default_and_prereleases():
    lines = nullmoth_cli.version_lines(RELEASES)
    assert lines[0] == "v3.0-rc1 (pre-release)  2026-11-05"
    assert lines[1] == "v2.9 (latest)  2026-11-01" and lines[2] == "v2.8  2026-10-01"
    assert nullmoth_cli.version_lines([]) == []


def test_choose_version(monkeypatch, capsys):
    assert nullmoth_cli.choose_version(ns(nullmoth_version=" v2.8 ")) == "v2.8"
    assert nullmoth_cli.choose_version(ns(yes=True)) == "latest"
    monkeypatch.setattr(nullmoth_cli.sys.stdin, "isatty", lambda: False)
    assert nullmoth_cli.choose_version(ns()) == "latest"
    monkeypatch.setattr(nullmoth_cli.sys.stdin, "isatty", lambda: True)
    assert nullmoth_cli.choose_version(ns(), ask=lambda p: "", releases=lambda: RELEASES) == "latest"
    assert nullmoth_cli.choose_version(ns(), ask=lambda p: "v2.8", releases=lambda: RELEASES) == "v2.8"
    out = capsys.readouterr().out
    assert "NullMoth releases (newest first):" in out and "v2.9 (latest)" in out


def result(**kw):
    base = dict(version="v2.9", verified=True, result="verified", detail="", kexts=5, display="yes", ip="10.0.0.7",
                ssh_up=True, vnc_up=True, notes=["a note"])
    base.update(kw)
    return nf.FinishResult(**base)


def test_the_report_prints_how_to_connect(capsys):
    assert nullmoth_cli.report(100, result()) == 0
    out = capsys.readouterr().out
    assert "NOTE: a note" in out and "NullMoth v2.9 is installed and loaded (5 extensions)" in out
    assert "ssh <user>@10.0.0.7\n" in out and "vnc://10.0.0.7\n" in out
    assert nullmoth.DISPLAY_LINE not in out  # a monitor was seen: nothing to ask for
    assert all(note in out for note in nullmoth.NOTES)


def test_the_report_asks_for_a_monitor_only_when_none_was_seen(capsys):
    nullmoth_cli.report(100, result(display="no", ssh_up=False, vnc_up=False))
    out = capsys.readouterr().out
    assert nullmoth.DISPLAY_LINE in out and "(not answering yet)" in out
    nullmoth_cli.report(100, result(display="", ip=""))
    assert "address was not found" in capsys.readouterr().out


def test_the_report_of_a_failed_install(capsys):
    assert nullmoth_cli.report(100, result(verified=False, detail="the driver installer stopped: STOP")) == 1
    out = capsys.readouterr().out
    assert "not confirmed in VM 100: the driver installer stopped: STOP" in out and "nullmoth.log" in out
    nullmoth_cli.report(100, result(verified=False, result="", detail=""))
    assert "the guest reported nothing" in capsys.readouterr().out


def test_versions_command(monkeypatch, capsys):
    monkeypatch.setattr(nf, "versions", lambda: RELEASES)
    assert cli.run_cli(["nullmoth-versions"]) == 0
    assert "v2.9 (latest)" in capsys.readouterr().out

    def boom():
        raise nf.FinishError("offline")
    monkeypatch.setattr(nf, "versions", boom)
    assert cli.run_cli(["nullmoth-versions"]) == 1
    assert "ERROR: offline" in capsys.readouterr().out


def plan(ok=True):
    p = AttachPlan(100)
    p.issues = [] if ok else ["card in use"]
    p.hints, p.warnings = ["a hint"], ["a warning"]
    p.steps = [PlanStep(title="Attach the card", argv=["true"])]
    return p


def test_the_notice_must_be_accepted_and_the_vmid_is_checked(capsys):
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00"]) == 2
    out = capsys.readouterr().out
    assert "This feature sets up your VM" in out and "--accept-nullmoth-risk" in out
    assert cli.run_cli(["nvidia-attach", "1", "--gpu", "01:00", "--accept-nullmoth-risk"]) == 2
    assert "VMID must be" in capsys.readouterr().out


def test_dry_run_prints_the_plan_and_changes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(nullmoth, "prepare_attach", lambda *a, **k: plan())
    monkeypatch.setattr(nf, "finish", lambda *a, **k: pytest.fail("a dry run must not run the setup"))
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "verifies it against SHA256SUMS" in out and "01. Attach the card" in out
    assert "WARN: a warning" in out and "  a hint" in out
    monkeypatch.setattr(nullmoth, "prepare_attach", lambda *a, **k: plan(ok=False))
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--dry-run"]) == 2
    assert "REFUSED: card in use" in capsys.readouterr().out


def test_attach_runs_the_whole_setup(monkeypatch, capsys):
    seen = {}

    def finish(vmid, gpu, choice, **kw):
        seen.update(vmid=vmid, gpu=gpu, choice=choice, macos=kw["macos"])
        kw["on_event"]("downloading")
        return result()
    monkeypatch.setattr(nf, "finish", finish)
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk", "--nullmoth-version", "v2.8",
                        "--macos", "tahoe"]) == 0
    assert seen == {"vmid": 100, "gpu": "01:00", "choice": "v2.8", "macos": "tahoe"}
    assert "  downloading" in capsys.readouterr().out
    monkeypatch.setattr(nf, "finish", lambda *a, **k: result(verified=False, detail="x"))
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk", "--yes"]) == 1


def test_attach_reports_a_refusal(monkeypatch, capsys):
    def finish(*a, **k):
        raise nf.FinishError("VM 100 is running.")
    monkeypatch.setattr(nf, "finish", finish)
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk", "--yes"]) == 2
    assert "ERROR: VM 100 is running." in capsys.readouterr().out
