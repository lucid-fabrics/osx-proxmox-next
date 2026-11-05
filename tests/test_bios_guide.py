from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

from gpu_fakes import add_card, make_host, write
from osx_proxmox_next import bios_guide as bg

DATA = bg.load_data()
FACTS = {"board_vendor": "ASUSTeK COMPUTER INC.", "board_name": "PRIME X670-P", "cpu_vendor": "amd",
         "virtualization": False, "dmar": False, "ivrs": False, "iommu_groups": 0, "efi": False, "displays": []}


# ── the data file ────────────────────────────────────────────────────

def test_every_vendor_entry_is_complete_and_honest_about_confidence():
    assert set(DATA["settings"]) >= {"vtx", "svm", "vtd", "amd_iommu", "above4g", "csm", "primary_display", "rebar"}
    assert DATA["docs_base"].endswith("/docs/guides/gpu/bios/")
    for key, ven in DATA["vendors"].items():
        assert ven["name"] and ven["doc"] and ven["enter"] and ven["match"], key
        assert ven["paths"], key
        for setting, entry in ven["paths"].items():
            assert setting in DATA["settings"], (key, setting)
            assert set(entry) == {"path", "value", "verified", "source"}, (key, setting)
            assert isinstance(entry["verified"], bool) and entry["source"] and entry["path"] and entry["value"]
            if entry["verified"]:
                assert entry["source"].startswith("https://"), (key, setting)
    verified = [(k, s) for k, v in DATA["vendors"].items() for s, e in v["paths"].items() if e["verified"]]
    assert len(verified) >= 70  # the paths were checked against vendor manuals
    assert not any(k == "mini-pc" for k, _ in verified)  # no official BIOS manual exists for these
    for key, setting in (("asrock-rack", "primary_display"), ("hp", "rebar"), ("lenovo", "rebar")):
        assert DATA["vendors"][key]["paths"][setting]["verified"]
    for group in ("virtualization", "iommu", "above4g", "csm", "primary_display", "rebar"):
        assert DATA["generic"][group]["names"] and DATA["generic"][group]["where"]


def test_no_em_dash_in_the_data():
    assert "—" not in (Path(bg.__file__).parent / "data/gpu/bios_paths.json").read_text()


@pytest.mark.parametrize("vendor,product,board,expect", [
    ("ASUSTeK COMPUTER INC.", "", "PRIME X670-P", "asus"),
    ("Micro-Star International Co., Ltd.", "MS-7E07", "MAG B650", "msi"),
    ("Gigabyte Technology Co., Ltd.", "", "Z790 AORUS ELITE", "gigabyte"),
    ("ASRock", "", "B650M", "asrock"),
    ("ASRockRack", "", "ROMED8-2T", "asrock-rack"),
    ("Supermicro", "", "X13DDW-A", "supermicro"),
    ("Super Micro Computer, Inc.", "", "H12SSL", "supermicro"),
    ("Dell Inc.", "OptiPlex 7090", "0X1", "dell"),
    ("Dell Inc.", "PowerEdge R740", "0Y2", "dell-poweredge"),
    ("HP", "HP Z4 G5", "", "hp"),
    ("Hewlett Packard Enterprise", "ProLiant DL380 Gen10", "", "hpe"),
    ("LENOVO", "ThinkStation P620", "", "lenovo"),
    ("LENOVO", "ThinkSystem SR650", "", "lenovo-thinksystem"),
    ("Intel Corporation", "NUC13ANH", "NUC13ANBi7", "intel-nuc"),
    ("Micro Computer (HK) Tech Limited", "MS-01", "", "mini-pc"),
    ("AZW", "SER7", "", "mini-pc"),
    ("Default string", "Default string", "Default string", "generic"),
])
def test_vendor_matching(vendor, product, board, expect):
    facts = {"board_vendor": vendor, "sys_vendor": vendor, "bios_vendor": "x", "product_name": product, "board_name": board}
    assert bg.match_vendor(facts, DATA) == expect


def test_doc_url():
    assert bg.doc_url(DATA, "dell-poweredge") == DATA["docs_base"] + "dell"
    assert bg.doc_url(DATA, "generic") == DATA["docs_base"] + "generic"
    assert bg.doc_url(DATA, "lenovo-thinksystem").endswith("/lenovo")


# ── what is missing ──────────────────────────────────────────────────

