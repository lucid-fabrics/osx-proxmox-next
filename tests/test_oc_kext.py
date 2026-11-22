"""data/gpu/oc_kext.py: read and replace kext bundles on a mounted OpenCore disk."""
from __future__ import annotations

import hashlib
import json
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "src/osx_proxmox_next/data/gpu/oc_kext.py"


def run(*args, expect=0):
    res = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)
    assert res.returncode == expect, (res.returncode, res.stdout, res.stderr)
    return res


def kext_files(name, version, exe=None, wrap=""):
    exe = exe or name
    plist = plistlib.dumps({"CFBundleShortVersionString": version, "CFBundleExecutable": exe})
    base = f"{wrap}{name}.kext/Contents/"
    return {base + "Info.plist": plist, base + "MacOS/" + exe: b"\x00bin" + version.encode()}


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path, hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install(oc, name, version, exe=None):
    for rel, data in kext_files(name, version, exe).items():
        out = oc / "Kexts" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)


def config(oc, entries):
    (oc / "config.plist").write_bytes(plistlib.dumps({"Kernel": {"Add": entries}}))


@pytest.fixture
def oc(tmp_path):
    d = tmp_path / "EFI" / "OC"
    d.mkdir(parents=True)
    return d


def test_versions_are_read_and_a_missing_kext_is_empty(oc):
    install(oc, "Lilu", "1.6.8")
    assert json.loads(run(oc, "versions", "Lilu.kext", "WhateverGreen.kext").stdout) == {"Lilu.kext": "1.6.8", "WhateverGreen.kext": ""}
    (oc / "Kexts" / "Lilu.kext" / "Contents" / "Info.plist").write_text("garbage")
    assert json.loads(run(oc, "versions", "Lilu.kext").stdout) == {"Lilu.kext": ""}


def test_a_path_is_not_a_kext_name(oc):
    assert "not a kext name" in run(oc, "versions", "../x.kext", expect=64).stderr
    assert "not a kext name" in run(oc, "replace", "x", "z", "s", expect=64).stderr


def test_replace_swaps_the_bundle_and_leaves_the_config_alone(oc, tmp_path):
    install(oc, "Lilu", "1.6.8")
    (oc / "Kexts" / "Lilu.kext" / "stale.txt").write_text("old")
    config(oc, [{"BundlePath": "Lilu.kext", "ExecutablePath": "Contents/MacOS/Lilu"}])
    before = (oc / "config.plist").read_bytes()
    zip_path, sha = make_zip(tmp_path / "Lilu.zip", {**kext_files("Lilu", "1.7.2"), "Docs/readme": b"x"})
    res = run(oc, "replace", "Lilu.kext", zip_path, sha)
    assert res.stdout.strip() == "1.7.2" and res.stderr == ""
    assert (oc / "config.plist").read_bytes() == before
    assert not (oc / "Kexts" / "Lilu.kext" / "stale.txt").exists() and not (oc / "Kexts" / "Lilu.kext.tmp").exists()
    assert json.loads(run(oc, "versions", "Lilu.kext").stdout) == {"Lilu.kext": "1.7.2"}


def test_a_wrapped_kext_is_found_and_a_changed_executable_is_corrected(oc, tmp_path):
    install(oc, "WhateverGreen", "1.6.0")
    config(oc, [{"BundlePath": "WhateverGreen.kext", "ExecutablePath": "Contents/MacOS/WhateverGreen"}])
    zip_path, sha = make_zip(tmp_path / "wg.zip", kext_files("WhateverGreen", "1.7.0", exe="WG", wrap="Release/"))
    res = run(oc, "replace", "WhateverGreen.kext", zip_path, sha)
    assert res.stdout.strip() == "1.7.0" and "ExecutablePath of WhateverGreen.kext in config.plist was corrected" in res.stderr
    entry = plistlib.loads((oc / "config.plist").read_bytes())["Kernel"]["Add"][0]
    assert entry["ExecutablePath"] == "Contents/MacOS/WG"
    assert b"<array/>" not in (oc / "config.plist").read_bytes()


def test_a_kext_the_config_does_not_list_is_reported_not_added(oc, tmp_path):
    config(oc, [])
    zip_path, sha = make_zip(tmp_path / "l.zip", kext_files("Lilu", "1.7.2"))
    res = run(oc, "replace", "Lilu.kext", zip_path, sha)
    assert "is not listed in config.plist" in res.stderr
    assert plistlib.loads((oc / "config.plist").read_bytes())["Kernel"]["Add"] == []
    (oc / "config.plist").write_text("not a plist")
    assert "could not be read" in run(oc, "replace", "Lilu.kext", zip_path, sha).stderr


def test_a_wrong_hash_a_missing_zip_and_a_bad_zip_change_nothing(oc, tmp_path):
    install(oc, "Lilu", "1.6.8")
    zip_path, sha = make_zip(tmp_path / "l.zip", kext_files("Lilu", "1.7.2"))
    assert "checksum mismatch" in run(oc, "replace", "Lilu.kext", zip_path, "0" * 64, expect=2).stderr
    assert "is missing" in run(oc, "replace", "Lilu.kext", tmp_path / "nope.zip", sha, expect=2).stderr
    other, osha = make_zip(tmp_path / "o.zip", kext_files("Other", "1"))
    assert "no Lilu.kext inside" in run(oc, "replace", "Lilu.kext", other, osha, expect=2).stderr
    evil, esha = make_zip(tmp_path / "e.zip", {"Lilu.kext/../../x": b"1", "Lilu.kext/Contents/Info.plist": b"x"})
    assert "unsafe path" in run(oc, "replace", "Lilu.kext", evil, esha, expect=2).stderr
    noinfo, nsha = make_zip(tmp_path / "n.zip", {"Lilu.kext/Contents/MacOS/Lilu": b"x"})
    assert "no readable Contents/Info.plist" in run(oc, "replace", "Lilu.kext", noinfo, nsha, expect=2).stderr
    noexe, xsha = make_zip(tmp_path / "x.zip", {"Lilu.kext/Contents/Info.plist": plistlib.dumps({"CFBundleExecutable": "Lilu"})})
    assert "no executable named Lilu" in run(oc, "replace", "Lilu.kext", noexe, xsha, expect=2).stderr
    empty, esha2 = make_zip(tmp_path / "em.zip", {"Lilu.kext/": b""})
    run(oc, "replace", "Lilu.kext", empty, esha2, expect=2)
    assert json.loads(run(oc, "versions", "Lilu.kext").stdout) == {"Lilu.kext": "1.6.8"}   # the old copy is untouched
    assert not (oc / "Kexts" / "Lilu.kext.tmp").exists()


def test_usage_is_reported(oc):
    assert "usage" in run(oc, expect=64).stderr
