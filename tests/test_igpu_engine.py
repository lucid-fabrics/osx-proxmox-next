"""The Intel iGPU engine: device list, memory layout, VM settings, host checks, downloads, payload disk."""
from __future__ import annotations

import hashlib
import io
import json
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from gpu_fakes import FakeRun
from nullmoth_fakes import add_dev
from osx_proxmox_next import igpu_engine as eng

LABG5 = (0x4B800001, 0x02C1)  # BDSM and GGC read from the tested host


def igd(root: Path, device: int = 0x46A6, driver: str | None = "vfio-pci", cls: int = 0x030000, vendor: int = 0x8086) -> None:
    add_dev(root / "sys", eng.IGD_BDF, vendor, device, cls=cls, driver=driver, group=1, boot_vga=True)


def regs(bdsm: int = LABG5[0], ggc: int = LABG5[1]) -> FakeRun:
    return FakeRun({"setpci -s 00:02.0 C0.L 50.W": (0, f"{bdsm:08x}\n{ggc:04x}\n")})


# ── layout ───────────────────────────────────────────────────────────

def test_the_tested_host_gets_1342m():
    lay = eng.layout(*LABG5)
    assert lay["ok"] and lay["host_top"] == 0x4F800000 and lay["max_ram_below_4g_mb"] == 1342


@pytest.mark.parametrize("ggc,size", [(0x0000, 0), (0x0100, 32), (0x0200, 64), (0x1000, 512), (0xF000, 4), (0xFE00, 60)])
def test_gms_decoding(ggc, size):
    assert eng.decode_gms(ggc) == size * eng.MB


@pytest.mark.parametrize("ggc", [0x1100, 0xEF00, 0xFF00])
def test_gms_reserved_values_are_refused(ggc):
    with pytest.raises(ValueError):
        eng.decode_gms(ggc)
    assert not eng.layout(0x4B800000, ggc)["ok"]


def test_bdsm_drops_the_lock_and_low_bits():
    assert eng.decode_bdsm(0x4B8FFFFF) == 0x4B800000


def test_result_is_rounded_up_to_two_megabytes():
    # top 0x40100000 (1025 MB) + 70 MB = 1095 MB, odd, so 1096
    assert eng.max_ram_below_4g_mb(0x40100000) == 1096


def test_no_stolen_memory_is_refused():
    assert "BDSM is 0" in eng.layout(0, 0x0200)["issue"]


@pytest.mark.parametrize("top_mb,ok", [(2400, True), (2490, True), (2491, False), (2900, False), (3002, False),
                                       (3003, True), (3514, True), (3515, False)])
def test_refused_ranges(top_mb, ok):
    # result = top + 70 MB: 2560 < result <= 3072 is the emulator range, above 3584 is the PCI hole
    lay = eng.layout(top_mb * eng.MB, 0)
    assert lay["ok"] is ok
    if not ok:
        assert "emulator" in lay["issue"] or "PCI hole" in lay["issue"]


# ── device list ──────────────────────────────────────────────────────

def test_the_list_is_whatever_xes_with_one_tested_chip():
    _fam, devices = eng.parse_devices(eng.devices_text())
    assert len(devices) == 62
    assert [hex(d) for d, v in devices.items() if v[1] == "tested"] == ["0x46a6"]


@pytest.mark.parametrize("device,family,rom", [(0x46A6, "adl-p", "adl-h"), (0xA7A0, "rpl-p", "adl-h"), (0x9A49, "tgl", "tgl"),
                                               (0x4680, "adl-s", "tgl"), (0xA780, "rpl-s", "tgl"), (0x46D1, "adl-n", "adl-n")])
def test_rom_follows_the_family(device, family, rom):
    info = eng.lookup(device)
    assert info["family"] == family and info["rom"] == rom and info["rom_file"] == eng.ROMS[rom][0]


def test_verdicts():
    assert eng.verdict(0x46A6)[0] == "intel"
    assert eng.verdict(0x9A49)[0] == "intel-untested"
    kind, head, _ = eng.verdict(0x4C8A)
    assert kind == "unsupported" and "Not supported yet" in head
    assert eng.verdict(0x56A0)[0] == "unsupported"  # an Arc card
    assert eng.lookup(0x56A0) is None


def test_every_rom_and_pin_is_a_pinned_hash():
    for _name, url, sha in [*eng.ROMS.values(), *eng.PINS.values()]:
        assert url.startswith("https://github.com/") and len(sha) == 64
    assert "/main/" not in eng.PINS["amfipass"][1] and eng.PINS["amfipass"][1].count("88d64a34") == 1


