"""Intel iGPU passthrough engine: what the host reads and what the VM gets.

Standard library only and no imports from this package: the bash installer embeds this file
verbatim (tests/test_igpu_parity.py diffs the two) and runs it with ``python3``. The pure parts
(device list, option ROM pick, memory layout, VM settings) take plain values so they unit-test
without a host; the parts that touch the host take a ``root`` (default ``/``) and a ``run``
callable like host_setup.py does.

The recipe was proven live on an Alder Lake-P (8086:46a6) host on macOS 13, 14, 15 and 26:

* the host gives up the iGPU (i915 blacklisted, vfio-pci binds it at boot);
* the VM is i440fx (``machine: pc``) with the legacy IGD mode of vfio-pci, ``vga: none``;
* ``max-ram-below-4g`` is sized from the host's stolen-memory layout so the guest's graphics
  memory (the part WhateverXe reads) sits over the host's reservation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

VENDOR = 0x8086
IGD_SLOT = "00:02.0"
IGD_BDF = "0000:" + IGD_SLOT
KVM_DIR = "/usr/share/kvm"
MB = 1024 * 1024

# Pinned downloads (option ROMs, AMFIPass). Every file is checked against its SHA-256 before use and never
# replaced silently: a mismatch is an error. Keep the URLs on tags or commits, never on a moving branch.
# WhateverXe and WhateverXe Graphics are not pinned: they come from the GitHub releases (see "releases" below).
_LONGQT = "https://github.com/LongQT-sea/intel-igpu-passthru/releases/download/v0.1/"
_WEX_GFX = "https://github.com/b00t0x/WhateverXe-Graphics/"
_WEX_GFX_COMMIT = "88d64a340e6f0d52e3f32a136c920d190ac43c53"
ROMS = {
    "adl-h": ("ADL-H_RPL-H_GOPv21_igd.rom", _LONGQT + "ADL-H_RPL-H_GOPv21_igd.rom",
              "8316906f2a391c3c6830d6c311e7cb6436525d34ba28a861127ae1ce649c2b2d"),
    "tgl": ("RKL_TGL_ADL_RPL_GOPv17_igd.rom", _LONGQT + "RKL_TGL_ADL_RPL_GOPv17_igd.rom",
            "9d91bb6a5cc6d7c6dc4ab1783771b77972bf00c17e05d58850638eb389ff1ce4"),
    "adl-n": ("ADL-N_TWL_GOPv21_igd.rom", _LONGQT + "ADL-N_TWL_GOPv21_igd.rom",
              "2d2343fccbbeaf3d7df2280dd25a5f60a0aaf4b8e8ded15997b444d23afe0c3a"),
}
PINS = {
    "amfipass": ("AMFIPass-v1.4.1-Mod-RELEASE.zip",
                 _WEX_GFX + "raw/" + _WEX_GFX_COMMIT + "/extras/AMFIPass-v1.4.1-Mod-RELEASE.zip",
                 "71f41de42e355d2a83e6f093ef34a5b24d0a9c9a9085830b2317f1b874f56250"),
}
AMFIPASS_VERSION = "1.4.1-Mod"

# Memory layout. OVMF puts a constant 65 MB of its own allocations above the guest's graphics
# memory (measured on pve-edk2-firmware, PVE 9.2), WhateverXe compares the guest graphics memory
# top with the host reservation, and Apple's driver treats an opregion in 0xA0000000-0xBFFFFFFF
# as a pre-silicon emulator. Hence the margin, the constant and the two refused ranges.
OVMF_ALLOC_MB = 65
MARGIN_MB = 5
ALIGN_MB = 2
EMULATOR_LOW_MB, EMULATOR_HIGH_MB = 2560, 3072
PCI_HOLE_MB = 3584

PAYLOAD_LABEL = "OSXPNIGPU"
PAYLOAD_MB = 160
LAUNCHD_PLIST = "com.osxproxmoxnext.igpu-firstboot.plist"
# data file name -> name on the payload disk
PAYLOAD_FILES = (("igpu_stage.sh", "stage.sh"), ("igpu_firstboot.sh", "firstboot.sh"),
                 ("igpu_launchd.plist", LAUNCHD_PLIST))
# Recovery on Sequoia does not mount the payload disk by itself (its FAT driver is loaded on demand by
# mount -t msdos), Ventura does. So: find the partition by its label, free it, mount it by hand, run the script.
# Typed key by key into Tahoe's slow recovery Terminal, so it stays as short as it can: all the logic is in stage.sh.
# The mount point is under /tmp: the recovery root volume is read-only, so `mkdir /p` fails and nothing runs.
STAGE_COMMAND = ("d=$(diskutil list|awk '/%s/{print $NF}');diskutil unmount $d;mkdir /tmp/p;mount -t msdos /dev/$d /tmp/p&&sh /tmp/p/stage.sh"
                 % PAYLOAD_LABEL)
BOOT_ARGS = ("-allow3d", "-disablegfxfirmware", "-wex-tgl-hold-render-wake")
GMS_ARG = "0x3"


def default_run(argv, timeout=20):
    """Run a command and return (exit code, stdout+stderr). (127, '') when it cannot start."""
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return res.returncode, (res.stdout or "") + (res.stderr or "")


# ── device list ──────────────────────────────────────────────────────

def data_dir():
    here = Path(__file__).resolve().parent
    return here / "data" / "gpu" if (here / "data" / "gpu").is_dir() else here


def devices_text():
    path = os.environ.get("IGPU_DEVICES") or str(data_dir() / "intel_devices.txt")
    return Path(path).read_text()


def parse_devices(text):
    """({family key: (label, rom key)}, {device id: (family key, status, model)}) from intel_devices.txt."""
    families, devices = {}, {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("|")
        if parts[0] == "family" and len(parts) == 4:
            families[parts[1]] = (parts[2], parts[3])
        elif parts[0] == "device" and len(parts) == 5:
            devices[int(parts[1], 16)] = (parts[2], parts[3], parts[4])
    return families, devices


def list_version(text):
    """The WhateverXe version a device list was built from (its `version|` line), or "unknown"."""
    for line in text.splitlines():
        if line.startswith("version|"):
            return line.split("|", 1)[1].strip() or "unknown"
    return "unknown"


def lookup(device_id, text=None):
    """Everything known about a device id, or None when WhateverXe does not list it."""
    families, devices = parse_devices(text if text is not None else devices_text())
    entry = devices.get(device_id)
    if entry is None:
        return None
    family, status, model = entry
    label, rom = families[family]
    return {"device": device_id, "family": family, "family_label": label, "status": status,
            "model": model, "rom": rom, "rom_file": ROMS[rom][0] if rom in ROMS else ""}


def basis(devices):
    """One plain sentence naming the WhateverXe release a verdict rests on. *devices* is a device_list() result."""
    if devices["source"] == "fallback":
        return "Based on WhateverXe %s, could not check %s." % (devices["version"], devices["wanted"] or "the newest release")
    if devices["version"] == "unknown":
        return "Checked against a custom device list."
    return "Checked against WhateverXe %s." % devices["version"]


def verdict(device_id, text=None, devices=None):
    """(kind, headline, detail): kind is intel, intel-untested or unsupported. *devices* (a device_list() result)
    names the release the verdict rests on; without it the built-in list is used and labelled as such."""
    if text is None and devices is not None:
        text = devices["text"]
    info = lookup(device_id, text)
    note = basis(devices if devices is not None else _builtin() if text is None else _override(text))
    if info is None:
        return ("unsupported", "Not supported by macOS",
                "WhateverXe has no driver support for this Intel graphics chip (it covers Tiger Lake and "
                "Alder, Raptor Lake; older chips use Apple's own drivers and are not offered here). " + note)
    if info["status"] == "nobackend":
        return ("unsupported", "Not supported yet",
                "%s is listed by WhateverXe but has no working backend this tool can use yet. %s"
                % (info["family_label"], note))
    if info["status"] == "tested":
        return ("intel", "Works with WhateverXe (beta) on macOS 13 to 15, Tahoe 26 experimental",
                "%s (%s). Tested end to end. %s" % (info["model"], info["family_label"], note))
    return ("intel-untested", "Should work with WhateverXe (beta), untested on this chip",
            "%s (%s). Only Alder Lake-P 8086:46a6 has been tested through this tool. %s"
            % (info["model"], info["family_label"], note))


# ── device list of a WhateverXe release ──────────────────────────────

# The list of supported chips is not in the release zip (its Info.plist matches on IOResources, not on PCI ids, so
# there is no IOPCIMatch to read). It is the DeviceTable in WhateverXe/kern_xelp_devices.hpp of the release tag:
# exact ids, family, whether an automatic backend exists, and the display name. docs/device-selection.md carries the
# same ids in a human table (ranges like `46D0`-`46D4`, no names, Rocket Lake only in prose), so the header is the
# more reliable source. The header was the same file in every release checked.
# The list is derived per release tag and cached on the host. The bundled intel_devices.txt (a snapshot of an older release) supplies
# what upstream does not say (option ROM per family, the chip tested through this tool) and is the offline fallback.
WEX_REPO = "b00t0x/WhateverXe"
WEX_DEVICES_URL = "https://raw.githubusercontent.com/%s/%%s/WhateverXe/kern_xelp_devices.hpp" % WEX_REPO
DEVICES_CACHE_DIR = "/var/lib/osx-proxmox-next/igpu/devices"
LATEST_TTL = 600
UPSTREAM_FAMILIES = {"TigerLake": "tgl", "AlderLakeN": "adl-n", "RocketLake": "rkl", "AlderLakeS": "adl-s",
                     "AlderLakeP": "adl-p", "RaptorLakeS": "rpl-s", "RaptorLakeP": "rpl-p"}
_ENTRY_RE = re.compile(r'\{\s*0x([0-9A-Fa-f]{4})\s*,\s*Family::(\w+)\s*,\s*Display::\w+\s*,\s*GT::(\w+)\s*,'
                       r'\s*"([^"|\\\r\n]*)"\s*\}')


def parse_header(header):
    """[[device id, upstream family, has backend, model], ...] from kern_xelp_devices.hpp. [] when nothing parses."""
    entries = []
    for m in _ENTRY_RE.finditer(header):
        entries.append([int(m.group(1), 16), m.group(2), m.group(3) != "Unknown", m.group(4)])
    return entries


def _family_label(name):
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def derive_text(entries, version, base_text=None):
    """intel_devices.txt text for a release: *entries* (parse_header) for the ids, models and backend, the base list
    for the option ROM per family and the chip tested through this tool."""
    base = base_text if base_text is not None else devices_text()
    families, devices = parse_devices(base)
    tested = {d for d, (_f, status, _m) in devices.items() if status == "tested"}
    lines = ["version|" + version]
    seen_families = set()
    rows = []
    for device, name, backend, model in entries:
        key = UPSTREAM_FAMILIES.get(name) or "new-" + name.lower()
        if key not in families:
            families[key] = (_family_label(name), "")
        status = "nobackend" if not backend or not families[key][1] else ("tested" if device in tested else "untested")
        seen_families.add(key)
        rows.append("device|%04X|%s|%s|%s" % (device, key, status, model))
    for key in sorted(seen_families):
        lines.append("family|%s|%s|%s" % (key, families[key][0], families[key][1]))
    return "\n".join(lines + rows) + "\n"


def _builtin():
    """The bundled list as a device_list() result, labelled as a fallback."""
    body = devices_text()
    return {"text": body, "version": list_version(body), "tag": "", "source": "fallback", "wanted": "the newest release"}


def _override(text):
    """A device list handed in by the caller (a test, IGPU_DEVICES) as a device_list() result."""
    return {"text": text, "version": list_version(text), "tag": "", "source": "override", "wanted": ""}


def _cache_dir(cache_dir):
    return Path(cache_dir or os.environ.get("IGPU_DEVICES_CACHE") or DEVICES_CACHE_DIR)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        pass  # a read-only host only loses the cache


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def latest_tag_from_redirect(opener=None):
    """The tag github.com/<repo>/releases/latest points at. Not part of the REST API, so the API rate limit (which a
    whole network shares when it has one public address) does not apply. ReleaseError when it does not answer."""
    url = "https://github.com/%s/releases/latest" % WEX_REPO
    request = urllib.request.Request(url, headers={"User-Agent": "osx-proxmox-next"})
    try:
        (opener or urllib.request.build_opener(_NoRedirect).open)(request, timeout=10)
    except urllib.error.HTTPError as exc:
        m = re.search(r"/releases/tag/([^/?#]+)$", exc.headers.get("Location") or "")
        if m and TAG_RE.match(m.group(1)):
            return m.group(1)
    except (OSError, ValueError):
        pass
    raise ReleaseError("Could not find the newest WhateverXe release on GitHub.")


def release_tag(choice="latest", get=None, cache_dir=None, now=None, redirect=None):
    """The release tag a device list is for: the newest published WhateverXe for "latest" (asked of GitHub, the
    answer is remembered for LATEST_TTL seconds), else the tag or version the user picked. ReleaseError offline."""
    import time
    choice = choice or LATEST
    if choice != LATEST:
        if not TAG_RE.match(choice):
            raise ReleaseError("%r is not a release tag." % choice)
        want = choice.lstrip("vV")
        try:
            for pick in list_releases("whateverxe", get):
                if choice in (pick["tag"], pick["version"]) or pick["version"] == want:
                    return pick["tag"]
        except ReleaseError:
            pass  # the API is rate limited or down: the tag as given is tried, a wrong one just fails to download
        return choice
    stamp = _cache_dir(cache_dir) / "latest.json"
    clock = now if now is not None else time.time()
    seen = _read_json(stamp)
    if (isinstance(seen, dict) and isinstance(seen.get("tag"), str) and TAG_RE.match(seen["tag"])
            and isinstance(seen.get("at"), (int, float)) and 0 <= clock - seen["at"] < LATEST_TTL):
        return seen["tag"]
    try:
        found = [p for p in list_releases("whateverxe", get) if not p["prerelease"]]
        if not found:
            raise ReleaseError("No published release of WhateverXe has the file this tool needs.")
        tag = found[0]["tag"]
    except ReleaseError:
        tag = (redirect or latest_tag_from_redirect)()  # the API is rate limited or down: the release page still answers
    _write_json(stamp, {"tag": tag, "at": clock})
    return tag


def fetch_header(tag, opener=None):
    """kern_xelp_devices.hpp of release *tag* as text. ReleaseError when it cannot be read."""
    url = WEX_DEVICES_URL % tag
    try:
        with (opener or urllib.request.urlopen)(urllib.request.Request(url, headers={"User-Agent": "osx-proxmox-next"}),
                                                timeout=20) as resp:  # noqa: S310 - fixed https host, tag is validated
            return resp.read().decode("utf-8")
    except (OSError, ValueError) as exc:
        raise ReleaseError("Could not read the device list of WhateverXe %s (%s)." % (tag, exc)) from exc


def _entry_ok(entry):
    """A cached [device id, upstream family, has backend, model] row of the right types (a hand-edited cache is refetched)."""
    return (isinstance(entry, list) and len(entry) == 4 and isinstance(entry[0], int) and isinstance(entry[1], str)
            and entry[1].isidentifier() and isinstance(entry[2], bool) and isinstance(entry[3], str)
            and "|" not in entry[3] and "\n" not in entry[3] and "\r" not in entry[3])


def release_entries(tag, cache_dir=None, header_get=None):
    """The parsed device table of release *tag*: from the host cache, else fetched once and cached per tag."""
    if not TAG_RE.match(tag):
        raise ReleaseError("%r is not a release tag." % tag)
    path = _cache_dir(cache_dir) / ("%s.json" % tag)
    cached = _read_json(path)
    if isinstance(cached, list) and cached and all(_entry_ok(e) for e in cached):
        return cached
    entries = parse_header((header_get or fetch_header)(tag))
    if not entries:
        raise ReleaseError("The device list of WhateverXe %s has a layout this tool does not know." % tag)
    _write_json(path, entries)
    return entries


def device_list(wex="latest", get=None, header_get=None, cache_dir=None, now=None, redirect=None):
    """The supported chips of the WhateverXe release that will be installed ("latest" or a tag), never raising.

    {"text", "version", "tag", "source": release|fallback|override, "wanted"}. When GitHub cannot be asked the bundled
    list comes back with source "fallback" so every message built on it says which release it is based on.
    IGPU_DEVICES (a file) overrides everything, for tests and for support."""
    override = os.environ.get("IGPU_DEVICES")
    if override:
        return _override(Path(override).read_text())
    try:
        if os.environ.get("IGPU_DEVICES_OFFLINE"):
            raise ReleaseError("offline by request (IGPU_DEVICES_OFFLINE)")
        tag = release_tag(wex, get, cache_dir, now, redirect)
        entries = release_entries(tag, cache_dir, header_get)
    except ReleaseError:
        out = _builtin()
        out["wanted"] = "the newest release" if (wex or LATEST) == LATEST else "release %s" % wex
        return out
    version = tag[1:] if tag[:1] in "vV" else tag
    return {"text": derive_text(entries, version), "version": version, "tag": tag, "source": "release", "wanted": ""}


# ── memory layout ────────────────────────────────────────────────────

def decode_gms(ggc):
    """Bytes of graphics stolen memory from the GGC register (Gen12: GMS is bits 15:8)."""
    gms = (ggc >> 8) & 0xFF
    if gms <= 0x10:
        return gms * 32 * MB
    if 0xF0 <= gms <= 0xFE:
        return (gms - 0xF0 + 1) * 4 * MB
    raise ValueError("unknown GMS value 0x%02x in GGC 0x%04x" % (gms, ggc))


def decode_bdsm(raw):
    """Base of the host's stolen memory: the BDSM register without its lock bit and low bits."""
    return raw & 0xFFF00000