def test_items_for_a_host_that_needs_everything(tmp_path):
    root = Path(make_host(tmp_path))
    add_card(root, boot_vga=True)
    add_dev = __import__("nullmoth_fakes").add_dev
    add_dev(root / "sys", "0000:00:02.0", 0x8086, 0x1234, cls=0x030000, driver="i915", group=3)
    (root / "sys/bus/pci/devices/0000:06:00.0/driver").unlink()  # the host driver holds it
    facts = {**FACTS, "displays": [{"bdf": "0000:06:00.0"}, {"bdf": "0000:00:02.0"}], "bar_errors": ["0000:06:00.0"]}
    items = {i["key"]: i for i in bg.check_items(facts, {"bdf": "0000:06:00", "boot_vga": True}, str(root))}
    assert [k for k, i in items.items() if not i["ok"]] == ["svm", "amd_iommu", "csm", "above4g", "primary_display"]
    intel = {i["key"] for i in bg.check_items({**facts, "cpu_vendor": "intel"}, None, str(root))}
    assert intel == {"vtx", "vtd", "csm"}


def test_items_for_a_ready_host(tmp_path):
    root = Path(make_host(tmp_path))
    add_card(root)
    facts = {**FACTS, "cpu_vendor": "intel", "virtualization": True, "dmar": True, "efi": True,
             "displays": [{"bdf": "0000:06:00.0"}]}
    assert all(i["ok"] for i in bg.check_items(facts, {"bdf": "0000:06:00", "boot_vga": True}, str(root)))
    # boot_vga on the only display adapter is a host-step problem, not a BIOS item
    assert bg.above4g_off(facts, "0000:06:00") is False
    assert bg.above4g_off({"bar_errors": ["0000:07:00.1"]}, "0000:06:00") is False
    # a card already handed to vfio-pci is not the host's screen, even if it was the boot display
    held = {**facts, "displays": [{"bdf": "0000:06:00.0"}, {"bdf": "0000:00:02.0"}]}
    assert all(i["ok"] for i in bg.check_items(held, {"bdf": "0000:06:00", "boot_vga": True}, str(root)))
    assert bg.rebar_capable(str(root), "0000:06:00") is False
    write(root, "/sys/bus/pci/devices/0000:06:00.0/resource0_resize", "0")
    assert bg.rebar_capable(str(root), "0000:06:00") is True


def test_iommu_counts_as_on_when_groups_exist_without_a_table():
    items = bg.check_items({**FACTS, "iommu_groups": 4, "virtualization": True, "efi": True}, None, "/nonexistent")
    assert all(i["ok"] for i in items)


# ── the card ─────────────────────────────────────────────────────────

def card(failing_keys, vendor="asus", **kw):
    items = [{"key": k, "ok": False, "detail": ""} for k in failing_keys]
    return bg.build_card(items, FACTS, DATA, vendor, **kw)


def test_card_is_ascii_boxed_and_fits_the_terminal():
    text = card(["svm", "amd_iommu", "above4g", "csm"], optional_rebar=True)
    assert text.isascii()
    widths = {len(line) for line in text.splitlines()}
    assert widths == {bg.WIDTH} and bg.WIDTH <= 80
    assert text.splitlines()[0] == "+" + "=" * (bg.WIDTH - 2) + "+"
    assert "BIOS STEPS FOR THIS SERVER" in text and "ASUSTEK COMPUTER INC. PRIME X670-P (AMD)" in text
    assert "Take a photo of this screen" in text and "Open the BIOS with: Del or F2." in text
    assert "Back in Proxmox, run:" in text and "osx-next-cli gpu-setup" in text
    assert "lucid-fabrics.github.io/osx-proxmox-next/docs/guides/gpu/bios/asus" in text
    assert "Use the BIOS search" in text and "Resizable BAR (optional)" in text
    assert "Save and exit" in text.replace("\n", " ")


def test_unverified_paths_are_hedged_and_verified_ones_are_not():
    assert "Usually under:" in card(["svm"], "mini-pc")
    assert "Path: " in card(["svm"], "asus") and "Usually under:" not in card(["svm"], "asus")
    dell = bg.build_card([{"key": "vtd", "ok": False, "detail": ""}], FACTS, DATA, "dell")
    assert "Path: Virtualization Support > VT for Direct I/O" in dell and "Usually under: Virtualization" not in dell


def test_vendor_without_an_entry_falls_back_to_generic_names():
    text = bg.build_card([{"key": "primary_display", "ok": False, "detail": ""}], FACTS, DATA, "hpe")
    assert "Look for: Primary Display, Initial Display Output" in text.replace("|", " ").replace("  ", " ") or "Look for:" in text
    generic = bg.build_card([{"key": "csm", "ok": False, "detail": ""}], FACTS, DATA, "generic", resume_cmd="X")
    assert "Look for:" in generic and "Open the BIOS with" not in generic and "bios/generic" in generic


