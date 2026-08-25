"""Tests for the local OpenCore image assembly (oc_builder) and its bash parity.

The boot image is no longer a prebuilt release asset: both the Python TUI and
the bash installer assemble it from pinned upstream releases. These tests pin
the invariants of that assembly (component pins, template contents, extraction
safety) and diff the bash installer's embedded copies against the Python
source of truth so the two cannot drift apart.
"""
from __future__ import annotations

import base64
import io
import plistlib
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

import osx_proxmox_next.oc_builder as ocb
from osx_proxmox_next.downloader import RESTRICTEVENTS_VERSION, DownloadError
from osx_proxmox_next.infrastructure import CommandResult
from osx_proxmox_next.oc_builder import (
    OC_COMPONENTS,
    _OC_PKG_FILES,
    _OCBD_SUBTREES,
    Component,
    _check_member,
    _extract_ocbd_resources,
    _extract_zip_files,
    _extract_zip_subtree,
    _fetch_component,
    _iso_build_script,
    _sha256,
    assemble_efi_tree,
    build_opencore_iso,
    ensure_opencore_iso,
)

REPO = Path(__file__).resolve().parent.parent
BASH_SCRIPT = REPO / "scripts/bash/osx-proxmox-next.sh"
DATA_DIR = REPO / "src/osx_proxmox_next/data/opencore"

EXPECTED_KEXT_ORDER = [
    "Lilu.kext",
    "VirtualSMC.kext",
    "WhateverGreen.kext",
    "AppleALC.kext",
    "MCEReporterDisabler.kext",
    "CryptexFixup.kext",
    "RestrictEvents.kext",
]

EXPECTED_DRIVERS = [
    "OpenRuntime.efi",
    "OpenHfsPlus.efi",
    "OpenCanopy.efi",
    "OpenPartitionDxe.efi",
    "ResetNvramEntry.efi",
]

EXPECTED_SSDTS = ["SSDT-DTGP.aml", "SSDT-EC.aml", "SSDT-EHCI.aml", "SSDT-PLUG.aml"]


# ---------------------------------------------------------------------------
# Component pins
# ---------------------------------------------------------------------------


def test_component_pins_are_well_formed() -> None:
    for name, comp in OC_COMPONENTS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", comp.sha256), name
        assert comp.url.startswith("https://github.com/acidanthera/"), name
        assert comp.filename.endswith((".zip", ".tar.gz")), name
        assert comp.version, name


def test_restrictevents_pin_matches_downloader() -> None:
    """downloader.py keeps its own RestrictEvents constants for the legacy
    inject path; the two pins must stay the same version."""
    assert OC_COMPONENTS["RestrictEvents"].version == RESTRICTEVENTS_VERSION
    assert RESTRICTEVENTS_VERSION in OC_COMPONENTS["RestrictEvents"].url


def test_ocbinarydata_is_commit_pinned() -> None:
    comp = OC_COMPONENTS["OcBinaryData"]
    m = re.search(r"/archive/([0-9a-f]{40})\.tar\.gz$", comp.url)
    assert m, comp.url
    assert comp.version == m.group(1)[:12]


def test_component_filenames_are_unique() -> None:
    names = [c.filename for c in OC_COMPONENTS.values()]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# config.plist template invariants
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def template() -> dict:
    return plistlib.loads((DATA_DIR / "config.plist").read_bytes())


def test_template_kexts_exact_order_all_enabled(template: dict) -> None:
    adds = template["Kernel"]["Add"]
    assert [k["BundlePath"] for k in adds] == EXPECTED_KEXT_ORDER
    assert all(k["Enabled"] for k in adds)


def test_template_drivers(template: dict) -> None:
    drivers = template["UEFI"]["Drivers"]
    assert [d["Path"] for d in drivers] == EXPECTED_DRIVERS
    assert all(d["Enabled"] for d in drivers)


def test_template_acpi(template: dict) -> None:
    adds = template["ACPI"]["Add"]
    assert sorted(a["Path"] for a in adds) == EXPECTED_SSDTS
    assert all(a["Enabled"] for a in adds)


