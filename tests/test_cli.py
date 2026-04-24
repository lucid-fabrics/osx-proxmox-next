import subprocess
from pathlib import Path

from osx_proxmox_next import cli as cli_module
from osx_proxmox_next.cli import run_cli


def test_cli_parser_has_expected_commands() -> None:
    from osx_proxmox_next.cli import build_parser
    parser = build_parser()
    cmds = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert "preflight" in cmds
    assert "plan" in cmds
    assert "apply" in cmds
    assert "bundle" in cmds
    assert "download" in cmds


def test_cli_version(capsys):
    """--version flag prints version and exits."""
    import pytest
    from osx_proxmox_next import __version__
    with pytest.raises(SystemExit) as exc_info:
        run_cli(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_cli_plan_json(monkeypatch, capsys):
    """--json flag outputs plan as JSON array."""
    import json as json_mod
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, ""), AssetCheck("Rec", Path("/tmp/rec.iso"), True, "")],
    )
    rc = run_cli(_plan_args() + ["--json"])
    assert rc == 0
    captured = capsys.readouterr()
    data = json_mod.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) > 0
    assert "title" in data[0]
    assert "command" in data[0]
    assert "risk" in data[0]
    assert "step" in data[0]
    # JSON mode should NOT print CPU info
    assert "CPU:" not in captured.out


def test_cli_plan_prints_cpu_info(monkeypatch, capsys):
    """Non-JSON plan prints CPU info line."""
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, ""), AssetCheck("Rec", Path("/tmp/rec.iso"), True, "")],
    )
    rc = run_cli(_plan_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "CPU:" in captured.out


def test_cli_plan_cpu_override_display(monkeypatch, capsys):
    """CPU model override is shown in plan output."""
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, ""), AssetCheck("Rec", Path("/tmp/rec.iso"), True, "")],
    )
    rc = run_cli(_plan_args() + ["--cpu-model", "Skylake-Server-IBRS"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "override" in captured.out


def test_cli_plan_net_model_e1000(monkeypatch, capsys):
    """--net-model e1000-82545em flows into qm create command."""
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, ""), AssetCheck("Rec", Path("/tmp/rec.iso"), True, "")],
    )
    rc = run_cli(_plan_args() + ["--net-model", "e1000-82545em"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "e1000-82545em" in captured.out


def test_cli_plan_net_model_default_vmxnet3(monkeypatch, capsys):
    """Omitting --net-model defaults to vmxnet3 in plan output."""
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, ""), AssetCheck("Rec", Path("/tmp/rec.iso"), True, "")],
    )
    rc = run_cli(_plan_args())
    assert rc == 0
    captured = capsys.readouterr()
    assert "vmxnet3" in captured.out


def test_cli_preflight(monkeypatch):
    from osx_proxmox_next.preflight import PreflightCheck
    monkeypatch.setattr(
        cli_module, "run_preflight",
        lambda: [PreflightCheck("qm available", True, "/usr/sbin/qm")],
    )
    rc = run_cli(["preflight"])
    assert rc == 0


def test_cli_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "export_log_bundle", lambda: tmp_path / "bundle.tar.gz")
    rc = run_cli(["bundle"])
    assert rc == 0


def test_cli_preflight_reruns_after_install(monkeypatch):
    from osx_proxmox_next.preflight import PreflightCheck
    calls = []

    def fake_run_preflight():
        calls.append(1)
        return [PreflightCheck("dmg2img", len(calls) > 1, "/usr/bin/dmg2img")]

    monkeypatch.setattr(cli_module, "run_preflight", fake_run_preflight)
    monkeypatch.setattr(cli_module, "has_missing_build_deps", lambda checks: len(calls) == 1)
    monkeypatch.setattr(cli_module, "install_missing_packages", lambda on_output=None: (True, ["dmg2img"]))
    rc = run_cli(["preflight"])
    assert rc == 0
    assert len(calls) == 2


def test_cli_guide():
    rc = run_cli(["guide", "boot issue"])
    assert rc == 0


def _plan_args():
    return [
        "plan",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
    ]


