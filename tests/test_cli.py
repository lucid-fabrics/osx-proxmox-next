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


def test_cli_progress_with_total():
    from osx_proxmox_next.cli import _cli_progress
    from osx_proxmox_next.downloader import DownloadProgress
    p = DownloadProgress(downloaded=1048576, total=2097152, phase="opencore")
    _cli_progress(p)


def test_cli_progress_without_total():
    from osx_proxmox_next.cli import _cli_progress
    from osx_proxmox_next.downloader import DownloadProgress
    p = DownloadProgress(downloaded=1048576, total=0, phase="recovery")
    _cli_progress(p)


def test_auto_download_missing_opencore(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck

    downloaded = []

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        cli_module, "download_opencore",
        lambda macos, dest, on_progress=None: (downloaded.append("oc"), tmp_path / "oc.iso")[1],
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)
    assert "oc" in downloaded


def test_auto_download_missing_recovery(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck

    downloaded = []

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "missing", downloadable=True)],
    )
    monkeypatch.setattr(
        cli_module, "download_recovery",
        lambda macos, dest, on_progress=None: (downloaded.append("rec"), tmp_path / "rec.img")[1],
    )

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)
    assert "rec" in downloaded


def test_auto_download_missing_opencore_error(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadError

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )

    def fail_download(macos, dest, on_progress=None):
        raise DownloadError("network error")

    monkeypatch.setattr(cli_module, "download_opencore", fail_download)

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)  # Should not raise


def test_auto_download_missing_recovery_error(monkeypatch, tmp_path):
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadError

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "missing", downloadable=True)],
    )

    def fail_download(macos, dest, on_progress=None):
        raise DownloadError("network error")

    monkeypatch.setattr(cli_module, "download_recovery", fail_download)

    from osx_proxmox_next.domain import VmConfig
    cfg = VmConfig(vmid=900, name="macos-sequoia", macos="sequoia", cores=8,
                   memory_mb=16384, disk_gb=128, bridge="vmbr0", storage="local-lvm")
    _auto_download_missing(cfg, tmp_path)  # Should not raise


def test_auto_download_missing_unknown_asset_type(monkeypatch, tmp_path):
    """Asset with unknown name is silently skipped."""
    from osx_proxmox_next.cli import _auto_download_missing
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        cli_module, "required_assets",
        lambda cfg: [AssetCheck("Unknown Asset", Path("/tmp/unknown"), False, "missing", downloadable=True)],
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
    monkeypatch.setattr(cli_module, "fetch_vm_info", lambda vmid: None)
    rc = run_cli(["uninstall", "--vmid", "106", "--execute"])
    assert rc == 2


def test_cli_uninstall_execute_success(monkeypatch, tmp_path):
    from osx_proxmox_next.executor import ApplyResult
    from osx_proxmox_next.planner import VmInfo
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        cli_module, "fetch_vm_info",
        lambda vmid: VmInfo(vmid=vmid, name="macos-test", status="running", config_raw="cores: 8"),
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
        lambda vmid: VmInfo(vmid=vmid, name="macos-test", status="stopped", config_raw=""),
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
        lambda vmid: VmInfo(vmid=vmid, name="macos-test", status="stopped", config_raw=""),
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
        lambda vmid: VmInfo(vmid=vmid, name="my-macos", status="running", config_raw="cores: 8"),
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