# ── VM settings ──────────────────────────────────────────────────────

def test_vm_settings():
    s = eng.vm_settings(1342, "x.rom")
    assert s["machine"] == "pc" and s["vga"] == "none"
    assert s["hostpci0"] == "0000:00:02.0,legacy-igd=1,romfile=x.rom"
    assert s["args_add"] == ["-machine max-ram-below-4g=1342M", "-set device.hostpci0.x-igd-lpc=on",
                             "-set device.hostpci0.x-igd-gms=0x3"]


def test_args_merge_replaces_and_never_duplicates():
    add = eng.vm_settings(1342, "r")["args_add"]
    once = eng.merge_args("-device a -machine max-ram-below-4g=2G", add)
    assert once.count("max-ram-below-4g") == 1 and "=1342M" in once and once.startswith("-device a")
    assert eng.merge_args(once, add) == once
    assert eng.merge_args("", add).startswith("-machine")


def test_args_strip():
    add = eng.vm_settings(1342, "r")["args_add"]
    assert eng.strip_args(eng.merge_args("-device a", add), add) == "-device a"
    assert eng.strip_args("", add) == ""


def test_recovery_bus():
    assert eng.recovery_bus({"sata1": "x"}) == "sata1"
    assert eng.recovery_bus({"ide2": "x"}) == "ide2"
    assert eng.recovery_bus({"ide0": "x"}) is None


# ── host check ───────────────────────────────────────────────────────

def test_check_passes_on_the_tested_host(tmp_path):
    igd(tmp_path)
    rep = eng.check(str(tmp_path), regs())
    assert rep["ok"] and rep["layout"]["max_ram_below_4g_mb"] == 1342 and not rep["warnings"]
    assert rep["info"]["rom_file"] == "ADL-H_RPL-H_GOPv21_igd.rom"


def test_check_warns_about_an_untested_chip(tmp_path):
    igd(tmp_path, device=0x9A49)
    rep = eng.check(str(tmp_path), regs())
    assert rep["ok"] and "not been tested" in rep["warnings"][0]


@pytest.mark.parametrize("kw,text", [
    ({"vendor": 0x10DE}, "not an Intel device"),
    ({"cls": 0x040300}, "not a VGA display"),
    ({"device": 0x56A0}, "Intel 56a0"),
    ({"driver": "i915"}, "driver: i915"),
    ({"driver": None}, "driver: none"),
])
def test_check_refuses(tmp_path, kw, text):
    igd(tmp_path, **kw)
    rep = eng.check(str(tmp_path), regs())
    assert not rep["ok"] and text in " ".join(rep["issues"])


def test_check_without_the_device(tmp_path):
    rep = eng.check(str(tmp_path), regs())
    assert not rep["ok"] and "00:02.0" in rep["issues"][0]


def test_check_without_setpci_or_with_a_bad_layout(tmp_path):
    igd(tmp_path)
    assert "setpci" in " ".join(eng.check(str(tmp_path), FakeRun())["issues"])
    assert "setpci" in " ".join(eng.check(str(tmp_path), FakeRun({"setpci": (0, "zz\nzz")}))["issues"])
    rep = eng.check(str(tmp_path), regs(ggc=0xFF00))
    assert not rep["ok"] and "GMS" in rep["issues"][0]


def test_check_needs_an_iommu_group(tmp_path):
    igd(tmp_path)
    (tmp_path / "sys/bus/pci/devices" / eng.IGD_BDF / "iommu_group").unlink()
    assert "IOMMU" in " ".join(eng.check(str(tmp_path), regs())["issues"])


def test_default_run_survives_a_missing_command():
    assert eng.default_run(["definitely-not-a-command-xyz"]) == (127, "")
    assert eng.default_run([sys.executable, "-c", "print('hi')"]) == (0, "hi\n")


# ── downloads ────────────────────────────────────────────────────────

class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_downloads_verifies_and_reuses(tmp_path, monkeypatch):
    body = b"payload"
    sha = hashlib.sha256(body).hexdigest()
    calls = []
    monkeypatch.setattr(eng.urllib.request, "urlopen", lambda url, timeout: calls.append(url) or Resp(body))
    out = eng.fetch("https://x/y", sha, tmp_path / "d" / "y")
    assert out.read_bytes() == body and len(calls) == 1
    eng.fetch("https://x/y", sha, out)
    assert len(calls) == 1  # cached