def test_long_lines_wrap_and_the_value_keeps_its_own_line():
    text = card(["primary_display"], "asus")
    assert all(len(line) == bg.WIDTH for line in text.splitlines())
    assert bg._line(20, "A very long label here", "Enabled") == ["A very long label here", "   = Enabled"]


def test_card_title_without_a_board_name():
    assert bg.board_title({"cpu_vendor": "intel"}) == "THIS SERVER (INTEL)"


def test_save_card(tmp_path):
    assert bg.save_card("hello", str(tmp_path)) == "/root/osx-proxmox-next-bios-steps.txt"
    assert (tmp_path / "root/osx-proxmox-next-bios-steps.txt").read_text() == "hello\n"


def test_qr_lines():
    url = bg.doc_url(DATA, "asus")
    unicode_rows = bg.qr_lines(url)
    assert unicode_rows and len({len(r) for r in unicode_rows}) == 1
    ascii_rows = bg.qr_lines(url, ascii_only=True)
    assert set("".join(ascii_rows)) == {"#", " "}
    assert len(ascii_rows) > len(unicode_rows)


def test_qr_without_segno(monkeypatch):
    monkeypatch.setitem(sys.modules, "segno", None)
    assert bg.qr_lines("https://x") == []


# ── reboot into the firmware setup ───────────────────────────────────

def test_firmware_setup_support(tmp_path):
    path = tmp_path / bg.OS_INDICATIONS.lstrip("/")
    assert bg.firmware_setup_supported(str(tmp_path)) is False
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x06\x00\x00\x00" + b"\x01" + b"\x00" * 7)
    assert bg.firmware_setup_supported(str(tmp_path)) is True
    path.write_bytes(b"\x06\x00\x00\x00" + b"\x00" + b"\x00" * 7)
    assert bg.firmware_setup_supported(str(tmp_path)) is False
    path.write_bytes(b"\x06")
    assert bg.firmware_setup_supported(str(tmp_path)) is False
    assert bg.REBOOT_ARGV == ["systemctl", "reboot", "--firmware-setup"]


def test_running_vms():
    listing = "VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n100 a running 4096 32 1\n101 b stopped 4096 32 0\nbad line\n"
    assert bg.parse_running_vms(listing) == [(100, "a")]


# ── command line ─────────────────────────────────────────────────────

def cli(monkeypatch, tmp_path, capsys, cmd, payload):
    monkeypatch.setenv("GPU_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = bg.main(["bios_guide.py", cmd])
    return code, capsys.readouterr().out


def test_cli(monkeypatch, tmp_path, capsys):
    root = Path(make_host(tmp_path))
    add_card(root)
    code, out = cli(monkeypatch, tmp_path, capsys, "vendor", {"facts": FACTS})
    assert (code, out.strip()) == (0, "asus")
    code, out = cli(monkeypatch, tmp_path, capsys, "check", {"facts": FACTS, "target": {"bdf": "0000:06:00", "boot_vga": False}})
    items = json.loads(out)
    assert code == 0 and not [i for i in items if i["key"] == "svm" and i["ok"]]
    code, out = cli(monkeypatch, tmp_path, capsys, "card", {"facts": FACTS, "items": items, "target": {"bdf": "0000:06:00"},
                                                           "save": True, "resume_cmd": "gpu-setup"})
    assert code == 0 and "BIOS STEPS" in out and (tmp_path / "root/osx-proxmox-next-bios-steps.txt").exists()
    ok = [dict(i, ok=True) for i in items]
    assert cli(monkeypatch, tmp_path, capsys, "card", {"facts": FACTS, "items": ok})[0] == 3
    assert cli(monkeypatch, tmp_path, capsys, "url", {"facts": FACTS})[1].strip().endswith("/asus")
    assert cli(monkeypatch, tmp_path, capsys, "url", {"vendor": "msi"})[1].strip().endswith("/msi")
    assert cli(monkeypatch, tmp_path, capsys, "firmware", {})[1].strip() == "no"
    assert cli(monkeypatch, tmp_path, capsys, "running", {"qm_list": "H\n1 a running\n"})[1].strip() == "1 a"
    assert cli(monkeypatch, tmp_path, capsys, "bogus", {})[0] == 64
