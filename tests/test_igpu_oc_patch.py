"""data/gpu/oc_igpu.py: the OpenCore patcher for the iGPU, run the way the product runs it."""
from __future__ import annotations

import hashlib
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from nullmoth_fakes import GUID, oc_config
from osx_proxmox_next import igpu_engine

SCRIPT = Path(igpu_engine.__file__).parent / "data/gpu/oc_igpu.py"


def make_zip(path: Path, bundle: str, exe: str, extra: dict[str, bytes] | None = None) -> str:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{bundle}/Contents/Info.plist", "<plist/>")
        z.writestr(f"{bundle}/Contents/MacOS/{exe}", b"bin")
        z.writestr(f"{bundle}.dSYM/Contents/Info.plist", "ignored")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def oc(tmp_path):
    d = tmp_path / "OC"
    d.mkdir()
    cfg = oc_config(boot_args="keepsyms=1 -allow3d")
    cfg["Kernel"]["Add"].insert(1, {"BundlePath": "WhateverGreen.kext", "Enabled": True})
    cfg["ACPI"] = {"Quirks": {"FadtEnableReset": False}}
    cfg["Misc"]["Boot"] = {"Timeout": 15}
    cfg["Misc"]["Security"]["ScanPolicy"] = 0
    (d / "config.plist").write_bytes(plistlib.dumps(cfg))
    return d


@pytest.fixture
def zips(tmp_path):
    w, a = tmp_path / "w.zip", tmp_path / "a.zip"
    return {"IGPU_WEX_ZIP": str(w), "IGPU_WEX_SHA256": make_zip(w, "WhateverXe.kext", "WhateverXe"),
            "IGPU_AMFI_ZIP": str(a), "IGPU_AMFI_SHA256": make_zip(a, "AMFIPass.kext", "AMFIPass")}