def test_fetch_rejects_a_mismatch_and_keeps_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(eng.urllib.request, "urlopen", lambda url, timeout: Resp(b"other"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        eng.fetch("https://x/y", "0" * 64, tmp_path / "y")
    assert not list(tmp_path.iterdir())


# ── payload disk ─────────────────────────────────────────────────────

def test_payload_files_carry_the_package_hash():
    assert "@PKG_SHA256@" not in eng.render_payload_file("igpu_firstboot.sh", "ab" * 32)
    assert "ab" * 32 in eng.render_payload_file("igpu_firstboot.sh", "ab" * 32)
    assert "STAGE_COMMAND" not in eng.STAGE_COMMAND and eng.STAGE_COMMAND.endswith("mount -t msdos /dev/$d /tmp/p&&sh /tmp/p/stage.sh") and "OSXPNIGPU" in eng.STAGE_COMMAND


class DiskRun:
    """Stands in for sgdisk, losetup, partprobe, mkfs.vfat, mount and umount; mount drops a folder the engine fills."""

    def __init__(self, fail: str = "", result: str = "", umount_fails: bool = False, no_partition: bool = False) -> None:
        self.calls, self.fail, self.result, self.umount_fails = [], fail, result, umount_fails
        self.no_partition = no_partition

    def __call__(self, argv, timeout=20):
        self.calls.append(argv)
        if argv[0] == self.fail:
            return 1, "boom"
        if argv[:2] == ["losetup", "-fP"]:
            return 0, "/dev/loop9\n"
        if argv[0] == "test":
            return (1, "") if self.no_partition else (0, "")
        if argv[0] == "mount" and self.result:
            Path(argv[-1], "result.txt").write_text(self.result)
        if argv[0] == "umount" and self.umount_fails:
            return 1, "busy"
        if argv[0] == "umount":  # a real unmount leaves an empty folder
            for child in Path(argv[-1]).iterdir():
                child.unlink()
        return 0, ""

    def names(self):
        return [c[0] for c in self.calls]


def test_payload_build(tmp_path):
    pkg = tmp_path / "p.pkg"
    pkg.write_bytes(b"pkg")
    run = DiskRun()
    ok, msg = eng.build_payload(str(pkg), str(tmp_path / "img"), run)
    assert ok and (tmp_path / "img").stat().st_size == eng.PAYLOAD_MB * eng.MB
    sgdisk = [c for c in run.calls if c[0] == "sgdisk"]
    assert sgdisk[1][:5] == ["sgdisk", "-n", "1:0:0", "-t", "1:0700"]  # GPT with one Microsoft basic data partition
    mkfs = next(c for c in run.calls if c[0] == "mkfs.vfat")
    assert mkfs[:5] == ["mkfs.vfat", "-F", "32", "-n", "OSXPNIGPU"] and mkfs[-1] == "/dev/loop9p1"
    assert run.names() == ["sgdisk", "sgdisk", "losetup", "partprobe", "test", "mkfs.vfat", "mount", "sync", "umount", "losetup"]
    assert run.calls[-1] == ["losetup", "-d", "/dev/loop9"]


@pytest.mark.parametrize("fail,text,detached", [("sgdisk", "sgdisk failed", False), ("losetup", "could not attach", False),
                                                ("mkfs.vfat", "mkfs.vfat failed", True), ("mount", "could not mount", True)])
def test_payload_build_failures(tmp_path, fail, text, detached):
    pkg = tmp_path / "p.pkg"
    pkg.write_bytes(b"pkg")
    run = DiskRun(fail)
    ok, msg = eng.build_payload(str(pkg), str(tmp_path / "img"), run)
    assert not ok and text in msg
    assert (["losetup", "-d", "/dev/loop9"] in run.calls) is detached  # a loop device is never left behind


def test_a_partition_that_never_appears_is_an_error(tmp_path):
    pkg = tmp_path / "p.pkg"
    pkg.write_bytes(b"pkg")
    run = DiskRun(no_partition=True)
    ok, msg = eng.build_payload(str(pkg), str(tmp_path / "img"), run)
    assert not ok and "never appeared" in msg and run.calls[-1] == ["losetup", "-d", "/dev/loop9"]
    assert eng.read_result(tmp_path / "i", DiskRun(no_partition=True)) == ""


def test_a_busy_mount_is_never_deleted_recursively(tmp_path):
    pkg = tmp_path / "p.pkg"
    pkg.write_bytes(b"pkg")
    run = DiskRun(umount_fails=True)
    ok, _msg = eng.build_payload(str(pkg), str(tmp_path / "img"), run)
    mnt = Path(next(c for c in run.calls if c[0] == "mount")[-1])
    assert ok and (mnt / "WhateverXe-Graphics.pkg").exists()  # still there: nothing removed the files
    assert ["losetup", "-d", "/dev/loop9"] not in run.calls  # and the loop device still backs the mount
    assert eng.read_result(tmp_path / "i", DiskRun(result="x", umount_fails=True)) == "x"


def test_payload_result(tmp_path):
    assert eng.read_result(tmp_path / "i", DiskRun(result="installed\n")) == "installed"
    assert eng.read_result(tmp_path / "i", DiskRun()) == ""
    assert eng.read_result(tmp_path / "i", DiskRun(fail="mount")) == ""
    assert eng.read_result(tmp_path / "i", DiskRun(fail="losetup")) == ""


# ── command line ─────────────────────────────────────────────────────

def cli(*argv, env=None, stdin=""):
    full = {"PATH": "/usr/bin:/bin", **(env or {})}
    return subprocess.run([sys.executable, eng.__file__, *argv], capture_output=True, text=True, input=stdin, env=full)


def test_cli_commands(tmp_path):
    igd(tmp_path)
    assert json.loads(cli("verdict", "46a6").stdout)["kind"] == "intel"
    assert json.loads(cli("lookup", "46a6").stdout)["family"] == "adl-p"
    assert json.loads(cli("lookup", "ffff").stdout) == {}
    assert json.loads(cli("settings", "1342", "r.rom").stdout)["machine"] == "pc"
    assert cli("stage-command").stdout.strip() == eng.STAGE_COMMAND
    pins = json.loads(cli("pins").stdout)
    assert pins["pins"]["graphics"][2] == eng.PINS["graphics"][2]
    add = eng.vm_settings(1342, "r")["args_add"]
    merged = cli("merge-args", stdin=json.dumps({"args": "-x", "wanted": add})).stdout.strip()
    assert merged == eng.merge_args("-x", add)
    assert cli("strip-args", stdin=json.dumps({"args": merged, "wanted": add})).stdout.strip() == "-x"
    assert json.loads(cli("check", env={"GPU_ROOT": str(tmp_path)}).stdout)["device"] == 0x46A6
    assert cli("bogus").returncode == 64 and cli("fetch", "nope:x", str(tmp_path)).returncode == 64


def test_main_in_process(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eng, "fetch", lambda url, sha, dest: Path(dest))
    assert eng.main(["x", "fetch", "rom:adl-h", str(tmp_path)]) == 0
    assert eng.main(["x", "fetch", "pin:whateverxe", str(tmp_path)]) == 0
    monkeypatch.setattr(eng, "fetch", lambda *a: (_ for _ in ()).throw(ValueError("bad")))
    assert eng.main(["x", "fetch", "pin:graphics", str(tmp_path)]) == 1
    monkeypatch.setattr(eng, "build_payload", lambda p, o: (True, "ok"))
    assert eng.main(["x", "payload", "a", "b"]) == 0
    monkeypatch.setattr(eng, "build_payload", lambda p, o: (False, "no"))
    assert eng.main(["x", "payload", "a", "b"]) == 1
    monkeypatch.setattr(eng, "read_result", lambda p: "staged")
    assert eng.main(["x", "result", "img"]) == 0 and "staged" in capsys.readouterr().out


def test_the_stage_command_is_short_and_the_script_reports_when_it_starts():
    # typed key by key into Tahoe's slow recovery Terminal: only mount and run, the logic lives in stage.sh
    assert len(eng.STAGE_COMMAND) <= 130 and eng.STAGE_COMMAND.count(";") <= 3
    # recovery's root volume is read-only: the mount point must live under /tmp (mkdir /p fails there)
    assert "mkdir /tmp/p" in eng.STAGE_COMMAND and "mkdir /p" not in eng.STAGE_COMMAND
    script = (Path(eng.__file__).parent / "data/gpu/igpu_stage.sh").read_text().splitlines()
    first = next(i for i, ln in enumerate(script) if ln.startswith("SRC="))
    assert script[first + 2] == 'echo started >"$SRC/result.txt"; sync'


def test_every_character_of_the_stage_command_is_typeable():
    from osx_proxmox_next.unattended import CHARMAP
    for ch in eng.STAGE_COMMAND:
        assert ch.islower() or ch.isupper() or ch.isdigit() or ch in CHARMAP, ch