def test_cli_plan(monkeypatch, tmp_path):
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, ""), AssetCheck("Rec", Path("/tmp/rec.iso"), True, "")],
    )
    rc = run_cli(_plan_args())
    assert rc == 0


def test_cli_plan_script_out(monkeypatch, tmp_path):
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, ""), AssetCheck("Rec", Path("/tmp/rec.iso"), True, "")],
    )
    out_file = tmp_path / "script.sh"
    rc = run_cli(_plan_args() + ["--script-out", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    assert "#!/usr/bin/env bash" in out_file.read_text()


def test_cli_apply_validation_fail():
    rc = run_cli([
        "apply",
        "--vmid", "5",
        "--name", "x",
        "--macos", "unknown",
        "--cores", "1",
        "--memory", "100",
        "--disk", "10",
        "--bridge", "br0",
        "--storage", "",
    ])
    assert rc == 2


def test_cli_apply_missing_assets(monkeypatch):
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing")],
    )
    monkeypatch.setattr(
        cli_module, "suggested_fetch_commands",
        lambda cfg: ["# fetch oc"],
    )
    rc = run_cli([
        "apply",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
    ])
    assert rc == 3


def test_cli_apply_success(monkeypatch, tmp_path):
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, "")],
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli([
        "apply",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
    ])
    assert rc == 0


def test_cli_apply_failure(monkeypatch, tmp_path):
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, "")],
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=False, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli([
        "apply",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
    ])
    assert rc == 4


def test_config_from_args_smbios():
    from osx_proxmox_next.cli import build_parser, _config_from_args
    parser = build_parser()
    args = parser.parse_args([
        "apply",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
        "--smbios-serial", "SERIAL123456",
        "--smbios-uuid", "UUID-1234",
        "--smbios-mlb", "MLB12345678901234",
        "--smbios-rom", "AABBCCDDEE00",
        "--smbios-model", "MacPro7,1",
    ])
    config = _config_from_args(args)
    assert config.smbios_serial == "SERIAL123456"
    assert config.smbios_uuid == "UUID-1234"
    assert config.smbios_mlb == "MLB12345678901234"
    assert config.smbios_rom == "AABBCCDDEE00"
    assert config.smbios_model == "MacPro7,1"


def test_cli_no_smbios():
    from osx_proxmox_next.cli import build_parser, _config_from_args
    parser = build_parser()
    args = parser.parse_args([
        "apply",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
        "--no-smbios",
    ])
    config = _config_from_args(args)
    assert config.no_smbios is True


def test_cli_main_block(monkeypatch):
    """Cover the if __name__ == '__main__' block."""
    from osx_proxmox_next.preflight import PreflightCheck
    monkeypatch.setattr(
        cli_module, "run_preflight",
        lambda: [PreflightCheck("qm available", True, "/usr/sbin/qm")],
    )
    import runpy
    import sys
    monkeypatch.setattr(sys, "argv", ["osx-next-cli", "preflight"])
    try:
        runpy.run_module("osx_proxmox_next.cli", run_name="__main__")
    except SystemExit as e:
        assert e.code == 0


def test_cli_progress_with_total(capsys):
    from osx_proxmox_next.cli import _cli_progress
    from osx_proxmox_next.downloader import DownloadProgress
    p = DownloadProgress(downloaded=1048576, total=2097152, phase="opencore")
    _cli_progress(p)
    out = capsys.readouterr().out
    assert "opencore" in out
    assert "50%" in out
    assert "1.0" in out


def test_cli_progress_without_total(capsys):
    from osx_proxmox_next.cli import _cli_progress
    from osx_proxmox_next.downloader import DownloadProgress
    p = DownloadProgress(downloaded=1048576, total=0, phase="recovery")
    _cli_progress(p)
    out = capsys.readouterr().out
    assert "recovery" in out
    assert "1.0" in out


def test_auto_download_missing_opencore(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck
    import osx_proxmox_next.cli as _cli_mod

    downloaded = []

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        _cli_mod, "run_download_worker",
        lambda cfg, missing, on_progress: (downloaded.append("oc"), []),
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)
    assert "oc" in downloaded


def test_auto_download_missing_recovery(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck
    import osx_proxmox_next.cli as _cli_mod

    downloaded = []

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        _cli_mod, "run_download_worker",
        lambda cfg, missing, on_progress: (downloaded.append("rec"), []),
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)
    assert "rec" in downloaded