def test_template_tools_reference_shipped_files_only(template: dict) -> None:
    tools = {t["Path"]: t for t in template["Misc"]["Tools"]}
    assert set(tools) == {"Shell.efi", "ResetSystem.efi"}
    assert tools["Shell.efi"]["Enabled"] is True
    assert tools["Shell.efi"]["Auxiliary"] is True


def test_template_passes_opencore_1_0_x_schema_gaps(template: dict) -> None:
    """Keys ocvalidate 1.0.7 requires that the historical image predates."""
    assert template["Booter"]["Quirks"]["ClearTaskSwitchBit"] is False
    assert template["UEFI"]["Unload"] == []
    csr = template["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["csr-active-config"]
    assert len(csr) == 4


def test_template_has_no_self_closing_tags() -> None:
    """OpenCore's OcXmlLib rejects <array/>, <dict/>, and <data/>."""
    raw = (DATA_DIR / "config.plist").read_bytes()
    assert b"<array/>" not in raw
    assert b"<dict/>" not in raw
    assert b"<data/>" not in raw


def test_template_survives_runtime_patch_without_duplicates(tmp_path: Path) -> None:
    """The template ships RestrictEvents pre-registered; the runtime patcher
    appends it only when absent. Both paths together must not duplicate it."""
    import os
    import subprocess

    from osx_proxmox_next.script_renderer import _plist_patch_script

    oc_dest = tmp_path / "ocdest"
    (oc_dest / "EFI/OC/Kexts/RestrictEvents.kext").mkdir(parents=True)
    (oc_dest / "EFI/OC/config.plist").write_bytes((DATA_DIR / "config.plist").read_bytes())
    script = _plist_patch_script(is_amd=True, apple_services=True,
                                 smbios_serial="F5KZV0Q1P7QM",
                                 smbios_uuid="0F3F79FD-5A38-4F5F-9B34-52A47B9C3E88",
                                 smbios_mlb="F5K937200GUF8YLJA",
                                 smbios_rom="a1b2c3d4e5f6",
                                 smbios_model="MacPro7,1")
    env = dict(os.environ, OC_DEST=str(oc_dest))
    result = subprocess.run(["bash", "-c", script], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    patched = plistlib.loads((oc_dest / "EFI/OC/config.plist").read_bytes())
    bundles = [k["BundlePath"] for k in patched["Kernel"]["Add"]]
    assert bundles == EXPECTED_KEXT_ORDER
    assert all(k["Enabled"] for k in patched["Kernel"]["Add"])


def test_data_files_exist_and_parse() -> None:
    for aml in EXPECTED_SSDTS:
        assert (DATA_DIR / "ACPI" / aml).stat().st_size > 0
    mce = plistlib.loads(
        (DATA_DIR / "MCEReporterDisabler.kext/Contents/Info.plist").read_bytes()
    )
    assert mce["CFBundleIdentifier"] == "org.rehabman.disabler.MCEReporter"


# ---------------------------------------------------------------------------
# Archive fetch + verification
# ---------------------------------------------------------------------------


def _component_for(path: Path) -> Component:
    return Component("1.0", path.as_uri(), _sha256(path))


def test_fetch_component_cache_hit_skips_download(tmp_path: Path) -> None:
    src = tmp_path / "src.zip"
    src.write_bytes(b"payload")
    comp = Component("1.0", "https://example.invalid/src.zip", _sha256(src))
    got = _fetch_component("Test", comp, tmp_path)
    assert got == src  # never touched the (invalid) URL


def test_fetch_component_downloads_and_verifies(tmp_path: Path) -> None:
    upstream = tmp_path / "up" / "good.zip"
    upstream.parent.mkdir()
    upstream.write_bytes(b"good bytes")
    cache = tmp_path / "cache"
    cache.mkdir()
    got = _fetch_component("Test", _component_for(upstream), cache)
    assert got.read_bytes() == b"good bytes"


def test_fetch_component_redownloads_corrupt_cache(tmp_path: Path) -> None:
    upstream = tmp_path / "up" / "good.zip"
    upstream.parent.mkdir()
    upstream.write_bytes(b"good bytes")
    cache = tmp_path / "cache"
    cache.mkdir()
    stale = cache / "good.zip"
    stale.write_bytes(b"corrupted")
    got = _fetch_component("Test", _component_for(upstream), cache)
    assert got.read_bytes() == b"good bytes"


def test_fetch_component_hash_mismatch_raises_and_cleans(tmp_path: Path) -> None:
    upstream = tmp_path / "up" / "evil.zip"
    upstream.parent.mkdir()
    upstream.write_bytes(b"tampered")
    cache = tmp_path / "cache"
    cache.mkdir()
    comp = Component("1.0", upstream.as_uri(), "0" * 64)
    with pytest.raises(DownloadError, match="checksum mismatch"):
        _fetch_component("Test", comp, cache)
    assert not (cache / "evil.zip").exists()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _make_targz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
                continue
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_check_member_rejects_traversal_and_absolute() -> None:
    with pytest.raises(DownloadError):
        _check_member("../evil")
    with pytest.raises(DownloadError):
        _check_member("a/../../evil")
    with pytest.raises(DownloadError):
        _check_member("/abs/path")
    _check_member("EFI/OC/ok..txt")  # dots inside a name are fine


def test_extract_zip_subtree_strips_components(tmp_path: Path) -> None:
    z = _make_zip(tmp_path / "vsmc.zip", {
        "Kexts/VirtualSMC.kext/": b"",  # explicit directory entry is skipped
        "Kexts/VirtualSMC.kext/Contents/Info.plist": b"x",
        "Kexts/SMCProcessor.kext/Contents/Info.plist": b"ignored",
        "Drivers/other.efi": b"ignored",
    })
    dest = tmp_path / "kexts"
    n = _extract_zip_subtree(z, "Kexts/VirtualSMC.kext/", dest, strip=1)
    assert n == 1
    assert (dest / "VirtualSMC.kext/Contents/Info.plist").read_bytes() == b"x"
    assert not (dest / "SMCProcessor.kext").exists()


def test_extract_zip_subtree_missing_prefix_raises(tmp_path: Path) -> None:
    z = _make_zip(tmp_path / "empty.zip", {"other/file": b"x"})
    with pytest.raises(DownloadError, match="No members"):
        _extract_zip_subtree(z, "Lilu.kext/", tmp_path / "out")


def test_extract_zip_subtree_rejects_traversal(tmp_path: Path) -> None:
    z = _make_zip(tmp_path / "evil.zip", {"Lilu.kext/../../evil": b"x"})
    with pytest.raises(DownloadError, match="unsafe"):
        _extract_zip_subtree(z, "Lilu.kext/", tmp_path / "out")


def test_extract_zip_files_missing_member_raises(tmp_path: Path) -> None:
    z = _make_zip(tmp_path / "oc.zip", {"X64/EFI/OC/OpenCore.efi": b"x"})
    with pytest.raises(DownloadError, match="Missing"):
        _extract_zip_files(z, {"X64/EFI/BOOT/BOOTx64.efi": "EFI/BOOT/BOOTx64.efi"},
                           tmp_path / "out")


def test_extract_ocbd_takes_only_pinned_subtrees(tmp_path: Path) -> None:
    tar = _make_targz(tmp_path / "ocbd.tar.gz", {
        "OcBinaryData-abc/Resources/Font/": b"",  # directory member is skipped
        "OcBinaryData-abc/Resources/Font/Font_1x.bin": b"font",
        "OcBinaryData-abc/Resources/Label/Apple.lbl": b"label",
        "OcBinaryData-abc/Resources/Image/Acidanthera/Syrah/Cursor.icns": b"icon",
        "OcBinaryData-abc/Resources/Image/Acidanthera/GoldenGate/Cursor.icns": b"skip",
        "OcBinaryData-abc/Resources/Audio/OCEFIAudio_Beep.mp3": b"skip",
    })
    dest = tmp_path / "tree"
    _extract_ocbd_resources(tar, dest)
    base = dest / "EFI/OC/Resources"
    assert (base / "Font/Font_1x.bin").read_bytes() == b"font"
    assert (base / "Label/Apple.lbl").exists()
    assert (base / "Image/Acidanthera/Syrah/Cursor.icns").exists()
    assert not (base / "Image/Acidanthera/GoldenGate").exists()
    assert not (base / "Audio").exists()


def test_extract_ocbd_empty_raises(tmp_path: Path) -> None:
    tar = _make_targz(tmp_path / "ocbd.tar.gz", {"OcBinaryData-abc/README.md": b"x"})
    with pytest.raises(DownloadError, match="No OpenCanopy resources"):
        _extract_ocbd_resources(tar, tmp_path / "tree")


# ---------------------------------------------------------------------------
# Full tree assembly (fake pinned components served over file://)
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_components(tmp_path: Path, monkeypatch) -> Path:
    up = tmp_path / "upstream"
    up.mkdir()
    oc_members = {name: b"efi-" + name.encode() for name in _OC_PKG_FILES}
    archives = {
        "OpenCorePkg": _make_zip(up / "OpenCore-RELEASE.zip", oc_members),
        "Lilu": _make_zip(up / "Lilu.zip", {"Lilu.kext/Contents/Info.plist": b"l"}),
        "VirtualSMC": _make_zip(up / "VirtualSMC.zip", {
            "Kexts/VirtualSMC.kext/Contents/Info.plist": b"v",
            "Kexts/SMCProcessor.kext/Contents/Info.plist": b"skip",
        }),
        "WhateverGreen": _make_zip(up / "WhateverGreen.zip",
                                   {"WhateverGreen.kext/Contents/Info.plist": b"w"}),
        "AppleALC": _make_zip(up / "AppleALC.zip", {
            "AppleALC.kext/Contents/Info.plist": b"a",
            "AppleALCU.kext/Contents/Info.plist": b"skip",
        }),
        "CryptexFixup": _make_zip(up / "CryptexFixup.zip",
                                  {"CryptexFixup.kext/Contents/Info.plist": b"c"}),
        "RestrictEvents": _make_zip(up / "RestrictEvents.zip",
                                    {"RestrictEvents.kext/Contents/Info.plist": b"r"}),
        "OcBinaryData": _make_targz(up / "OcBinaryData.tar.gz", {
            "OcBinaryData-abc/Resources/Font/Font_1x.bin": b"font",
        }),
    }
    fake = {name: _component_for(path) for name, path in archives.items()}
    monkeypatch.setattr(ocb, "OC_COMPONENTS", fake)
    return tmp_path


def test_assemble_efi_tree_layout(fake_components: Path) -> None:
    cache = fake_components / "cache"
    cache.mkdir()
    work = fake_components / "work"
    work.mkdir()
    tree = assemble_efi_tree(cache, work)

    for rel in _OC_PKG_FILES.values():
        assert (tree / rel).exists(), rel
    kexts = tree / "EFI/OC/Kexts"
    expected = {k.replace(".kext", "") for k in EXPECTED_KEXT_ORDER}
    assert {p.name.replace(".kext", "") for p in kexts.iterdir()} == expected
    assert not (kexts / "SMCProcessor.kext").exists()
    assert not (kexts / "AppleALCU.kext").exists()
    assert (tree / "EFI/OC/config.plist").read_bytes() == (DATA_DIR / "config.plist").read_bytes()
    for aml in EXPECTED_SSDTS:
        assert (tree / "EFI/OC/ACPI" / aml).exists()
    assert (tree / "EFI/OC/Resources/Font/Font_1x.bin").exists()
    # Shell.efi is OpenShell.efi renamed to match Misc->Tools
    assert (tree / "EFI/OC/Tools/Shell.efi").read_bytes() == b"efi-X64/EFI/OC/Tools/OpenShell.efi"


# ---------------------------------------------------------------------------
# Image build + ensure orchestration
# ---------------------------------------------------------------------------


def test_iso_build_script_contents(tmp_path: Path) -> None:
    script = _iso_build_script(tmp_path / "tree", tmp_path / "oc.iso.part")
    for needle in ("truncate -s 128M", "sgdisk -Z", "-t 1:EF00", "mkfs.fat -F 32",
                   "losetup -fP", "trap", "cp -a", "umount", "losetup -j"):
        assert needle in script, needle


class _FakeAdapter:
    def __init__(self, ok: bool, create: bool = True):
        self._ok = ok
        self._create = create
        self.scripts: list[str] = []

    def run(self, argv):
        self.scripts.append(argv[-1])
        if self._create:
            part = re.search(r"truncate -s \d+M (\S+)", argv[-1]).group(1).strip("'")
            Path(part).write_bytes(b"img")
        return CommandResult(ok=self._ok, returncode=0 if self._ok else 1,
                             output="" if self._ok else "loop failed")


def _patch_adapter(monkeypatch, adapter) -> None:
    monkeypatch.setattr("osx_proxmox_next.services.get_proxmox_adapter", lambda: adapter)


def test_build_opencore_iso_success(fake_components: Path, monkeypatch) -> None:
    adapter = _FakeAdapter(ok=True)
    _patch_adapter(monkeypatch, adapter)
    dest = fake_components / "iso"
    iso = build_opencore_iso(dest)
    assert iso == dest / "opencore-osx-proxmox-vm.iso"
    assert iso.exists()
    assert not (dest / (iso.name + ".part")).exists()
    assert not list(dest.glob(".oc-build-*"))  # work dir cleaned up


def test_build_opencore_iso_failure_raises_and_cleans(fake_components: Path,
                                                      monkeypatch) -> None:
    adapter = _FakeAdapter(ok=False)
    _patch_adapter(monkeypatch, adapter)
    dest = fake_components / "iso"
    with pytest.raises(DownloadError, match="build failed"):
        build_opencore_iso(dest)
    assert not (dest / "opencore-osx-proxmox-vm.iso").exists()
    assert not (dest / "opencore-osx-proxmox-vm.iso.part").exists()
    assert not list(dest.glob(".oc-build-*"))


def test_ensure_opencore_iso_cache_hit(tmp_path: Path, monkeypatch) -> None:
    iso = tmp_path / "opencore-osx-proxmox-vm.iso"
    iso.write_bytes(b"cached")
    monkeypatch.setattr(ocb, "build_opencore_iso",
                        lambda *a, **k: pytest.fail("must not rebuild on cache hit"))
    assert ensure_opencore_iso("sequoia", tmp_path) == iso


def test_ensure_opencore_iso_force_rebuilds(tmp_path: Path, monkeypatch) -> None:
    iso = tmp_path / "opencore-osx-proxmox-vm.iso"
    iso.write_bytes(b"cached")
    built = []
    monkeypatch.setattr(ocb, "build_opencore_iso",
                        lambda dest, on_progress=None: built.append(dest) or iso)
    assert ensure_opencore_iso("sequoia", tmp_path, force=True) == iso
    assert built == [tmp_path]


def test_ensure_opencore_iso_falls_back_to_prebuilt(tmp_path: Path, monkeypatch) -> None:
    def boom(dest, on_progress=None):
        raise DownloadError("upstream offline")

    fallback_iso = tmp_path / "opencore-osx-proxmox-vm.iso"
    calls = []

    def fake_download(macos, dest_dir, on_progress=None, force=False):
        calls.append((macos, force))
        fallback_iso.write_bytes(b"prebuilt")
        return fallback_iso

    monkeypatch.setattr(ocb, "build_opencore_iso", boom)
    monkeypatch.setattr(ocb, "download_opencore", fake_download)
    got = ensure_opencore_iso("sequoia", tmp_path)
    assert got == fallback_iso
    assert calls == [("sequoia", False)]


# ---------------------------------------------------------------------------
# Bash installer parity (the Python/bash parity rule)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bash_text() -> str:
    return BASH_SCRIPT.read_text()


def _bash_assoc(bash_text: str, name: str) -> dict[str, str]:
    m = re.search(rf"declare -A {name}=\((.*?)\n\)", bash_text, re.S)
    assert m, f"{name} not found in bash script"
    body = m.group(1)
    entries = dict(re.findall(r'\["([^"]+)"\]="([^"]+)"', body))
    assert entries, name
    return entries


def test_bash_component_pins_match_python(bash_text: str) -> None:
    urls = _bash_assoc(bash_text, "OC_COMPONENT_URLS")
    shas = _bash_assoc(bash_text, "OC_COMPONENT_SHA256")
    acid = re.search(r'^OC_ACID="([^"]+)"', bash_text, re.M).group(1)
    commit = re.search(r'^OCBD_COMMIT="([0-9a-f]{40})"', bash_text, re.M).group(1)
    assert set(urls) == set(OC_COMPONENTS)
    assert set(shas) == set(OC_COMPONENTS)
    for name, comp in OC_COMPONENTS.items():
        expanded = urls[name].replace("$OC_ACID", acid).replace("${OCBD_COMMIT}", commit)
        assert expanded == comp.url, name
        assert shas[name] == comp.sha256, name


@pytest.mark.parametrize("var,rel", [
    ("OC_CONFIG_TEMPLATE_B64", "config.plist"),
    ("OC_SSDT_DTGP_B64", "ACPI/SSDT-DTGP.aml"),
    ("OC_SSDT_EC_B64", "ACPI/SSDT-EC.aml"),
    ("OC_SSDT_EHCI_B64", "ACPI/SSDT-EHCI.aml"),
    ("OC_SSDT_PLUG_B64", "ACPI/SSDT-PLUG.aml"),
    ("OC_MCE_INFO_PLIST_B64", "MCEReporterDisabler.kext/Contents/Info.plist"),
])
def test_bash_embedded_blobs_match_data_files(bash_text: str, var: str, rel: str) -> None:
    m = re.search(rf'^{var}="([A-Za-z0-9+/=]+)"$', bash_text, re.M)
    assert m, f"{var} not found in bash script"
    assert base64.b64decode(m.group(1)) == (DATA_DIR / rel).read_bytes()


def test_bash_extraction_map_matches_python(bash_text: str) -> None:
    for member, rel in _OC_PKG_FILES.items():
        assert f'"{member}": "{rel}"' in bash_text, member
    for subtree in _OCBD_SUBTREES:
        assert f'"{subtree}"' in bash_text, subtree


def test_bash_kext_prefixes_match_python(bash_text: str) -> None:
    for prefix, strip in [("Lilu.kext/", 0), ("WhateverGreen.kext/", 0),
                          ("AppleALC.kext/", 0), ("CryptexFixup.kext/", 0),
                          ("RestrictEvents.kext/", 0),
                          ("Kexts/VirtualSMC.kext/", 1)]:
        assert f'"{prefix}", {strip}' in bash_text, prefix


def test_bash_falls_back_to_prebuilt_iso(bash_text: str) -> None:
    assert "assemble_opencore_iso" in bash_text
    assert 'OC_URL="https://github.com/lucid-fabrics/osx-proxmox-next/releases/download/assets/opencore-osx-proxmox-vm.iso"' in bash_text
    fallback = bash_text.index("falling back to prebuilt ISO")
    assert bash_text.index("assemble_opencore_iso \"$OC_ISO\"") < fallback


def test_ensure_opencore_iso_checksum_mismatch_never_falls_back(tmp_path: Path,
                                                                monkeypatch) -> None:
    """A hash mismatch is the tamper signal: it must abort, not silently
    downgrade to the unpinned prebuilt ISO."""
    from osx_proxmox_next.oc_builder import ChecksumError

    def tampered(dest, on_progress=None):
        raise ChecksumError("Lilu checksum mismatch")

    monkeypatch.setattr(ocb, "build_opencore_iso", tampered)
    monkeypatch.setattr(ocb, "download_opencore",
                        lambda *a, **k: pytest.fail("fell back on checksum mismatch"))
    with pytest.raises(ChecksumError):
        ensure_opencore_iso("sequoia", tmp_path)


def test_assemble_progress_is_monotonic_across_components(fake_components: Path,
                                                          monkeypatch) -> None:
    """The phase progress bar must climb once across all components, not
    reset to 0% for every downloaded archive."""
    cache = fake_components / "cache"
    cache.mkdir()
    work = fake_components / "work"
    work.mkdir()
    seen: list[float] = []

    def record(p):
        seen.append(p.downloaded / p.total)

    assemble_efi_tree(cache, work, on_progress=record)
    assert seen, "no progress reported"
    assert seen == sorted(seen), "progress went backwards"
    assert seen[-1] <= 1.0


# ---------------------------------------------------------------------------
# Bash execution parity (needs bash >= 4 and sha256sum; runs in CI, skips
# on stock macOS)
# ---------------------------------------------------------------------------

import shutil as _shutil
import subprocess as _sp


def _bash_supports_harness() -> bool:
    bash = _shutil.which("bash")
    if not bash or not _shutil.which("sha256sum"):
        return False
    # bash 3.x accepts string subscripts by collapsing them to index 0, so an
    # assignment probe passes falsely; check the version number instead.
    probe = _sp.run([bash, "-c", 'echo "${BASH_VERSINFO[0]}"'],
                    capture_output=True, text=True)
    return probe.stdout.strip().isdigit() and int(probe.stdout.strip()) >= 4


_bash_harness = pytest.mark.skipif(
    not _bash_supports_harness(),
    reason="needs bash >= 4 (associative arrays) and sha256sum",
)


def _extract_bash_sections(text: str) -> str:
    """The pins block plus the embedded-data/assembly-function block."""
    pins_start = text.index("# ── OpenCore component pins")
    pins_end = text.index("set -euo pipefail")
    funcs_start = text.index("# ── Embedded OpenCore data files")
    funcs_end = text.index("# ── Build OpenCore GPT+ESP disk from source ISO")
    return text[pins_start:pins_end] + "\n" + text[funcs_start:funcs_end]


def _write_harness(tmp_path: Path, bash_text: str, body: str,
                   fixtures: dict[str, Path]) -> Path:
    extracted = tmp_path / "extracted.sh"
    extracted.write_text(_extract_bash_sections(bash_text))
    overrides = "\n".join(
        f'OC_COMPONENT_URLS[{name}]="{path.as_uri()}"\n'
        f'OC_COMPONENT_SHA256[{name}]="{_sha256(path)}"'
        for name, path in fixtures.items()
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        'YW=""; CL=""; RD=""; GN=""\n'
        "msg_info() { :; }\n"
        "msg_ok() { :; }\n"
        'msg_error() { echo "ERR: $1" >&2; }\n'
        "cleanup_stale_loops() { :; }\n"
        'BUILD_LOOP=""; BUILD_DEST_MNT=""\n'
        f'source "{extracted}"\n'
        + overrides + "\n"
        + body
    )
    return harness


def _fixture_archives(tmp_path: Path) -> dict[str, Path]:
    """Same shapes as the fake_components fixture, for the bash side."""
    up = tmp_path / "bash-upstream"
    up.mkdir()
    oc_members = {name: b"efi-" + name.encode() for name in _OC_PKG_FILES}
    return {
        "OpenCorePkg": _make_zip(up / "OpenCore-RELEASE.zip", oc_members),
        "Lilu": _make_zip(up / "Lilu.zip", {"Lilu.kext/Contents/Info.plist": b"l"}),
        "VirtualSMC": _make_zip(up / "VirtualSMC.zip", {
            "Kexts/VirtualSMC.kext/Contents/Info.plist": b"v",
            "Kexts/SMCProcessor.kext/Contents/Info.plist": b"skip",
        }),
        "WhateverGreen": _make_zip(up / "WhateverGreen.zip",
                                   {"WhateverGreen.kext/Contents/Info.plist": b"w"}),
        "AppleALC": _make_zip(up / "AppleALC.zip", {
            "AppleALC.kext/Contents/Info.plist": b"a",
            "AppleALCU.kext/Contents/Info.plist": b"skip",
        }),
        "CryptexFixup": _make_zip(up / "CryptexFixup.zip",
                                  {"CryptexFixup.kext/Contents/Info.plist": b"c"}),
        "RestrictEvents": _make_zip(up / "RestrictEvents.zip",
                                    {"RestrictEvents.kext/Contents/Info.plist": b"r"}),
        "OcBinaryData": _make_targz(up / "OcBinaryData.tar.gz", {
            "OcBinaryData-abc/Resources/Font/Font_1x.bin": b"font",
        }),
    }


@_bash_harness
def test_bash_assemble_tree_executes_identically_to_python(tmp_path: Path,
                                                           bash_text: str,
                                                           monkeypatch) -> None:
    """Run the real bash assembly (fixture archives over file://) and diff
    the resulting tree byte-for-byte against the Python assembly."""
    fixtures = _fixture_archives(tmp_path)
    cache = tmp_path / "bash-cache"
    cache.mkdir()
    bash_tree = tmp_path / "bash-tree"
    harness = _write_harness(
        tmp_path, bash_text,
        f'CACHE_DIR="{cache}"\n'
        f'assemble_opencore_tree "{bash_tree}" || {{ echo TREE_FAILED; exit 1; }}\n',
        fixtures,
    )
    result = _sp.run(["bash", str(harness)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

    monkeypatch.setattr(ocb, "OC_COMPONENTS",
                        {n: _component_for(p) for n, p in fixtures.items()})
    py_cache = tmp_path / "py-cache"
    py_cache.mkdir()
    py_work = tmp_path / "py-work"
    py_work.mkdir()
    py_tree = assemble_efi_tree(py_cache, py_work)

    bash_files = {f.relative_to(bash_tree).as_posix(): f.read_bytes()
                  for f in bash_tree.rglob("*") if f.is_file()}
    py_files = {f.relative_to(py_tree).as_posix(): f.read_bytes()
                for f in py_tree.rglob("*") if f.is_file()}
    assert bash_files == py_files


@_bash_harness
def test_bash_disk_stage_failure_reaches_fallback_branch(tmp_path: Path,
                                                         bash_text: str) -> None:
    """A loop-device failure inside assemble_opencore_iso must RETURN to the
    caller's else-branch (prebuilt fallback), never exit the installer."""
    fixtures = _fixture_archives(tmp_path)
    cache = tmp_path / "bash-cache"
    cache.mkdir()
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, script in {
        "truncate": "#!/bin/sh\n: > \"$3\"\nexit 0\n",
        "sgdisk": "#!/bin/sh\nexit 0\n",
        "losetup": "#!/bin/sh\nexit 1\n",
    }.items():
        shim = shims / name
        shim.write_text(script)
        shim.chmod(0o755)
    dest_iso = tmp_path / "out" / "opencore-osx-proxmox-vm.iso"
    dest_iso.parent.mkdir()
    tmpd = tmp_path / "tempdir"
    tmpd.mkdir()
    harness = _write_harness(
        tmp_path, bash_text,
        f'CACHE_DIR="{cache}"\n'
        f'TEMP_DIR="{tmpd}"\n'
        f'PATH="{shims}:$PATH"\n'
        f'if assemble_opencore_iso "{dest_iso}"; then echo BUILT; else echo FELL_BACK; fi\n'
        "echo SURVIVED\n",
        fixtures,
    )
    result = _sp.run(["bash", str(harness)], capture_output=True, text=True)
    out = result.stdout
    assert "FELL_BACK" in out, result.stdout + result.stderr
    assert "SURVIVED" in out, "assemble_opencore_iso exited the shell instead of returning"
    assert "BUILT" not in out
    assert not dest_iso.exists()
