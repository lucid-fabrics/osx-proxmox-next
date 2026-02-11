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
    """Cover the if __name__ == '__main__' block (line 131)."""
    from osx_proxmox_next.preflight import PreflightCheck
    monkeypatch.setattr(
        cli_module, "run_preflight",
        lambda: [PreflightCheck("qm available", True, "/usr/sbin/qm")],
    )
    # Simulate running cli module as __main__
    import runpy
    import sys
    monkeypatch.setattr(sys, "argv", ["osx-next-cli", "preflight"])
    try:
        runpy.run_module("osx_proxmox_next.cli", run_name="__main__")
    except SystemExit as e:
        assert e.code == 0