def test_auto_download_missing_opencore_error(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck
    import osx_proxmox_next.cli as _cli_mod

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        _cli_mod, "run_download_worker",
        lambda cfg, missing, on_progress: ["OpenCore: network error"],
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)  # Should not raise


def test_auto_download_missing_recovery_error(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck
    import osx_proxmox_next.cli as _cli_mod

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        _cli_mod, "run_download_worker",
        lambda cfg, missing, on_progress: ["Recovery: network error"],
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)  # Should not raise


def test_auto_download_missing_unknown_asset_type(monkeypatch, tmp_path):
    """Asset with unknown name is silently skipped (run_download_worker handles routing)."""
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck
    import osx_proxmox_next.cli as _cli_mod

    called = []
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("Unknown Asset", Path("/tmp/unknown"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        _cli_mod, "run_download_worker",
        lambda cfg, missing, on_progress: (called.append(True), []),
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)  # Should not raise, just skip


def test_auto_download_missing_nothing_downloadable(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, "")],
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)  # No-op, nothing missing


def test_cli_download_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli_module, "download_opencore",
        lambda macos, dest, on_progress=None: tmp_path / f"opencore-{macos}.iso",
    )
    monkeypatch.setattr(
        cli_module, "download_recovery",
        lambda macos, dest, on_progress=None: tmp_path / f"{macos}-recovery.img",
    )
    rc = run_cli(["download", "--macos", "sequoia", "--dest", str(tmp_path)])
    assert rc == 0


def test_cli_download_opencore_only(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli_module, "download_opencore",
        lambda macos, dest, on_progress=None: tmp_path / f"opencore-{macos}.iso",
    )
    rc = run_cli(["download", "--macos", "sequoia", "--dest", str(tmp_path), "--opencore-only"])
    assert rc == 0


def test_cli_download_recovery_only(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli_module, "download_recovery",
        lambda macos, dest, on_progress=None: tmp_path / f"{macos}-recovery.img",
    )
    rc = run_cli(["download", "--macos", "sequoia", "--dest", str(tmp_path), "--recovery-only"])
    assert rc == 0


def test_cli_download_failure(monkeypatch, tmp_path):
    from osx_proxmox_next.downloader import DownloadError
    monkeypatch.setattr(
        cli_module, "download_opencore",
        lambda macos, dest, on_progress=None: (_ for _ in ()).throw(DownloadError("fail")),
    )
    monkeypatch.setattr(
        cli_module, "download_recovery",
        lambda macos, dest, on_progress=None: (_ for _ in ()).throw(DownloadError("fail")),
    )
    rc = run_cli(["download", "--macos", "sequoia", "--dest", str(tmp_path)])
    assert rc == 5


def test_cli_apply_no_download_flag(monkeypatch):
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        cli_module, "suggested_fetch_commands",
        lambda cfg: ["# fetch oc"],
    )
    rc = run_cli([
        "apply",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
        "--no-download",
    ])
    assert rc == 3


def test_cli_download_both_exclusive_flags(monkeypatch, tmp_path):
    """Passing both --opencore-only and --recovery-only results in no downloads."""
    oc_called = []
    rec_called = []
    monkeypatch.setattr(
        cli_module, "download_opencore",
        lambda macos, dest, on_progress=None: oc_called.append(1),
    )
    monkeypatch.setattr(
        cli_module, "download_recovery",
        lambda macos, dest, on_progress=None: rec_called.append(1),
    )
    rc = run_cli(["download", "--macos", "sequoia", "--dest", str(tmp_path),
                  "--opencore-only", "--recovery-only"])
    assert rc == 0
    assert oc_called == []
    assert rec_called == []