def run(oc: Path, action: str, env: dict | None = None):
    return subprocess.run([sys.executable, str(SCRIPT), str(oc), action], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", **(env or {})})


def cfg_of(oc: Path) -> dict:
    return plistlib.loads((oc / "config.plist").read_bytes())


def test_attach_sets_everything_the_recipe_needs(oc, zips):
    original = (oc / "config.plist").read_bytes()
    assert run(oc, "attach", zips).returncode == 0
    cfg = cfg_of(oc)
    names = [k["BundlePath"] for k in cfg["Kernel"]["Add"]]
    assert names[:4] == ["Lilu.kext", "WhateverXe.kext", "AMFIPass.kext", "WhateverGreen.kext"]
    assert next(k for k in cfg["Kernel"]["Add"] if k["BundlePath"] == "WhateverGreen.kext")["Enabled"] is False
    assert cfg["ACPI"]["Quirks"]["FadtEnableReset"] is True
    args = cfg["NVRAM"]["Add"][GUID]["boot-args"].split()
    assert args.count("-allow3d") == 1 and "-disablegfxfirmware" in args and "-wex-tgl-hold-render-wake" in args
    assert (oc / "Kexts/WhateverXe.kext/Contents/MacOS/WhateverXe").exists()
    assert not (oc / "Kexts/WhateverXe.kext.dSYM").exists()
    assert (oc / "config.plist.pre-igpu").read_bytes() == original
    assert b"<array/>" not in (oc / "config.plist").read_bytes()


def test_attach_twice_changes_nothing_more(oc, zips):
    run(oc, "attach", zips)
    first = (oc / "config.plist").read_bytes()
    backup = (oc / "config.plist.pre-igpu").read_bytes()
    assert run(oc, "attach", zips).returncode == 0
    assert (oc / "config.plist").read_bytes() == first and (oc / "config.plist.pre-igpu").read_bytes() == backup


def test_restore_puts_the_original_back(oc, zips):
    original = (oc / "config.plist").read_bytes()
    run(oc, "attach", zips)
    assert run(oc, "restore").returncode == 0
    assert (oc / "config.plist").read_bytes() == original
    assert not (oc / "config.plist.pre-igpu").exists() and not (oc / "Kexts/WhateverXe.kext").exists()
    assert "nothing to restore" in run(oc, "restore").stderr  # repeatable


def test_restore_keeps_whatever_xe_while_the_graphics_package_is_installed(oc, zips):
    run(oc, "attach", zips)
    patched = (oc / "config.plist").read_bytes()
    assert run(oc, "mark").returncode == 0
    res = run(oc, "restore")
    assert res.returncode == 0 and res.stderr.startswith("KEPT:") and "hang at boot" in res.stderr
    assert (oc / "config.plist").read_bytes() == patched and (oc / "Kexts/WhateverXe.kext").exists()
    assert (oc / "config.plist.pre-igpu").exists()  # the backup stays for a later, safe restore


def test_restore_keeps_a_config_the_user_changed(oc, zips):
    run(oc, "attach", zips)
    cfg = cfg_of(oc)
    cfg["Misc"]["Boot"]["Timeout"] = 99
    (oc / "config.plist").write_bytes(plistlib.dumps(cfg))
    res = run(oc, "restore")
    assert res.returncode == 0 and "KEPT" in res.stderr
    assert cfg_of(oc)["Misc"]["Boot"]["Timeout"] == 99
    assert list(oc.glob("config.plist.pre-igpu.kept-*"))


@pytest.mark.parametrize("break_it,text", [
    (lambda z: {**z, "IGPU_WEX_SHA256": "0" * 64}, "checksum mismatch"),
    (lambda z: {k: v for k, v in z.items() if k != "IGPU_AMFI_ZIP"}, "zip is missing"),
])
def test_attach_refuses_bad_downloads_and_changes_nothing(oc, zips, break_it, text):
    original = (oc / "config.plist").read_bytes()
    res = run(oc, "attach", break_it(zips))
    assert res.returncode == 2 and text in res.stderr
    assert (oc / "config.plist").read_bytes() == original and not (oc / "config.plist.pre-igpu").exists()


def test_attach_refuses_a_zip_without_the_kext_and_unsafe_paths(oc, tmp_path, zips):
    empty = tmp_path / "e.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("other/file", "x")
    env = {**zips, "IGPU_WEX_ZIP": str(empty), "IGPU_WEX_SHA256": hashlib.sha256(empty.read_bytes()).hexdigest()}
    assert "no WhateverXe.kext" in run(oc, "attach", env).stderr
    evil = tmp_path / "v.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("WhateverXe.kext/../../x", "x")
    env["IGPU_WEX_ZIP"], env["IGPU_WEX_SHA256"] = str(evil), hashlib.sha256(evil.read_bytes()).hexdigest()
    assert "unsafe path" in run(oc, "attach", env).stderr


def test_attach_refuses_nullmoth_leftovers(oc, zips):
    cfg = cfg_of(oc)
    cfg["Kernel"]["Block"].append({"Identifier": "com.apple.iokit.IONDRVSupport", "Enabled": True})
    (oc / "config.plist").write_bytes(plistlib.dumps(cfg))
    res = run(oc, "attach", zips)
    assert res.returncode == 2 and "nvidia-detach" in res.stderr
    cfg = cfg_of(oc)
    cfg["Kernel"]["Block"].pop()
    cfg["NVRAM"]["Add"][GUID]["boot-args"] += " nvfb=1"
    (oc / "config.plist").write_bytes(plistlib.dumps(cfg))
    assert "NullMoth boot-args" in run(oc, "attach", zips).stderr


def test_attach_needs_lilu(oc, zips):
    cfg = cfg_of(oc)
    cfg["Kernel"]["Add"] = [k for k in cfg["Kernel"]["Add"] if k["BundlePath"] != "Lilu.kext"]
    (oc / "config.plist").write_bytes(plistlib.dumps(cfg))
    assert "no Lilu.kext" in run(oc, "attach", zips).stderr


def test_attach_replaces_an_existing_amfipass_entry_without_duplicating(oc, zips):
    cfg = cfg_of(oc)
    cfg["Kernel"]["Add"].append({"BundlePath": "AMFIPass.kext", "Enabled": True})
    (oc / "config.plist").write_bytes(plistlib.dumps(cfg))
    assert run(oc, "attach", zips).returncode == 0
    assert [k["BundlePath"] for k in cfg_of(oc)["Kernel"]["Add"]].count("AMFIPass.kext") == 1


def test_fadt_only_sets_the_quirk(oc):
    before = cfg_of(oc)
    assert run(oc, "fadt").returncode == 0
    after = cfg_of(oc)
    assert after["ACPI"]["Quirks"]["FadtEnableReset"] is True
    before["ACPI"]["Quirks"]["FadtEnableReset"] = True
    assert after == before and not (oc / "config.plist.pre-igpu").exists()


def test_stage_shows_recovery_only_and_unstage_puts_it_back(oc):
    run(oc, "stage")
    cfg = cfg_of(oc)
    assert cfg["Misc"]["Security"]["ScanPolicy"] == 0x201 and cfg["Misc"]["Boot"]["Timeout"] == 0
    run(oc, "stage")  # twice must not save 0x201 as the original
    assert run(oc, "unstage").returncode == 0
    assert cfg_of(oc)["Misc"]["Security"]["ScanPolicy"] == 0 and not (oc / "igpu-scanpolicy.orig").exists()


def test_unstage_restores_a_custom_policy_and_leaves_a_changed_one(oc):
    cfg = cfg_of(oc)
    cfg["Misc"]["Security"]["ScanPolicy"] = 0x10F00
    (oc / "config.plist").write_bytes(plistlib.dumps(cfg))
    run(oc, "stage")
    run(oc, "unstage")
    assert cfg_of(oc)["Misc"]["Security"]["ScanPolicy"] == 0x10F00
    run(oc, "unstage")  # nothing staged: untouched
    assert cfg_of(oc)["Misc"]["Security"]["ScanPolicy"] == 0x10F00


def test_usage(oc):
    assert run(oc, "bogus").returncode == 64
    assert subprocess.run([sys.executable, str(SCRIPT)], capture_output=True).returncode == 64
