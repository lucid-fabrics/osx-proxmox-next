from __future__ import annotations

import asyncio
import importlib.util
import io
import plistlib
import zipfile
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Input, Select, Static

from nullmoth_fakes import GUID, MAC_CFG, FakeQm, good_card, oc_config, write_oc
from osx_proxmox_next import cli, nullmoth
from osx_proxmox_next import oc_builder
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.domain import PlanStep
from osx_proxmox_next.executor import ApplyResult
from osx_proxmox_next.gpu import GpuInfo

REPO = Path(__file__).resolve().parent.parent
PATCHER = REPO / "src/osx_proxmox_next/data/nullmoth/oc_patch.py"
ZIP = Path("/cache/AMFIPass-v1.4.1-RELEASE.zip")


@pytest.fixture(scope="module")
def patch():
    spec = importlib.util.spec_from_file_location("oc_patch", PATCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AMFIPass.kext/Contents/Info.plist", "x")
        z.writestr("AMFIPass.kext/Contents/MacOS/AMFIPass", "bin")
    return path


# ── OpenCore patcher ─────────────────────────────────────────────────

def test_attach_patches_config_and_detach_restores_exactly(patch, tmp_path, monkeypatch):
    oc = tmp_path / "OC"
    original = write_oc(oc)
    monkeypatch.setenv("AMFIPASS_ZIP", str(make_zip(tmp_path / "a.zip")))
    patch.attach(str(oc))
    cfg = plistlib.loads((oc / "config.plist").read_bytes())
    assert [k["BundlePath"] for k in cfg["Kernel"]["Add"]] == ["Lilu.kext", "AMFIPass.kext", "VirtualSMC.kext"]
    block = [b for b in cfg["Kernel"]["Block"] if b["Identifier"] == patch.IONDRV]
    assert len(block) == 1 and block[0]["Strategy"] == "Exclude" and block[0]["Enabled"] is True
    args = cfg["NVRAM"]["Add"][GUID]["boot-args"].split()
    assert "nv_disable=1" not in args and len(args) == len(set(args))
    assert args[:3] == ["keepsyms=1", "debug=0x100", "revblock=pci"]
    assert {"nvfb=1", "nvaccel=1", "nvfbheads=4", "-nvkmsnosmooth", "amfi=0x80", "gfxrst=4",
            "amfi_get_out_of_my_way=0x1", "ipc_control_port_options=0"} <= set(args)
    assert cfg["PlatformInfo"]["Generic"]["SystemProductName"] == "MacPro7,1"
    assert (oc / "Kexts/AMFIPass.kext/Contents/MacOS/AMFIPass").exists()
    assert (oc / patch.BACKUP).read_bytes() == original
    assert b"<array/>" not in (oc / "config.plist").read_bytes()
    patch.detach(str(oc))
    assert (oc / "config.plist").read_bytes() == original
    assert not (oc / patch.BACKUP).exists() and not (oc / "Kexts/AMFIPass.kext").exists()


def test_attach_twice_is_idempotent_and_keeps_pristine_backup(patch, tmp_path, monkeypatch):
    oc = tmp_path / "OC"
    original = write_oc(oc, boot_args="keepsyms=1 -wegnoegpu amfi=0x1")
    monkeypatch.setenv("AMFIPASS_ZIP", str(make_zip(tmp_path / "a.zip")))
    patch.attach(str(oc))
    first = (oc / "config.plist").read_bytes()
    patch.attach(str(oc))
    assert (oc / "config.plist").read_bytes() == first
    assert (oc / patch.BACKUP).read_bytes() == original
    args = plistlib.loads(first)["NVRAM"]["Add"][GUID]["boot-args"].split()
    assert "-wegnoegpu" not in args and args.count("amfi=0x80") == 1 and "amfi=0x1" not in args


def test_attach_without_lilu_inserts_first_and_keeps_macpro_model(patch, tmp_path, monkeypatch):
    oc = tmp_path / "OC"
    write_oc(oc, model="MacPro7,1")
    cfg = plistlib.loads((oc / "config.plist").read_bytes())
    cfg["Kernel"]["Add"] = []
    (oc / "config.plist").write_bytes(plistlib.dumps(cfg))
    monkeypatch.setenv("AMFIPASS_ZIP", str(make_zip(tmp_path / "a.zip")))
    patch.attach(str(oc))
    out = plistlib.loads((oc / "config.plist").read_bytes())
    assert out["Kernel"]["Add"][0]["BundlePath"] == "AMFIPass.kext"


@pytest.mark.parametrize("kw,msg", [
    ({"csr": b"\x00\x00\x00\x00"}, "csr-active-config"),
    ({"sbm": "Default"}, "SecureBootModel"),
])
def test_attach_refuses_without_changing_anything(patch, tmp_path, monkeypatch, kw, msg, capsys):
    oc = tmp_path / "OC"
    original = write_oc(oc, **kw)
    monkeypatch.setenv("AMFIPASS_ZIP", str(make_zip(tmp_path / "a.zip")))
    with pytest.raises(SystemExit) as exc:
        patch.attach(str(oc))
    assert exc.value.code == 2 and msg in capsys.readouterr().err
    assert (oc / "config.plist").read_bytes() == original
    assert not (oc / patch.BACKUP).exists() and not (oc / "Kexts/AMFIPass.kext").exists()


def test_attach_needs_a_kext_source(patch, tmp_path, monkeypatch):
    oc = tmp_path / "OC"
    write_oc(oc)
    monkeypatch.delenv("AMFIPASS_ZIP", raising=False)
    with pytest.raises(SystemExit):
        patch.attach(str(oc))
    (oc / "Kexts/AMFIPass.kext").mkdir()
    monkeypatch.setenv("AMFIPASS_ZIP", "")
    patch.attach(str(oc))  # kext already on disk, no zip needed


def test_zip_member_checks(patch, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("AMFIPass.kext/../evil", "x")
    with pytest.raises(SystemExit):
        patch.unpack_kext(str(bad), str(tmp_path))
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("Other.kext/a", "x")
    with pytest.raises(SystemExit):
        patch.unpack_kext(str(empty), str(tmp_path))


def test_refused_zip_keeps_existing_kext(patch, tmp_path):
    oc = tmp_path / "OC"
    (oc / "Kexts/AMFIPass.kext").mkdir(parents=True)
    (oc / "Kexts/AMFIPass.kext/keep").write_text("x")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("Other.kext/a", "x")
    with pytest.raises(SystemExit):
        patch.unpack_kext(str(empty), str(oc))
    assert (oc / "Kexts/AMFIPass.kext/keep").exists()


def test_refused_zip_keeps_existing_kext(patch, tmp_path):
    oc = tmp_path / "OC"
    (oc / "Kexts/AMFIPass.kext").mkdir(parents=True)
    (oc / "Kexts/AMFIPass.kext/keep").write_text("x")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("Other.kext/a", "x")
    with pytest.raises(SystemExit):
        patch.unpack_kext(str(empty), str(oc))
    assert (oc / "Kexts/AMFIPass.kext/keep").exists()


def test_detach_without_backup_and_main_usage(patch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        patch.detach(str(tmp_path))
    assert exc.value.code == 3
    with pytest.raises(SystemExit) as exc:
        patch.main(["x"])
    assert exc.value.code == 64


def test_main_dispatch(patch, tmp_path, monkeypatch):
    oc = tmp_path / "OC"
    write_oc(oc)
    monkeypatch.setenv("AMFIPASS_ZIP", str(make_zip(tmp_path / "a.zip")))
    patch.main(["p", str(oc), "attach"])
    patch.main(["p", str(oc), "detach"])


# ── helpers ──────────────────────────────────────────────────────────

def test_notice_copy_is_verbatim():
    text = nullmoth.notice_text()
    assert text.startswith("NVIDIA GPU support (Beta)\n\nThis feature prepares your VM")
    assert text.endswith("Driver and docs: https://github.com/nullmoth/nvidia-macos-driver")
    assert chr(0x2014) not in text
    assert nullmoth.ACK_LABEL.endswith("not responsible for the NullMoth driver.")


@pytest.mark.parametrize("cfg,want", [
    ({"description": "%3Ch2%3EmacOS%20Sequoia%2015%20VM%3C%2Fh2%3E"}, "sequoia"),
    ({"name": "macos-tahoe"}, "tahoe"),
    ({"description": "Ventura", "name": "macos-tahoe"}, "ventura"),
    ({"name": "mac"}, None),
])
def test_detect_macos(cfg, want):
    assert nullmoth.detect_macos(cfg) == want


def test_oc_patch_script_shape():
    script = nullmoth.oc_patch_script("100", "attach", ZIP)
    assert "AMFIPASS_ZIP=/cache/AMFIPass-v1.4.1-RELEASE.zip AMFIPASS_SHA256=" in script and " python3 -" in script
    assert script.rstrip().endswith("NULLMOTH_PY")
    assert "ide0" in script and "attach <<'NULLMOTH_PY' && sync" in script
    assert "AMFIPASS_ZIP=" not in nullmoth.oc_patch_script("100", "detach")


# ── preflight / plans ────────────────────────────────────────────────

def prep(tmp_path, vms=None, gpu="01:00", macos=None, allow_stop=False, card=True, **kw):
    if card:
        good_card(tmp_path)
    vms = vms if vms is not None else {100: ("stopped", MAC_CFG)}
    return nullmoth.prepare_attach(100, gpu, ZIP, macos=macos, allow_stop=allow_stop,
                                   adapter=FakeQm(vms), sysfs_root=tmp_path,
                                   cmdline_path=tmp_path / "cmdline", **kw)


def test_attach_plan_happy_path(tmp_path):
    plan = prep(tmp_path)
    assert plan.ok and not plan.warnings
    titles = [s.title for s in plan.steps]
    assert titles[0] == "Verify VM is stopped" and titles[1].startswith("Record the original VM settings")
    assert titles[2].startswith("Prepare OpenCore")  # the record goes first, so an undo always finds it
    assert plan.steps[3].argv == ["qm", "set", "100", "--hostpci0", "0000:01:00,pcie=0"]
    assert plan.steps[4].argv[-1].endswith("pci-hole64-size=64G")


def test_attach_sequoia_warns_and_explicit_macos_wins(tmp_path):
    cfg = MAC_CFG.replace("macos-tahoe", "plain")
    plan = prep(tmp_path, vms={100: ("stopped", cfg)}, macos="Sequoia")
    assert plan.ok and "15.5" in plan.warnings[0]


@pytest.mark.parametrize("vms,kw,needle", [
    ({}, {}, "not found"),
    ({100: ("running", MAC_CFG)}, {}, "qm stop 100"),
    ({100: ("stopped", "name: x\nargs: -cpu host\n")}, {}, "not a macOS VM"),
    ({100: ("stopped", MAC_CFG.replace("tahoe", "sonoma"))}, {}, "15.5 Sequoia or newer"),
    ({100: ("stopped", MAC_CFG.replace("macos-tahoe", "vm"))}, {}, "Cannot tell which macOS"),
    ({100: ("stopped", MAC_CFG + "hostpci0: 0000:09:00,pcie=0\n")}, {}, "hostpci0 is already in use"),
    ({100: ("stopped", MAC_CFG), 101: ("running", "hostpci0: host=01:00.0,pcie=1\n")}, {}, "running VM 101"),
])
def test_attach_refusals(tmp_path, vms, kw, needle):
    plan = prep(tmp_path, vms=vms, **kw)
    assert not plan.ok and not plan.steps
    assert any(needle in i for i in plan.issues)


def test_attach_refuses_unbound_card_with_hint_and_bad_bdf(tmp_path):
    good_card(tmp_path, driver=None)
    plan = prep(tmp_path, card=False)
    assert not plan.ok and any("vfio.conf" in h for h in plan.hints)
    assert not prep(tmp_path, gpu="zz", card=False).ok


def test_attach_stopped_other_user_warns_and_same_card_reattach_ok(tmp_path):
    vms = {100: ("stopped", MAC_CFG + "hostpci0: 0000:01:00,pcie=0\n"),
           101: ("stopped", "hostpci1: 01:00.0\n")}
    plan = prep(tmp_path, vms=vms)
    assert plan.ok and "VM 101 also has" in plan.warnings[0]


def test_attach_allow_stop_adds_stop_step(tmp_path):
    plan = prep(tmp_path, vms={100: ("running", MAC_CFG)}, allow_stop=True)
    assert plan.ok and plan.steps[0].title == "Stop VM"


def test_attach_skips_mmio_step_when_present(tmp_path):
    cfg = MAC_CFG.replace("-cpu host\n", "-cpu host " + nullmoth.MMIO_ARG + "\n")
    assert len(prep(tmp_path, vms={100: ("stopped", cfg)}).steps) == 4


def test_detach_plan(tmp_path):
    cfg = MAC_CFG.replace("-cpu host\n", "-cpu host " + nullmoth.MMIO_ARG + "\n") + "hostpci0: 0000:01:00,pcie=0\n"
    plan = nullmoth.prepare_detach(100, adapter=FakeQm({100: ("stopped", cfg)}))
    assert [s.title for s in plan.steps] == [
        "Verify VM is stopped", "Restore the previous OpenCore config",
        "Remove GPU (hostpci0)", "Remove the profile's QEMU args"]
    assert not nullmoth.prepare_detach(100, adapter=FakeQm({})).ok
    assert not nullmoth.prepare_detach(100, adapter=FakeQm({100: ("running", cfg)})).ok
    stop = nullmoth.prepare_detach(100, adapter=FakeQm({100: ("running", cfg)}), allow_stop=True)
    assert stop.steps[0].title == "Stop VM"


# ── oc_builder pin ───────────────────────────────────────────────────

def test_amfipass_pin():
    comp = oc_builder.AMFIPASS
    assert len(comp.sha256) == 64 and comp.filename == "AMFIPass-v1.4.1-RELEASE.zip"
    assert "76516394fddb7ce4526ccbcc5b806795c30e9a89" in comp.url
    assert "AMFIPass" not in oc_builder.OC_COMPONENTS  # base image unchanged


def test_ensure_amfipass_verifies(tmp_path, monkeypatch):
    data = b"zipdata"
    import hashlib
    comp = oc_builder.Component("1", "https://x/AMFIPass-v1.zip", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(oc_builder, "AMFIPASS", comp)
    monkeypatch.setattr(oc_builder, "_download_file", lambda u, d, p, ph: d.write_bytes(data))
    assert oc_builder.ensure_amfipass(tmp_path).read_bytes() == data
    monkeypatch.setattr(oc_builder, "_download_file", lambda u, d, p, ph: d.write_bytes(b"evil"))
    (tmp_path / "AMFIPass-v1.zip").unlink()
    with pytest.raises(oc_builder.ChecksumError):
        oc_builder.ensure_amfipass(tmp_path)


# ── CLI ──────────────────────────────────────────────────────────────

def patch_cli(monkeypatch, tmp_path, vms=None, results=None):
    good_card(tmp_path)
    fake = FakeQm(vms or {100: ("stopped", MAC_CFG)})
    monkeypatch.setattr("osx_proxmox_next.services.proxmox_service._pve", fake)
    real = nullmoth.check_nvidia_gpu
    monkeypatch.setattr(nullmoth, "check_nvidia_gpu", lambda b, r=None, c=None: real(b, tmp_path, tmp_path / "cmd"))
    applied = []

    def fake_apply(steps, execute=False, **kw):
        applied.append(steps)
        return results or ApplyResult(True, [], tmp_path / "log")
    monkeypatch.setattr(cli, "apply_plan", fake_apply)
    monkeypatch.setattr(cli, "create_snapshot", lambda v: type("S", (), {"path": "snap"})())
    monkeypatch.setattr(cli, "ensure_amfipass", lambda d: ZIP)
    return applied


def test_cli_parsing():
    args = cli.build_parser().parse_args(["nvidia-attach", "120", "--gpu", "01:00", "--accept-nullmoth-risk", "--dry-run"])
    assert (args.vmid, args.gpu, args.accept_risk, args.dry_run) == (120, "01:00", True, True)
    assert cli.build_parser().parse_args(["nvidia-detach", "5"]).dry_run is False


def test_cli_without_ack_changes_nothing(monkeypatch, tmp_path, capsys):
    applied = patch_cli(monkeypatch, tmp_path)
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00"]) == 2
    out = capsys.readouterr().out
    assert "NVIDIA GPU support (Beta)" in out and "Nothing was changed" in out and not applied


def test_cli_dry_run_needs_no_ack(monkeypatch, tmp_path, capsys):
    applied = patch_cli(monkeypatch, tmp_path)
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "--hostpci0" in out and not applied


def test_cli_dry_run_reports_refusal(monkeypatch, tmp_path, capsys):
    patch_cli(monkeypatch, tmp_path, vms={100: ("running", MAC_CFG)})
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--dry-run"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_cli_attach_applies(monkeypatch, tmp_path, capsys):
    applied = patch_cli(monkeypatch, tmp_path)
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk"]) == 0
    assert len(applied[0]) == 5 and "kmutil showloaded" in capsys.readouterr().out


def test_cli_attach_failure_and_refusal_and_download_error(monkeypatch, tmp_path, capsys):
    from osx_proxmox_next.downloader import DownloadError
    from osx_proxmox_next.executor import StepResult
    bad = ApplyResult(False, [StepResult("Prepare", "c", False, 1, "boom\nline")], tmp_path / "log")
    patch_cli(monkeypatch, tmp_path, results=bad)
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk"]) == 10
    assert "FAILED at step: Prepare" in capsys.readouterr().out
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "09:00", "--accept-nullmoth-risk"]) == 2
    monkeypatch.setattr(cli, "ensure_amfipass", lambda d: (_ for _ in ()).throw(DownloadError("x")))
    assert cli.run_cli(["nvidia-attach", "100", "--gpu", "01:00", "--accept-nullmoth-risk"]) == 5
    assert cli.run_cli(["nvidia-attach", "5", "--gpu", "01:00"]) == 2


def test_cli_detach(monkeypatch, tmp_path, capsys):
    cfg = MAC_CFG + "hostpci0: 0000:01:00,pcie=0\n"
    applied = patch_cli(monkeypatch, tmp_path, vms={100: ("stopped", cfg)})
    assert cli.run_cli(["nvidia-detach", "100", "--dry-run"]) == 0
    assert cli.run_cli(["nvidia-detach", "100"]) == 0
    assert len(applied) == 1
    assert cli.run_cli(["nvidia-detach", "7"]) == 2
    assert cli.run_cli(["nvidia-detach", "999", "--dry-run"]) == 2
    assert cli.run_cli(["nvidia-detach", "999"]) == 2
