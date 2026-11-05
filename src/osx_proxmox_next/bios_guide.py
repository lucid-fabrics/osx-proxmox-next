"""BIOS step of the GPU walkthrough: what is missing, and a card to photograph.

Standard library only (segno is optional, for QR codes) and no imports from this
package: the bash installer embeds this file verbatim (tests/test_gpu_parity.py
diffs the two) together with data/gpu/bios_paths.json, and runs it with
``python3 - <command>``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from pathlib import Path

CARD_FILE = "/root/osx-proxmox-next-bios-steps.txt"
WIDTH = 76
OS_INDICATIONS = "/sys/firmware/efi/efivars/OsIndicationsSupported-8be4df61-93ca-11d2-aa0d-00e098032b8c"
# Longest names first: ASRock Rack before ASRock, PowerEdge before Dell, and so on.
_ORDER = ("asrock-rack", "asrock", "asus", "msi", "gigabyte", "supermicro", "dell-poweredge", "dell", "hpe",
          "hp", "lenovo-thinksystem", "lenovo", "intel-nuc", "mini-pc")


def _p(root, path):
    return Path(root) / path.lstrip("/")


def _read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        return None


def load_data(path=None):
    path = path or os.environ.get("BIOS_DATA") or str(Path(__file__).with_name("data") / "gpu" / "bios_paths.json")
    return json.loads(Path(path).read_text())


def match_vendor(facts, data):
    """Key of the BIOS vendor in *data*, or 'generic' when nothing matches."""
    hay = " ".join(str(facts.get(k, "")) for k in ("board_vendor", "sys_vendor", "bios_vendor", "product_name",
                                                    "board_name")).lower()
    for key in _ORDER:
        for token in data["vendors"].get(key, {}).get("match", []):
            if re.search(r"\b" + re.escape(token), hay):
                return key
    return "generic"


def doc_url(data, key):
    slug = data["vendors"][key]["doc"] if key in data["vendors"] else "generic"
    return data["docs_base"] + slug


# ── what is missing ──────────────────────────────────────────────────

def above4g_off(facts, bdf):
    """True when the kernel could not place one of the card's BARs ("no space for" in dmesg).

    A card with a 256 MiB window works without Above 4G Decoding, so only this kernel message counts."""
    return any(b.startswith(bdf + ".") for b in facts.get("bar_errors", []))


def _driver(root, bdf):
    link = _p(root, "/sys/bus/pci/devices/%s.0/driver" % bdf)
    return link.resolve().name if link.exists() else None


def rebar_capable(root, bdf):
    base = _p(root, "/sys/bus/pci/devices/%s.0" % bdf)
    return any((base / ("resource%d_resize" % i)).exists() for i in range(6))


def check_items(facts, target, root="/"):
    """One entry per BIOS item: key, label, ok, detail. Only failing ones need the card."""
    intel = facts.get("cpu_vendor") == "intel"
    items = [
        {"key": "vtx" if intel else "svm", "ok": bool(facts.get("virtualization")),
         "detail": "The CPU does not report virtualization (vmx or svm)."},
        {"key": "vtd" if intel else "amd_iommu", "ok": bool(facts.get("dmar") or facts.get("ivrs") or facts.get("iommu_groups")),
         "detail": "The firmware does not publish the IOMMU table (DMAR or IVRS)."},
        {"key": "csm", "ok": bool(facts.get("efi")),
         "detail": "This host did not boot in UEFI mode (no /sys/firmware/efi)."},
    ]
    if target:
        bdf = target["bdf"]
        items.append({"key": "above4g", "ok": not above4g_off(facts, bdf),
                      "detail": "The kernel could not find room for the card's memory window."})
        others = [d for d in facts.get("displays", []) if not d["bdf"].startswith(bdf + ".")]
        held = _driver(root, bdf) == "vfio-pci"  # already handed to VMs: the host is not using it as its screen
        items.append({"key": "primary_display", "ok": not (target.get("boot_vga") and others and not held),
                      "detail": "The firmware uses the chosen card as the boot display."})
    return items


# ── the card ─────────────────────────────────────────────────────────

def _entry(data, vendor, key):
    ven = data["vendors"].get(vendor, {})
    return ven.get("paths", {}).get(key)


def _generic_hint(data, key):
    group = {"vtx": "virtualization", "svm": "virtualization", "vtd": "iommu", "amd_iommu": "iommu"}.get(key, key)
    return data["generic"].get(group, {"names": "", "where": ""})


def _line(width, left, value):
    dots = width - len(left) - len(value) - 2
    if dots < 3:
        return [left, "   = " + value]
    return [left + " " + "." * dots + " " + value]


def _wrap(text, width, indent):
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent) or [indent]


def board_title(facts):
    name = " ".join(x for x in (facts.get("board_vendor", ""), facts.get("board_name", "")) if x).strip()
    return ((name or "this server") + " (%s)" % ("Intel" if facts.get("cpu_vendor") == "intel" else "AMD")).upper()


_ORDER_STEPS = (("csm", "CSM off"), ("above4g", "Above 4G Decoding on"), ("rebar", "Resizable BAR on Auto"))
QR_GAP = 3


def order_note(failing_keys, optional_rebar=False):
    """The ordering tip, naming only the settings that are still off. Empty when order cannot matter."""
    keys = set(failing_keys) | ({"rebar"} if optional_rebar else set())
    parts = [label for key, label in _ORDER_STEPS if key in keys]
    return "Do these in this order: " + ", then ".join(parts) + "." if len(parts) > 1 else ""


def build_card(failing, facts, data, vendor, resume_cmd="osx-next-cli gpu-setup", optional_rebar=False, qr=None,
               max_width=None):
    """ASCII-safe photo card (plain text). *failing* are the entries from check_items with ok false.

    *qr* (lines of the guide's QR code) goes inside the frame, so one photo of the screen holds the steps
    and the code. It sits beside the steps when the frame fits in *max_width* columns (no limit when
    None), and under the steps when it does not."""
    qr = list(qr or [])
    qr_w = max((len(q) for q in qr), default=0)
    width = WIDTH if max_width is None else max(48, min(WIDTH, max_width))
    text_w = width - 4
    beside = bool(qr) and (max_width is None or text_w + QR_GAP + qr_w + 4 <= max_width)
    inner = text_w + (qr_w + QR_GAP if beside else 0)
    ven = data["vendors"].get(vendor)
    body = ["BIOS STEPS FOR THIS SERVER", board_title(facts), "",
            "Take a photo of this screen, then reboot into the BIOS.", ""]
    if ven:
        body += _wrap("Open the BIOS with: %s. %s" % (ven["enter"], ven.get("mode", "")), text_w, "")
        body.append("")
    start = len(body)
    n = 0
    for item in failing:
        n += 1
        entry = _entry(data, vendor, item["key"])
        label = data["settings"][item["key"]]["label"]
        value = entry["value"] if entry else "Enabled"
        body += _line(text_w, "%d. %s" % (n, label), value)
        if entry:
            lead = "Path: " if entry["verified"] else "Usually under: "
            body += _wrap(lead + entry["path"], text_w, "   ")
        else:
            hint = _generic_hint(data, item["key"])
            body += _wrap("Look for: " + hint["names"], text_w, "   ")
            body += _wrap(hint["where"], text_w, "   ")
        if item["key"] in ("vtx", "svm"):
            body += _wrap("Without it no VM can start.", text_w, "   ")
    if optional_rebar:
        n += 1
        body += _line(text_w, "%d. Resizable BAR (optional)" % n, "Auto")
        body += _wrap("Leave it on Auto. If the VM will not start, set it to Disabled.", text_w, "   ")
    n += 1
    body.append("%d. Save and exit (usually F10)" % n)
    body.append("")
    body += _wrap(data["search_tip"], text_w, "")
    note = order_note([i["key"] for i in failing], optional_rebar)
    if note:
        body += _wrap(note, text_w, "")
    body += ["", "Back in Proxmox, run:", "   " + resume_cmd, "", "Same steps with pictures on your phone:",
             "   " + re.sub(r"^https?://", "", doc_url(data, vendor))]
    if qr and not beside:
        body += [""] + qr
    elif qr:
        while len(body) < start + len(qr):
            body.append("")
        for i, q in enumerate(qr):
            body[start + i] = body[start + i].ljust(text_w) + " " * QR_GAP + q
    border = "+" + "=" * (inner + 2) + "+"
    rows = [border] + ["| " + ln.ljust(inner) + " |" for ln in body] + [border]
    return "\n".join(rows)


def save_card(text, root="/"):
    path = _p(root, CARD_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    return CARD_FILE


# ── QR ───────────────────────────────────────────────────────────────

def qr_lines(url, ascii_only=False):
    """QR code as text lines (dark module = block). Empty list when segno is not installed."""
    try:
        import segno  # type: ignore
    except ImportError:
        return []
    rows = [list(r) for r in segno.make(url, error="m", micro=False).matrix_iter(border=2)]
    if ascii_only:
        return ["".join("##" if c else "  " for c in r) for r in rows]
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))
    glyph = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    return ["".join(glyph[(top, bot)] for top, bot in zip(a, b)) for a, b in zip(rows[0::2], rows[1::2])]


# ── reboot into the firmware setup ───────────────────────────────────

def firmware_setup_supported(root="/"):
    """True when the firmware allows 'reboot into setup' (OsIndicationsSupported bit 0)."""
    try:
        raw = _p(root, OS_INDICATIONS).read_bytes()
    except OSError:
        return False
    return len(raw) >= 5 and bool(raw[4] & 1)


def parse_running_vms(qm_list):
    out = []
    for line in qm_list.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[2] == "running":
            out.append((int(parts[0]), parts[1]))
    return out


REBOOT_ARGV = ["systemctl", "reboot", "--firmware-setup"]


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    root = os.environ.get("GPU_ROOT", "/")
    data = load_data()
    payload = json.loads(sys.stdin.read() or "{}")
    if cmd == "vendor":
        print(match_vendor(payload["facts"], data))
    elif cmd == "check":
        print(json.dumps(check_items(payload["facts"], payload.get("target"), root)))
    elif cmd == "card":
        facts = payload["facts"]
        failing = [i for i in payload["items"] if not i["ok"]]
        if not failing:
            return 3
        vendor = match_vendor(facts, data)
        rebar = bool(payload.get("target")) and rebar_capable(root, payload["target"]["bdf"])
        text = build_card(failing, facts, data, vendor, payload.get("resume_cmd") or "osx-next-cli gpu-setup", rebar,
                          payload.get("qr"), payload.get("max_width"))
        if payload.get("save"):
            save_card(text, root)
        print(text)
    elif cmd == "url":
        print(doc_url(data, payload.get("vendor") or match_vendor(payload["facts"], data)))
    elif cmd == "firmware":
        print("yes" if firmware_setup_supported(root) else "no")
    elif cmd == "running":
        for vmid, name in parse_running_vms(payload.get("qm_list", "")):
            print("%d %s" % (vmid, name))
    else:
        sys.stderr.write("usage: bios_guide.py vendor|check|card|url|firmware|running\n")
        return 64
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