def host_top(bdsm_raw, ggc):
    return decode_bdsm(bdsm_raw) + decode_gms(ggc)


def max_ram_below_4g_mb(top):
    """max-ram-below-4g (MB) that makes the guest's graphics memory cover the host reservation:
    host top + margin + OVMF's own allocations, rounded up to 2 MB."""
    need = top + (MARGIN_MB + OVMF_ALLOC_MB) * MB
    unit = ALIGN_MB * MB
    return -(-need // unit) * ALIGN_MB


def layout(bdsm_raw, ggc):
    """The memory layout for a host, or the reason it cannot be used."""
    try:
        top = host_top(bdsm_raw, ggc)
    except ValueError as exc:
        return {"ok": False, "issue": str(exc)}
    if decode_bdsm(bdsm_raw) == 0:
        return {"ok": False, "issue": "The iGPU reports no stolen memory (BDSM is 0). Check that the iGPU is "
                                      "enabled in the BIOS."}
    mb = max_ram_below_4g_mb(top)
    out = {"ok": True, "host_top": top, "bdsm": decode_bdsm(bdsm_raw), "gms_bytes": decode_gms(ggc),
           "max_ram_below_4g_mb": mb, "issue": ""}
    if EMULATOR_LOW_MB < mb <= EMULATOR_HIGH_MB:
        out.update(ok=False, issue=(
            "This host's graphics memory ends at 0x%X, so the guest would need %d MB of low RAM. That puts "
            "the OpRegion between 2.5 and 3.0 GB, where Apple's driver mistakes the guest for a pre-silicon "
            "emulator. Hosts like this need a WhateverXe patch (b00t0x/WhateverXe issue 57)." % (top, mb)))
    elif mb > PCI_HOLE_MB:
        out.update(ok=False, issue=(
            "This host's graphics memory ends at 0x%X, so the guest would need %d MB of low RAM. That is "
            "above 3.5 GB, where the PCI hole starts." % (top, mb)))
    return out


# ── VM settings ──────────────────────────────────────────────────────

def vm_settings(mb, rom_file, bdf=IGD_BDF):
    """What the VM config gets: machine, display, the passthrough device and the QEMU args to add."""
    return {
        "machine": "pc",
        "vga": "none",
        "hostpci0": "%s,legacy-igd=1,romfile=%s" % (bdf, rom_file),
        "args_add": ["-machine max-ram-below-4g=%dM" % mb,
                     "-set device.hostpci0.x-igd-lpc=on",
                     "-set device.hostpci0.x-igd-gms=" + GMS_ARG],
    }


def _arg_re(arg):
    key = arg.split("=", 1)[0]
    return re.compile(r"\s+".join(re.escape(p) for p in key.split()) + r"=\S+")


def merge_args(args, wanted):
    """*args* with each wanted ``-flag key=value`` set to the wanted value, never duplicated."""
    out = args or ""
    for arg in wanted:
        rx = _arg_re(arg)
        out = rx.sub(lambda _m, a=arg: a, out, count=1) if rx.search(out) else "%s %s" % (out.rstrip(), arg)
    return out.strip()


def strip_args(args, wanted):
    out = args or ""
    for arg in wanted:
        out = re.sub(r"\s*" + _arg_re(arg).pattern, "", out)
    return out.strip()


def recovery_bus(cfg):
    """The slot that holds the recovery disk: sata1 on an i440fx iGPU VM, ide2 on a q35 VM, else None."""
    for slot in ("sata1", "ide2"):
        if slot in cfg:
            return slot
    return None


# ── host checks ──────────────────────────────────────────────────────

def _p(root, path):
    return Path(root) / path.lstrip("/")


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _hex(path):
    text = _read(path)
    try:
        return int(text, 16) if text is not None else None
    except ValueError:
        return None


def read_registers(run=default_run):
    """(BDSM, GGC) of the iGPU from PCI config space, or None when setpci cannot read them."""
    code, out = run(["setpci", "-s", IGD_SLOT, "C0.L", "50.W"])
    words = out.split()
    if code != 0 or len(words) < 2:
        return None
    try:
        return int(words[0], 16), int(words[1], 16)
    except ValueError:
        return None


def bound_driver(root="/"):
    link = _p(root, "/sys/bus/pci/devices/%s/driver" % IGD_BDF)
    return link.resolve().name if link.exists() else None


def check(root="/", run=default_run, text=None, wex="latest", devices=None):
    """Every host-side readiness check for the iGPU: issues refuse, warnings inform. The chip is judged against the
    device list of WhateverXe *wex* ("latest" or a tag), see device_list; report["devices"] says which release."""
    report = {"ok": False, "issues": [], "warnings": [], "hints": [], "device": None, "info": None,
              "layout": None, "driver": None, "devices": None, "basis": "", "verdict": None}
    dev = _p(root, "/sys/bus/pci/devices/%s" % IGD_BDF)
    if not dev.exists():
        report["issues"].append("There is no PCI device at %s. The Intel iGPU must sit at 00:02.0." % IGD_SLOT)
        return report
    vendor, cls = _hex(dev / "vendor"), _hex(dev / "class")
    if vendor != VENDOR:
        report["issues"].append("The device at %s is not an Intel device." % IGD_SLOT)
        return report
    if cls is None or cls >> 8 != 0x0300:
        report["issues"].append("The Intel device at %s is not a VGA display controller." % IGD_SLOT)
        return report
    device = _hex(dev / "device") or 0
    report["device"] = device
    if devices is None:
        devices = _override(text) if text is not None else device_list(wex)
    text = devices["text"]
    report["devices"] = {k: devices[k] for k in ("version", "tag", "source", "wanted")}
    report["basis"] = basis(devices)
    info = lookup(device, text)
    report["info"] = info
    kind, head, detail = verdict(device, text, devices)
    report["verdict"] = [kind, head, detail]
    if kind == "unsupported":
        report["issues"].append("Intel %04x: %s" % (device, detail))
        return report
    if devices["source"] == "fallback":
        report["warnings"].append("The supported-chip list could not be checked against GitHub. %s" % report["basis"])
    if kind == "intel-untested":
        report["warnings"].append(
            "%s (8086:%04x) has not been tested through this tool. Only Alder Lake-P 8086:46a6 has. It should "
            "work: WhateverXe lists it. %s" % (info["family_label"], device, report["basis"]))
    report["driver"] = bound_driver(root)
    if report["driver"] != "vfio-pci":
        report["issues"].append(
            "The host still uses the iGPU (driver: %s). Run gpu-setup, apply the host step and reboot so "
            "vfio-pci holds it." % (report["driver"] or "none"))
    regs = read_registers(run)
    if regs is None:
        report["issues"].append("Could not read the iGPU memory registers with setpci (is pciutils installed?).")
    else:
        report["layout"] = layout(*regs)
        if not report["layout"]["ok"]:
            report["issues"].append(report["layout"]["issue"])
    group = dev / "iommu_group"
    if not group.exists():
        report["issues"].append("IOMMU is not active for the iGPU (no iommu_group). Enable VT-d in the BIOS.")
    report["ok"] = not report["issues"]
    return report


# ── downloads ────────────────────────────────────────────────────────

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, sha256, dest):
    """Download *url* to *dest* unless a file with the pinned hash is already there. Raises on mismatch."""
    dest = Path(dest)
    if dest.exists() and sha256_file(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:  # noqa: S310 - pinned https URL
        shutil.copyfileobj(resp, out)
    got = sha256_file(part)
    if got != sha256:
        part.unlink()
        raise ValueError("checksum mismatch for %s: expected %s, got %s" % (url, sha256, got))
    part.replace(dest)
    return dest


def rom_for_family(family, text=None):
    families, _devices = parse_devices(text if text is not None else devices_text())
    return ROMS[families[family][1]]


# ── WhateverXe releases (GitHub) ─────────────────────────────────────

# WhateverXe and WhateverXe Graphics are never pinned: the newest published release is the default and the user
# may pick any older one. The file of a release is verified against the sha256 `digest` GitHub stores for the
# asset; a release without a digest, or a download that does not match, is an error. There is no silent fallback.
GITHUB_API = "https://api.github.com/repos/"
LATEST = "latest"
RELEASE_KINDS = {
    "whateverxe": ("b00t0x/WhateverXe", re.compile(r"^WhateverXe-.+-RELEASE\.zip$"), "WhateverXe"),
    "graphics": ("b00t0x/WhateverXe-Graphics", re.compile(r"^WhateverXe-Graphics-.+\.pkg$"), "WhateverXe Graphics"),
}
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
STATE_FIELDS = ("whateverxe", "graphics")


class ReleaseError(Exception):
    """GitHub could not be asked, or a release cannot be used. The message says what to do next."""


def github_get(url):
    """The JSON GitHub returns for *url*. GITHUB_TOKEN, when set, raises the rate limit."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "osx-proxmox-next"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise ReleaseError("GitHub refused the request (rate limit reached). Wait a few minutes and run the "
                               "command again, or set GITHUB_TOKEN to a GitHub token to raise the limit.") from exc
        if exc.code == 404:
            raise ReleaseError("GitHub has no such release.") from exc
        raise ReleaseError("GitHub answered HTTP %d for %s. Try again in a few minutes." % (exc.code, url)) from exc
    except (OSError, ValueError) as exc:
        raise ReleaseError("Could not reach GitHub (%s). Check the network and run the command again." % exc) from exc


def _pick(kind, rel):
    """The download of release *rel* for *kind* (None when the release does not carry the file)."""
    repo, rx, _label = RELEASE_KINDS[kind]
    assets = [a for a in rel.get("assets") or [] if rx.match(a.get("name") or "")]
    if len(assets) != 1:
        return None
    asset = assets[0]
    tag = rel.get("tag_name") or ""
    if os.path.basename(asset["name"]) != asset["name"] or not TAG_RE.match(tag):
        return None  # a name or tag that is not a plain word is never put on a path or a command line
    m = DIGEST_RE.match(asset.get("digest") or "")
    return {"kind": kind, "tag": tag, "version": tag[1:] if tag[:1] in "vV" else tag, "name": asset["name"],
            "url": asset.get("browser_download_url") or "", "sha256": m.group(1) if m else "",
            "prerelease": bool(rel.get("prerelease")), "published": rel.get("published_at") or ""}


def list_releases(kind, get=None):
    """Every published release of *kind* that has the file, newest first."""
    repo = RELEASE_KINDS[kind][0]
    data = (get or github_get)("%s%s/releases?per_page=30" % (GITHUB_API, repo))
    picks = [_pick(kind, r) for r in data if not r.get("draft")]
    return sorted((p for p in picks if p), key=lambda p: p["published"], reverse=True)


def _usable(pick):
    """The pick itself, or ReleaseError when it cannot be verified or does not come from the project."""
    label = RELEASE_KINDS[pick["kind"]][2]
    repo = RELEASE_KINDS[pick["kind"]][0]
    if not pick["sha256"]:
        raise ReleaseError("%s %s has no sha256 digest on GitHub, so its download cannot be verified and is "
                           "refused. Pick another release." % (label, pick["tag"]))
    if not pick["url"].startswith("https://github.com/%s/releases/download/" % repo):
        raise ReleaseError("%s %s points at an unexpected download address, refused." % (label, pick["tag"]))
    return pick


def resolve(kind, choice=LATEST, get=None):
    """The release picked by *choice* ("latest" or a tag) with `source` set to latest or chosen."""
    label = RELEASE_KINDS[kind][2]
    choice = choice or LATEST
    if choice == LATEST:
        found = [p for p in list_releases(kind, get) if not p["prerelease"]]
        if not found:
            raise ReleaseError("No published release of %s has the file this tool needs." % label)
        return dict(_usable(found[0]), source=LATEST)
    if not TAG_RE.match(choice):
        raise ReleaseError("%r is not a release tag." % choice)
    want = choice.lstrip("vV")
    for pick in list_releases(kind, get):
        if pick["tag"] == choice or pick["version"] == want:
            return dict(_usable(pick), source="chosen")
    try:
        pick = _pick(kind, (get or github_get)("%s%s/releases/tags/%s" % (GITHUB_API, RELEASE_KINDS[kind][0], choice)))
    except ReleaseError as exc:
        raise ReleaseError("%s has no release tagged %s (%s). See the list with: igpu-versions" % (label, choice, exc)) from exc
    if pick is None:
        raise ReleaseError("%s %s has no download this tool can use." % (label, choice))
    return dict(_usable(pick), source="chosen")


def select_assets(wex=LATEST, graphics=LATEST, get=None):
    """{"whateverxe": pick, "graphics": pick}; a ReleaseError names the one that failed."""
    return {"whateverxe": resolve("whateverxe", wex, get), "graphics": resolve("graphics", graphics, get)}


def fetch_release(pick, dest_dir, fetcher=None):
    """Download *pick* into *dest_dir* (a cached file with the right hash is reused) and return its path."""
    return (fetcher or fetch)(pick["url"], pick["sha256"], Path(dest_dir) / pick["name"])


# ── what is installed, per VM ────────────────────────────────────────

STATE_DIR = "/var/lib/osx-proxmox-next/igpu"


def state_path(vmid, base=None):
    return Path(base or os.environ.get("IGPU_STATE_DIR") or STATE_DIR) / ("%s.json" % int(vmid))


def read_state(vmid, base=None):
    try:
        data = json.loads(state_path(vmid, base).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(vmid, update, base=None):
    """Merge *update* into the VM's record (versions, sources, package state) and return the record."""
    import time
    path = state_path(vmid, base)
    data = read_state(vmid, base)
    data.update(update)
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return data


def picks_state(picks, macos=""):
    """The state fields that name the chosen downloads (see write_state)."""
    out = {"amfipass_version": AMFIPASS_VERSION, "macos": macos}
    for key in STATE_FIELDS:
        if key in picks:
            p = picks[key]
            out.update({key + "_version": p["version"], key + "_tag": p["tag"], key + "_sha256": p["sha256"],
                        key + "_source": p["source"]})
    return out


def describe_state(state):
    """One plain line per installed component, for reports and status output."""
    lines = []
    for key, label in (("whateverxe", "WhateverXe"), ("graphics", "WhateverXe Graphics")):
        if state.get(key + "_version"):
            lines.append("%s %s (%s)" % (label, state[key + "_version"],
                                         "latest when installed" if state.get(key + "_source") == LATEST else "chosen by the user"))
    if state.get("amfipass_version"):
        lines.append("AMFIPass %s (pinned)" % state["amfipass_version"])
    return lines


# ── payload disk ─────────────────────────────────────────────────────

def render_payload_file(name, pkg_sha256):
    """A payload script with the package hash filled in."""
    return (data_dir() / name).read_text().replace("@PKG_SHA256@", pkg_sha256)


def _attach_loop(img, run):
    """Attach *img* to a loop device with its partitions and return the partition node, or ("", message)."""
    code, out = run(["losetup", "-fP", "--show", str(img)], 60)
    loop = out.strip()
    if code != 0 or not loop:
        return "", "could not attach the payload image: " + out.strip()
    part = loop + "p1"
    for _ in range(10):
        run(["partprobe", loop], 20)
        if run(["test", "-b", part], 5)[0] == 0:
            return loop, part
    run(["losetup", "-d", loop], 20)
    return "", "the payload partition never appeared"


def _release(mnt, loop, run):
    """Unmount, remove the mount folder and free the loop device. A folder that is still a mount point is left
    alone: removing it recursively would delete the files of the payload image itself."""
    code, _msg = run(["umount", mnt], 60)
    if code == 0:
        os.rmdir(mnt)
        run(["losetup", "-d", loop], 20)


def build_payload(pkg, out, run=default_run):
    """Build the payload disk: a GPT disk with one FAT32 partition (graphics package, stage and first-boot
    scripts, LaunchDaemon). macOS recovery mounts it as /Volumes/OSXPNIGPU; a bare FAT32 volume without a
    partition table mounted on Ventura but not on Sequoia. Returns (ok, message)."""
    pkg_sha = sha256_file(pkg)
    out = Path(out)
    with open(out, "wb") as f:
        f.truncate(PAYLOAD_MB * MB)
    for argv in (["sgdisk", "-Z", str(out)], ["sgdisk", "-n", "1:0:0", "-t", "1:0700", "-c", "1:" + PAYLOAD_LABEL, str(out)]):
        code, msg = run(argv, 60)
        if code != 0:
            return False, "sgdisk failed: " + msg.strip()
    loop, part = _attach_loop(out, run)
    if not loop:
        return False, part
    code, msg = run(["mkfs.vfat", "-F", "32", "-n", PAYLOAD_LABEL, part], 120)
    if code != 0:
        run(["losetup", "-d", loop], 20)
        return False, "mkfs.vfat failed: " + msg.strip()
    mnt = tempfile.mkdtemp(prefix="igpu-payload-")
    code, msg = run(["mount", part, mnt], 60)
    if code != 0:
        os.rmdir(mnt)
        run(["losetup", "-d", loop], 20)
        return False, "could not mount the payload image: " + msg.strip()
    try:
        shutil.copyfile(pkg, os.path.join(mnt, "WhateverXe-Graphics.pkg"))
        for src, name in PAYLOAD_FILES:
            (Path(mnt) / name).write_text(render_payload_file(src, pkg_sha))
    finally:
        run(["sync"], 120)
        _release(mnt, loop, run)
    return True, "payload disk built (%d MB)" % PAYLOAD_MB


def read_result(img, run=default_run):
    """The text the guest left in result.txt on the payload disk, or '' (the guest must not be writing it)."""
    loop, part = _attach_loop(img, run)
    if not loop:
        return ""
    mnt = tempfile.mkdtemp(prefix="igpu-result-")
    code, _msg = run(["mount", "-o", "ro", part, mnt], 60)
    if code != 0:
        os.rmdir(mnt)
        run(["losetup", "-d", loop], 20)
        return ""
    try:
        return _read(os.path.join(mnt, "result.txt")) or ""
    finally:
        _release(mnt, loop, run)


# ── acceleration check (host side) ───────────────────────────────────

# Gen12 registers in BAR0 that a guest with working acceleration leaves non-zero: the blitter and render
# ring tails (a ring nobody submitted to stays at 0) and the first plane's surface address (display on).
ACCEL_REGS = {"BCS_TAIL": 0x22030, "RCS_TAIL": 0x2030, "PLANE_SURF_1_A": 0x7019C}
ACCEL_TIMEOUT = 300
ACCEL_POLL = 10
BAR0_SIZE = 16 << 20


def read_bar_registers(path, offsets=None):
    """Read the registers (name -> 32-bit value) from the BAR0 file at *path*, read-only."""
    import mmap
    import struct
    offsets = offsets or ACCEL_REGS
    fd = os.open(path, os.O_RDONLY | os.O_SYNC)
    try:
        size = min(os.fstat(fd).st_size or BAR0_SIZE, BAR0_SIZE)
        m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
        try:
            return {k: struct.unpack_from("<I", m, o)[0] for k, o in offsets.items()}
        finally:
            m.close()
    finally:
        os.close(fd)


def parse_accel(regs):
    """(active, detail) from register values. The GPU counts as in use when either ring tail is non-zero."""
    detail = ", ".join("%s=%#x" % (k, regs.get(k, 0)) for k in ACCEL_REGS)
    # a powered-down or reset device reads all ones from BAR0: that is no GPU work
    tails = [regs.get(k, 0) for k in ("BCS_TAIL", "RCS_TAIL")]
    if all(v == 0xFFFFFFFF for v in regs.values()) and regs:
        return False, detail + " (the iGPU is not responding)"
    return any(v not in (0, 0xFFFFFFFF) for v in tails), detail


def wait_accel(read, timeout=ACCEL_TIMEOUT, poll=ACCEL_POLL, clock=None, sleep=None):
    """Poll *read* (a callable returning register values) until the GPU shows work or *timeout* seconds pass.
    Returns (active, detail of the last look)."""
    import time
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + timeout
    detail = "no reading"
    while True:
        try:
            active, detail = parse_accel(read())
        except OSError as exc:
            active, detail = False, "cannot read the iGPU registers: %s" % exc
        if active:
            return True, detail
        if clock() >= deadline:
            return False, detail
        sleep(poll)


def bar0_path(root=None):
    return os.path.join(root or os.environ.get("GPU_SYSFS", "/sys"), "bus/pci/devices", IGD_BDF, "resource0")


def check_accel(timeout=ACCEL_TIMEOUT, root=None, clock=None, sleep=None):
    """Wait for the running guest to use the iGPU. Returns (active, detail)."""
    path = bar0_path(root)
    return wait_accel(lambda: read_bar_registers(path), timeout, ACCEL_POLL, clock, sleep)


# ── command line (used by the bash installer) ────────────────────────

def _emit(obj):
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _usage():
    sys.stderr.write("usage: igpu_engine.py check [wex]|verdict <id> [wex]|lookup <id> [wex]|devices [wex]|settings <mb> <rom>|merge-args|strip-args|"
                     "fetch <rom:KEY|pin:NAME> <dir>|assets <dir> [wex] [graphics]|releases <whateverxe|graphics>|"
                     "state-write <vmid>|state-record <vmid> <macos>|state-show <vmid>|payload <pkg> <img>|result <img>|accel-check [seconds]|stage-command|pins\n")
    return 64


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    root = os.environ.get("GPU_ROOT", "/")
    if cmd == "check" and len(argv) in (2, 3):
        _emit(check(root, wex=argv[2] if len(argv) == 3 else "latest"))
        return 0
    if cmd == "verdict" and len(argv) in (3, 4):
        devices = device_list(argv[3] if len(argv) == 4 else "latest")
        _emit(dict(zip(("kind", "headline", "detail"), verdict(int(argv[2], 16), devices["text"], devices))))
        return 0
    if cmd == "lookup" and len(argv) in (3, 4):
        devices = device_list(argv[3] if len(argv) == 4 else "latest")
        _emit(lookup(int(argv[2], 16), devices["text"]) or {})
        return 0
    if cmd == "devices" and len(argv) in (2, 3):
        devices = device_list(argv[2] if len(argv) == 3 else "latest")
        _emit(dict({k: v for k, v in devices.items() if k != "text"}, basis=basis(devices)))
        return 0
    if cmd == "settings" and len(argv) == 4:
        _emit(vm_settings(int(argv[2]), argv[3]))
        return 0
    if cmd in ("merge-args", "strip-args"):
        data = json.loads(sys.stdin.read())
        fn = merge_args if cmd == "merge-args" else strip_args
        sys.stdout.write(fn(data["args"], data["wanted"]) + "\n")
        return 0
    if cmd == "fetch" and len(argv) == 4:
        kind, _, key = argv[2].partition(":")
        entry = ROMS.get(key) if kind == "rom" else PINS.get(key)
        if entry is None:
            sys.stderr.write("unknown download %s\n" % argv[2])
            return 64
        try:
            path = fetch(entry[1], entry[2], Path(argv[3]) / entry[0])
        except (OSError, ValueError) as exc:
            sys.stderr.write("ERROR: %s\n" % exc)
            return 1
        sys.stdout.write(str(path) + "\n")
        return 0
    if cmd == "assets" and len(argv) in (3, 4, 5):
        try:
            picks = select_assets(*argv[3:])
            out = {k: dict(p, path=str(fetch_release(p, argv[2]))) for k, p in picks.items()}
        except (ReleaseError, OSError, ValueError) as exc:
            sys.stderr.write("ERROR: %s\n" % exc)
            return 1
        _emit(out)
        return 0
    if cmd == "releases" and len(argv) == 3 and argv[2] in RELEASE_KINDS:
        try:
            _emit(list_releases(argv[2]))
        except ReleaseError as exc:
            sys.stderr.write("ERROR: %s\n" % exc)
            return 1
        return 0
    if cmd == "state-write" and len(argv) == 3:
        _emit(write_state(argv[2], json.loads(sys.stdin.read())))
        return 0
    if cmd == "state-record" and len(argv) == 4:
        picks = json.loads(sys.stdin.read())
        write_state(argv[2], picks_state(picks, argv[3]))
        return 0
    if cmd == "state-show" and len(argv) == 3:
        sys.stdout.write("\n".join(describe_state(read_state(argv[2]))) + "\n")
        return 0
    if cmd == "payload" and len(argv) == 4:
        ok, msg = build_payload(argv[2], argv[3])
        sys.stderr.write(msg + "\n")
        return 0 if ok else 1
    if cmd == "result" and len(argv) == 3:
        sys.stdout.write(read_result(argv[2]) + "\n")
        return 0
    if cmd == "accel-check" and len(argv) in (2, 3):
        active, detail = check_accel(int(argv[2]) if len(argv) == 3 else ACCEL_TIMEOUT)
        sys.stdout.write(("active" if active else "inactive") + ": " + detail + "\n")
        return 0 if active else 1
    if cmd == "stage-command":
        sys.stdout.write(STAGE_COMMAND + "\n")
        return 0
    if cmd == "pins":
        _emit({"roms": {k: list(v) for k, v in ROMS.items()}, "pins": {k: list(v) for k, v in PINS.items()}})
        return 0
    return _usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