def test_cli_auto_download_on_missing(monkeypatch, tmp_path):
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.rollback import RollbackSnapshot

    call_count = [0]

    def fake_required_assets(cfg):
        call_count[0] += 1
        if call_count[0] == 1:
            return [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing", downloadable=True)]
        return [AssetCheck("OC", Path("/tmp/oc.iso"), True, "")]

    monkeypatch.setattr(cli_module, "required_assets", fake_required_assets)
    monkeypatch.setattr(cli_module, "_auto_download_missing", lambda cfg, dest: None)
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli([
        "apply",
        "--vmid", "900",
        "--name", "macos-sequoia",
        "--macos", "sequoia",
        "--cores", "8",
        "--memory", "16384",
        "--disk", "128",
        "--bridge", "vmbr0",
        "--storage", "local-lvm",
    ])
    assert rc == 0


# ── Status Tests ────────────────────────────────────────────────────


def test_cli_parser_has_status_command() -> None:
    from osx_proxmox_next.cli import build_parser
    parser = build_parser()
    cmds = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert "status" in cmds


def test_cli_status_invalid_vmid():
    rc = run_cli(["status", "--vmid", "5"])
    assert rc == 2


def test_cli_status_vm_not_found(monkeypatch):
    monkeypatch.setattr(cli_module, "fetch_vm_info", lambda vmid, adapter=None: None)
    rc = run_cli(["status", "--vmid", "106"])
    assert rc == 2


def test_cli_status_success(monkeypatch, capsys):
    from osx_proxmox_next.planner import VmInfo
    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(
            vmid=vmid, name="macos-sonoma", status="running",
            config_raw="cores: 8\nmemory: 16384\nballoon: 0\nnet0: vmxnet3=AA:BB:CC:DD:EE:FF\ncpu: host\nmachine: q35\nide0: local:iso/opencore.iso\n",
        ),
    )
    rc = run_cli(["status", "--vmid", "106"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "macos-sonoma" in captured.out
    assert "running" in captured.out
    assert "cores: 8" in captured.out
    assert "memory: 16384" in captured.out
    assert "balloon: 0" in captured.out
    # ide0 should NOT be printed (not in the filter list)
    assert "ide0" not in captured.out


def test_cli_status_no_config(monkeypatch, capsys):
    from osx_proxmox_next.planner import VmInfo
    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="test-vm", status="stopped", config_raw=""),
    )
    rc = run_cli(["status", "--vmid", "200"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "test-vm" in captured.out
    assert "stopped" in captured.out


# ── Uninstall Tests ─────────────────────────────────────────────────


def test_cli_parser_has_uninstall_command() -> None:
    from osx_proxmox_next.cli import build_parser
    parser = build_parser()
    cmds = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert "uninstall" in cmds


def test_cli_uninstall_dry_run():
    rc = run_cli(["uninstall", "--vmid", "106"])
    assert rc == 0


def test_cli_uninstall_dry_run_with_purge():
    rc = run_cli(["uninstall", "--vmid", "106", "--purge"])
    assert rc == 0


def test_cli_uninstall_invalid_vmid():
    rc = run_cli(["uninstall", "--vmid", "5"])
    assert rc == 2


def test_cli_uninstall_invalid_vmid_high():
    rc = run_cli(["uninstall", "--vmid", "9999999"])
    assert rc == 2


def test_cli_uninstall_vm_not_found(monkeypatch):
    monkeypatch.setattr(cli_module, "fetch_vm_info", lambda vmid, adapter=None: None)
    rc = run_cli(["uninstall", "--vmid", "106", "--execute"])
    assert rc == 2


def test_cli_uninstall_execute_success(monkeypatch, tmp_path):
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="macos-test", status="running", config_raw="cores: 8"),
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["uninstall", "--vmid", "106", "--execute"])
    assert rc == 0


def test_cli_uninstall_execute_failure(monkeypatch, tmp_path):
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="macos-test", status="stopped", config_raw=""),
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=False, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["uninstall", "--vmid", "106", "--execute"])
    assert rc == 6


def test_cli_uninstall_execute_with_purge(monkeypatch, tmp_path):
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo
    from osx_proxmox_next.rollback import RollbackSnapshot

    captured_steps = []

    def fake_apply(steps, execute=False):
        captured_steps.extend(steps)
        return ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt")

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="macos-test", status="stopped", config_raw=""),
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(cli_module, "apply_plan", fake_apply)
    rc = run_cli(["uninstall", "--vmid", "106", "--purge", "--execute"])
    assert rc == 0
    assert any("--purge" in step.command for step in captured_steps)


def test_cli_uninstall_vm_info_displayed(monkeypatch, tmp_path, capsys):
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="my-macos", status="running", config_raw="cores: 8"),
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    run_cli(["uninstall", "--vmid", "106", "--execute"])
    captured = capsys.readouterr()
    assert "my-macos" in captured.out
    assert "running" in captured.out


# ── edit command ──────────────────────────────────────────────────────


def test_cli_edit_parser_registered() -> None:
    from osx_proxmox_next.cli import build_parser
    parser = build_parser()
    cmds = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert "edit" in cmds


def test_cli_edit_invalid_vmid() -> None:
    rc = run_cli(["edit", "--vmid", "5", "--cores", "4"])
    assert rc == 2


def test_cli_edit_no_changes() -> None:
    rc = run_cli(["edit", "--vmid", "900"])
    assert rc == 2


def test_cli_edit_dry_run(capsys) -> None:
    rc = run_cli(["edit", "--vmid", "900", "--cores", "4"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry run" in out.lower() or "qm stop" in out


def test_cli_edit_dry_run_shows_steps(capsys) -> None:
    rc = run_cli(["edit", "--vmid", "900", "--name", "my-vm", "--memory", "8192"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "qm stop" in out
    assert "my-vm" in out
    assert "8192" in out


def test_cli_edit_invalid_bridge() -> None:
    rc = run_cli(["edit", "--vmid", "900", "--bridge", "eth0"])
    assert rc == 2


def test_cli_edit_execute_success(monkeypatch, tmp_path) -> None:
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="macos-test", status="running", config_raw="cores: 4"),
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["edit", "--vmid", "900", "--cores", "4", "--execute"])
    assert rc == 0


def test_cli_edit_execute_failure(monkeypatch, tmp_path) -> None:
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="macos-test", status="stopped", config_raw=""),
    )
    monkeypatch.setattr(
        cli_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=tmp_path / "snap.conf"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=False, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["edit", "--vmid", "900", "--memory", "8192", "--execute"])
    assert rc == 7


def test_cli_edit_start_flag(capsys) -> None:
    rc = run_cli(["edit", "--vmid", "900", "--cores", "4", "--start"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "qm start" in out


def test_cli_edit_add_disk(capsys) -> None:
    rc = run_cli(["edit", "--vmid", "900", "--add-disk", "64"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "+64G" in out


def test_cli_edit_bridge_update(capsys) -> None:
    rc = run_cli(["edit", "--vmid", "900", "--bridge", "vmbr1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "vmbr1" in out


# ── clone command ─────────────────────────────────────────────────────


def test_cli_clone_parser_registered() -> None:
    from osx_proxmox_next.cli import build_parser
    parser = build_parser()
    cmds = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    assert "clone" in cmds


def test_cli_clone_dry_run(capsys) -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "qm clone" in out
    assert "--smbios1" in out


def test_cli_clone_dry_run_shows_vmgenid(capsys) -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "vmgenid" in out


def test_cli_clone_dry_run_no_apple_services(capsys) -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--no-apple-services"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "vmgenid" not in out


def test_cli_clone_invalid_source_vmid_low() -> None:
    rc = run_cli(["clone", "--source-vmid", "5", "--new-vmid", "901"])
    assert rc == 2


def test_cli_clone_invalid_source_vmid_high() -> None:
    rc = run_cli(["clone", "--source-vmid", "9999999", "--new-vmid", "901"])
    assert rc == 2


def test_cli_clone_invalid_dst_vmid() -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "5"])
    assert rc == 2


def test_cli_clone_same_vmid() -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "900"])
    assert rc == 2


def test_cli_clone_invalid_macos(capsys) -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--macos", "invalid"])
    assert rc == 2
    assert "ERROR" in capsys.readouterr().out


def test_cli_clone_invalid_name_too_short(capsys) -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--name", "ab"])
    assert rc == 2
    assert "ERROR" in capsys.readouterr().out


def test_cli_clone_invalid_name_bad_chars(capsys) -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--name", "has spaces!"])
    assert rc == 2
    assert "ERROR" in capsys.readouterr().out


def test_cli_clone_with_name(capsys) -> None:
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--name", "my-clone"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "my-clone" in out


def test_cli_clone_vm_not_found(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "fetch_vm_info", lambda vmid, adapter=None: None)
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--execute"])
    assert rc == 2


def test_cli_clone_execute_success(monkeypatch, tmp_path, capsys) -> None:
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="macos-src", status="running",
                                           config_raw="net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0"),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--execute"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Clone OK" in out
    assert "901" in out


def test_cli_clone_execute_failure(monkeypatch, tmp_path) -> None:
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="macos-src", status="stopped", config_raw=""),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=False, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--execute"])
    assert rc == 8


def test_cli_clone_preserves_bridge_from_source(monkeypatch, tmp_path, capsys) -> None:
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo

    captured_steps = []

    def fake_apply(steps, execute=False):
        captured_steps.extend(steps)
        return ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt")

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="src", status="stopped",
                                           config_raw="net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr5,firewall=0"),
    )
    monkeypatch.setattr(cli_module, "apply_plan", fake_apply)
    run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--execute"])
    mac_step = next((s for s in captured_steps if "--net0" in s.argv), None)
    assert mac_step is not None
    assert "vmbr5" in mac_step.argv[-1]


# ---------------------------------------------------------------------------
# doctor command
# ---------------------------------------------------------------------------

def _make_doctor_checks(failures=0, warnings=0):
    from osx_proxmox_next.doctor import DoctorCheck, Severity
    checks = []
    for _ in range(failures):
        checks.append(DoctorCheck("balloon", Severity.FAIL, "balloon=1", fix="qm set 100 --balloon 0"))
    for _ in range(warnings):
        checks.append(DoctorCheck("agent", Severity.WARN, "agent not enabled"))
    if not checks:
        checks.append(DoctorCheck("balloon", Severity.OK, "balloon=0"))
    return checks


def test_cli_doctor_invalid_vmid_low(capsys) -> None:
    rc = run_cli(["doctor", "--vmid", "0"])
    assert rc == 2
    assert "ERROR" in capsys.readouterr().out


def test_cli_doctor_invalid_vmid_high(capsys) -> None:
    rc = run_cli(["doctor", "--vmid", "999999999"])
    assert rc == 2


def test_cli_doctor_all_ok(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "run_doctor", lambda vmid: _make_doctor_checks())
    rc = run_cli(["doctor", "--vmid", "100"])
    assert rc == 0
    assert "All checks passed" in capsys.readouterr().out


def test_cli_doctor_with_failures(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "run_doctor", lambda vmid: _make_doctor_checks(failures=1))
    rc = run_cli(["doctor", "--vmid", "100"])
    assert rc == 4
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "Fix:" in out


def test_cli_doctor_warnings_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "run_doctor", lambda vmid: _make_doctor_checks(warnings=2))
    rc = run_cli(["doctor", "--vmid", "100"])
    assert rc == 1
    assert "warning" in capsys.readouterr().out


def test_cli_doctor_mixed_failures_and_warnings(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_module, "run_doctor", lambda vmid: _make_doctor_checks(failures=2, warnings=1))
    rc = run_cli(["doctor", "--vmid", "100"])
    assert rc == 4
    out = capsys.readouterr().out
    assert "failure" in out
    assert "warning" in out


# ---------------------------------------------------------------------------
# clone — apple_services message branch
# ---------------------------------------------------------------------------

def test_cli_clone_execute_success_prints_apple_services(monkeypatch, tmp_path, capsys) -> None:
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="src", status="stopped", config_raw=""),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--execute"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Apple services" in out


def test_cli_clone_execute_no_apple_services_no_message(monkeypatch, tmp_path, capsys) -> None:
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid, adapter=None: VmInfo(vmid=vmid, name="src", status="stopped", config_raw=""),
    )
    monkeypatch.setattr(
        cli_module, "apply_plan",
        lambda steps, execute=False: ApplyResult(ok=True, results=[], log_path=tmp_path / "log.txt"),
    )
    rc = run_cli(["clone", "--source-vmid", "900", "--new-vmid", "901", "--execute", "--no-apple-services"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Apple services" not in out
