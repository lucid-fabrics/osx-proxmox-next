"""The guided GPU passthrough walkthrough: state, scan, and one method per step.

Both front ends (the CLI in gpu_setup_cli.py and the TUI panel in _gpu_mixin.py)
drive a Session. The host changes are done by host_setup.py and the BIOS card by
bios_guide.py (standalone modules the bash installer embeds); VM changes go
through nullmoth.py (NVIDIA) and gpu_attach.py (AMD).
"""

from __future__ import annotations

import re
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from collections.abc import Callable, Iterable

from . import amd_host, bios_guide, gpu, gpu_attach, host_setup, igpu_attach, intel_host, nullmoth, nvidia_host
from .gpu import AttachRecord, load_record, parse_config
from .host_setup import STEPS, default_run
from .infrastructure import ProxmoxAdapter
from .services import fetch_vm_info, get_proxmox_adapter, list_macos_vms

__all__ = [
    "CardChanged",
    "GpuCard",
    "VmOption",
    "Session",
    "TROUBLESHOOTING",
    "card_text",
    "docs_url",
    "render_rail",
    "verdict",
]

RESUME_CMD = "osx-next-cli gpu-setup"
# (value, label, one-line description) of the AMD display choice, shared by the CLI and the TUI
DISPLAY_OPTIONS = (
    ("gpu", "Monitor on the GPU", "Best performance. Plug a screen into the card; the Proxmox console goes blank."),
    ("console", "Keep the Proxmox console", "The card speeds up apps and you keep using the web console."),
)
# NVIDIA has no such choice (the console always goes black), so it gets this note instead
NVIDIA_DISPLAY_NOTE = ("NVIDIA: the Proxmox console goes black once the driver loads. Turn on Remote Login and Screen "
                       "Sharing first, and plug a monitor or a dummy plug into the card.")
DOCS_ROOT = "https://lucid-fabrics.github.io/osx-proxmox-next/docs/guides/"
NULLMOTH_DOC = DOCS_ROOT + "nvidia-nullmoth"
SUPPORTED_DOC = DOCS_ROOT + "gpu-passthrough#supported-cards"
NEXT_NVIDIA = (f"Next, in macOS: install the NullMoth driver yourself (guide: {NULLMOTH_DOC}). "
               "We do not ship or support it.")
NEXT_AMD = "Next: start the VM. macOS uses the card with its own driver."
INTEL_DISPLAY_NOTE = ("Intel iGPU: the host gives it up for good (no local console), and the VM needs a monitor or an "
                      "HDMI dummy plug on the host's native HDMI port. LSPCON HDMI and USB-C ports do not work.")
INTEL_DOC = DOCS_ROOT + "intel-igpu-passthrough"
NEXT_INTEL = ("Next: plug a monitor or an HDMI dummy plug into the native HDMI port and start the VM. "
              "The graphics package installs itself on the first boot.")
VENDOR_NAMES = {"amd": "AMD", "nvidia": "NVIDIA", "intel": "Intel"}
VENDOR_IDS = {"amd": 0x1002, "nvidia": 0x10DE, "intel": 0x8086}
_PRE_TURING = ("Older than the RTX 20 series: NVIDIA's macOS drivers stopped at High Sierra and NullMoth "
               "needs Turing or newer.")
_BLACK_BOX = {
    "no_signal": (
        "No signal on the monitor:\n"
        "  1. Use an HDMI cable first. DisplayPort outputs have stayed black on several cards.\n"
        "  2. Plug the monitor into the card, not the motherboard, and power it on before the VM starts.\n"
        "  3. The VM must have 'vga none' (the display setting in the VM step) and the card must be on the\n"
        "     PCI Express port. Run gpu-setup again and pick 'Monitor on the GPU'.\n"
        "  4. Reset the OpenCore NVRAM from the picker (Reset NVRAM entry) and start the VM again."),
    "black_after_logo": (
        "Black screen after the Apple logo:\n"
        "  1. Check that the VM is not using the emulated display: gpu-setup shows 'vga none' for it.\n"
        "  2. Navi cards need agdpmod=pikera in the boot-args. This tool adds it; check the VM's OpenCore\n"
        "     disk was patched (gpu-setup --status).\n"
        "  3. Turn Resizable BAR off in the BIOS (some RX 6600 cards stay black with it on).\n"
        "  4. Try HDMI instead of DisplayPort."),
    "once_per_boot": (
        "Works once, then fails until the host reboots (the AMD reset bug):\n"
        "  1. Polaris, Vega and Navi 10/14 need the vendor-reset module. Run gpu-setup and let the Host step\n"
        "     install it. With Secure Boot on it cannot be installed automatically (the Host step explains why).\n"
        "  2. Shut the VM down from inside macOS (Apple menu > Shut Down), not with 'qm stop'.\n"
        "  3. If it still fails, reboot the host between VM runs."),
}
TROUBLESHOOTING = _BLACK_BOX


@dataclass
class GpuCard:
    bdf: str
    vendor: str  # "amd" | "nvidia" | "intel"
    device_id: int
    name: str
    kind: str  # native | nullmoth | intel | intel-untested | unsupported
    headline: str
    detail: str
    boot_vga: bool = False
    host_console: bool = False  # boot_vga AND a host display driver or a firmware framebuffer holds the card
    driver: str | None = None
    users: list[tuple[int, bool]] = field(default_factory=list)
    shares: list[str] = field(default_factory=list)
    risky: list[str] = field(default_factory=list)  # storage or network devices in the same IOMMU group
    lxc: list[tuple[int, str]] = field(default_factory=list)  # containers that use the host GPU driver nodes
    model: str = ""  # marketing name from the device list (Intel iGPU only)
    macos: str = ""  # the macOS release the verdict below is for
    works: bool = False  # supported for that release and nothing running holds it
    status: str = ""  # "works with macOS Sequoia 15", "not supported by macOS ...", "in use by VM 100"
    recommended: bool = False  # the strongest card that works: preselected, still used only after a confirmation

    @property
    def prepared(self) -> bool:
        return self.driver == "vfio-pci"

    @property
    def detected_line(self) -> str:
        """One line for the recommended card: what was found and what it costs."""
        model = self.model or "Intel graphics"
        reboot = "" if self.prepared else " One host reboot needed."
        return (f"{model} ({self.device_id:04x}) detected: full macOS acceleration. "
                f"The host gives up its local screen.{reboot}")

    @property
    def running_users(self) -> list[int]:
        return [vmid for vmid, running in self.users if running]

    @property
    def stopped_users(self) -> list[int]:
        return [vmid for vmid, running in self.users if not running]

    @property
    def selectable(self) -> bool:
        """A supported card that nothing running holds. A stopped VM that lists it only warns."""
        return self.kind != "unsupported" and not self.running_users and not self.risky

    @property
    def notes(self) -> list[str]:
        """Warnings shown on the card: stopped VMs that also list it, and the firmware boot screen."""
        out = [gpu.shared_note(vmid) for vmid in self.stopped_users]
        if self.boot_vga and not self.host_console:
            out.append("The firmware shows its boot screen on this card.")
        return out

    @property
    def why_not(self) -> str:
        if self.kind == "unsupported":
            return self.detail
        reasons = []
        if self.risky:
            reasons.append("Its IOMMU group also holds storage or network devices (" + ", ".join(self.risky)
                           + "). Passing the card through would take them from the host. Move the card to another slot.")
        if self.running_users:
            reasons.append("In use by " + ", ".join(f"VM {vmid} (running)" for vmid in self.running_users))
        return " ".join(reasons)


_MACOS_NUMBER = {"ventura": "13", "sonoma": "14", "sequoia": "15", "tahoe": "26"}
MACOS_NAMES = {"ventura": "macOS Ventura 13", "sonoma": "macOS Sonoma 14", "sequoia": "macOS Sequoia 15",
               "tahoe": "macOS Tahoe 26"}
DEFAULT_MACOS = "sequoia"
# a supported AMD card beats the Intel iGPU, which beats an NVIDIA card (beta, third-party driver)
STRENGTH = {"amd": 3, "intel": 2, "nvidia": 1}


def version_status(vendor: str, device_id: int, kind: str, macos: str) -> tuple[bool, str]:
    """(works, one-line status) of a card for one macOS release. Reuses the AMD, NVIDIA and Intel support data."""
    name = MACOS_NAMES[macos]
    if kind == "unsupported":
        return False, f"not supported by {name}"
    if vendor == "nvidia" and macos not in ("sequoia", "tahoe"):
        return False, f"not supported by {name} (NullMoth needs macOS 15.5 or newer)"
    if vendor == "amd":
        fam = amd_host.family_for(device_id)
        if macos == "tahoe" and fam is not None and fam.tahoe != "ok":
            return True, f"works with {name} (not confirmed on this family)"
    if vendor == "intel" and macos == "tahoe":
        return True, f"works with {name} (experimental: restart from Proxmox)"
    return True, f"works with {name}"


def verdict(vendor: str, device_id: int) -> tuple[str, str, str]:
    """(kind, headline, detail) for one card. The headline is the plain-words verdict."""
    if vendor == "amd":
        fam = amd_host.family_for(device_id)
        if fam is None:
            return "unsupported", "Not supported by macOS", amd_host.unsupported_reason(device_id)
        span = "macOS 13 to 26" if fam.tahoe == "ok" else "macOS 13 to 15 (26 not confirmed)"
        return "native", f"Works natively on {span}", fam.label
    if vendor == "intel":
        return intel_host.verdict(device_id)
    if nvidia_host.is_supported_device(device_id):
        return ("nullmoth", "Works with NullMoth (beta) on macOS 15.5 to 26",
                "Needs the third-party NullMoth driver, which you install yourself.")
    return "unsupported", "Not supported by macOS", _PRE_TURING


def docs_url(page: str) -> str:
    return DOCS_ROOT + page


CARD_WIDTH = 76


def card_lines(card: GpuCard, width: int = CARD_WIDTH) -> list[str]:
    """The text lines inside one scan card. A card that cannot be used ends with ``Can't use: <reason>``."""
    flags = []
    if card.host_console:
        flags.append("host console")
    if card.driver:
        flags.append(f"bound to {card.driver}")
    flags += [f"container {ctid} uses {node}" for ctid, node in card.lxc]
    if card.shares and not card.risky:
        flags.append("shares IOMMU group with " + ", ".join(card.shares))
    chip = f"{VENDOR_IDS[card.vendor]:04x}:{card.device_id:04x}"
    lines = [card.name, f"{card.bdf}   {VENDOR_NAMES[card.vendor]} {chip}"]
    if card.status:
        lines.append(("For this VM: " if not card.macos else "") + card.status
                     + (" (recommended)" if card.recommended else ""))
    if card.recommended and card.vendor == "intel":
        lines.append("Recommended: " + card.detected_line)
    if card.kind != "unsupported":
        lines.append(card.headline)
        lines.append(card.detail)
    if flags:
        lines.append("Status: " + ", ".join(flags))
    if card.kind != "unsupported":
        lines += ["Note: " + n for n in card.notes]
    if not card.selectable:
        lines.append("Can't use: " + card.why_not)
    inner = width - 7
    out: list[str] = []
    for ln in lines:
        out += textwrap.wrap(ln, inner) or [""]
    return out


def card_text(card: GpuCard, width: int = CARD_WIDTH) -> str:
    """One scan card in a full-width plain box (ASCII, safe on a serial console).

    A usable card has a ``>`` marker at the left of its top line and a heavy frame; one that cannot be
    used has a plain frame and a dimmed look in the TUI."""
    inner = width - 7
    mark = ">>" if card.selectable else "  "
    rule = ("=" if card.selectable else "-") * (width - 5)
    body = [f"{mark} | {ln.ljust(inner)} |" for ln in card_lines(card, width)]
    return "\n".join([f"{mark} +{rule}+"] + body + [f"{mark} +{rule}+"])


def render_rail(state: dict) -> str:
    """Two lines: the step names, and what each is."""
    names, marks = [], []
    for idx, name in enumerate(STEPS, start=1):
        label = f"[{idx} {name}]"
        if idx in state["skipped"]:
            mark = "skipped"
        elif idx in state["done"]:
            mark = "done"
        elif idx == state["step"]:
            mark = "<now>"
        else:
            mark = ""
        names.append(label)
        marks.append(mark.center(len(label)))
    return " " + " ".join(names) + "\n" + " " + " ".join(marks).rstrip()


class CardChanged(Exception):
    """The device at the saved address is not the card that was chosen (the PCI layout changed)."""


@dataclass
class VmOption:
    vmid: int
    name: str
    macos: str  # "15", "26" ... or "" when unknown
    status: str
    eligible: bool
    why: str = ""

    @property
    def label(self) -> str:
        base = f"{self.vmid:<5} {self.name or '(no name)'}  " + (f"macOS {self.macos}  " if self.macos else "") + self.status
        return base + ("" if self.eligible else f"  ({self.why})")


class Session:
    """One run of the walkthrough. All host access goes through *root*, *run* and *adapter*."""

    def __init__(
        self,
        root: str = "/",
        run: Callable = default_run,
        adapter: ProxmoxAdapter | None = None,
        resume_cmd: str = RESUME_CMD,
    ) -> None:
        self.root = root
        self.run = run
        self.undo_kept = 0  # files and OpenCore configs the last undo left as the user had them
        self.adapter = adapter
        self.sysfs = Path(root) / "sys"
        self.undo_nothing = False  # the last undo found no record at all
        self.state = host_setup.load_state(root) or host_setup.new_state()
        self.state["resume_cmd"] = resume_cmd

    # ── plumbing ─────────────────────────────────────────────────────

    @property
    def pve(self) -> ProxmoxAdapter:
        if self.adapter is None:
            self.adapter = get_proxmox_adapter()
        return self.adapter

    def save(self) -> None:
        host_setup.save_state(self.state, self.root)

    def rail(self) -> str:
        return render_rail(self.state)

    def complete(self, step: int, skipped: bool = False) -> None:
        """Mark *step* done (or skipped) and move to the next one."""
        host_setup.complete_step(self.state, step, skipped)
        self.save()

    def goto(self, step: int) -> None:
        host_setup.goto_step(self.state, step)
        self.save()

    def pause(self) -> str:
        """Save the state and leave the login hint. Returns the resume command."""
        self.save()
        host_setup.set_login_hint(self.state, self.root)
        return self.state["resume_cmd"]

    def restart(self) -> None:
        """Begin a new run after a finished one. What was changed before stays recorded for --undo."""
        keep = {k: self.state[k] for k in ("host_changes", "vm_records", "resume_cmd", "vendor_reset_installed")}
        self.state = {**host_setup.new_state(), **keep}
        self.save()

    # ── 1. scan ──────────────────────────────────────────────────────

    def facts(self) -> dict:
        return host_setup.detect_facts(self.root, self.run)

    def summary_fields(self, facts: dict | None = None) -> list[str]:
        f = facts or self.facts()
        board = " ".join(x for x in (f["board_vendor"], f["board_name"]) if x) or "unknown board"
        ver = re.search(r"pve-manager/(\d+\.\d+)", f["pve"] or "")
        fields = [f"CPU: {f['cpu_vendor'].upper()}", f"Board: {board}", f"Boot loader: {f['bootloader']}"]
        if ver:
            fields.append(f"PVE {ver.group(1)}")
        fields += [f"IOMMU: {'on' if f['iommu_groups'] else 'off'}", f"Boot: {'UEFI' if f['efi'] else 'CSM/legacy'}"]
        return fields

    def summary_line(self, facts: dict | None = None) -> str:
        return "   ".join(self.summary_fields(facts))

    def _lspci_name(self, bdf: str) -> str:
        code, out = self.run(["lspci", "-nn", "-s", bdf + ".0"])
        m = re.search(r"\]:\s*(.*?)\s*\[[0-9a-f]{4}:[0-9a-f]{4}\]", out) if code == 0 else None
        return m.group(1) if m else ""

    def scan(self, exclude_vm: int = -1, macos: str = DEFAULT_MACOS) -> list[GpuCard]:
        facts = self.facts()
        devices = {d["bdf"]: d for d in facts["pci"]}
        seen: list[str] = []
        cards: list[GpuCard] = []
        for disp in facts["displays"]:
            bdf = disp["bdf"].rsplit(".", 1)[0]
            vendor = {0x1002: "amd", 0x10DE: "nvidia", 0x8086: "intel"}.get(disp["vendor"])
            if vendor is None or bdf in seen:
                continue
            seen.append(bdf)
            kind, headline, detail = verdict(vendor, disp["device"])
            funcs = sorted(b for b in devices if b.startswith(bdf + "."))
            first = devices.get(bdf + ".0", disp)
            shares, risky = [], []
            if first["group"]:
                for other in facts["pci"]:
                    if other["group"] == first["group"] and other["bdf"] not in funcs and (other["class"] or 0) >> 8 not in (0x0604, 0x0600):
                        shares.append(other["bdf"])
                        if (other["class"] or 0) >> 16 in (0x01, 0x02):
                            risky.append(other["bdf"])
            containers = [(c, n) for c, n in host_setup.lxc_gpu_users(self.root) if n == "/dev/dri" or vendor == "nvidia"]
            info = intel_host.lookup(disp["device"]) if vendor == "intel" else None
            cards.append(GpuCard(
                model=info["model"] if info else "",
                bdf=bdf, vendor=vendor, device_id=disp["device"], name=self._lspci_name(bdf) or f"{vendor.upper()} GPU",
                kind=kind, headline=headline, detail=detail, boot_vga=bool(first["boot_vga"]), driver=first["driver"],
                host_console=gpu.is_host_console(self.sysfs / "bus/pci/devices" / f"{bdf}.0",
                                                 Path(self.root) / "proc/iomem"),
                users=gpu.gpu_users(self.pve, bdf, exclude_vm, self.root) if kind != "unsupported" else [],
                shares=shares, risky=risky, lxc=containers if kind != "unsupported" else [],
            ))
        macos = macos if macos in MACOS_NAMES else DEFAULT_MACOS
        for card in cards:
            card.macos = macos
            card.works, card.status = version_status(card.vendor, card.device_id, card.kind, macos)
            if card.running_users:
                card.works, card.status = False, "in use by " + ", ".join(f"VM {v}" for v in card.running_users)
            card.works = card.works and card.selectable
        best = max((c for c in cards if c.works), key=lambda c: STRENGTH[c.vendor], default=None)
        if best is not None:
            best.recommended = True
        return sorted(cards, key=lambda c: (not c.selectable, not c.works, not c.recommended))  # usable, working, best first

    # ── 2. choose ────────────────────────────────────────────────────

    def macos_for(self, vmid: int | None, wanted: str = "") -> str:
        """The release a card is judged for: the --macos flag, else the chosen VM's, else Sequoia."""
        if wanted in MACOS_NAMES:
            return wanted
        if vmid is not None:
            info = fetch_vm_info(vmid, adapter=self.pve)
            version = nullmoth.detect_macos(gpu.parse_config(info.config_raw)) if info else None
            if version in MACOS_NAMES:
                return version
        return DEFAULT_MACOS

    def macos_vms(self) -> list[str]:
        return list_macos_vms()

    def vm_options(self, vendor: str) -> list[VmOption]:
        """The macOS VMs made by this tool, with whether each can take a card of *vendor* right now."""
        out: list[VmOption] = []
        for line in self.macos_vms()[1:]:
            parts = line.split()
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            vmid, name, status = int(parts[0]), parts[1], parts[2]
            info = fetch_vm_info(vmid, adapter=self.pve)
            version = nullmoth.detect_macos(gpu.parse_config(info.config_raw)) if info else None
            macos = _MACOS_NUMBER.get(version or "", "")
            why = ""
            if status == "running":
                why = "running: stop it first"
            elif vendor == "nvidia" and version not in ("sequoia", "tahoe"):
                why = f"macOS {macos}: NullMoth needs 15.5+" if macos else "macOS version unknown: NullMoth needs 15.5+"
            elif vendor == "intel" and info and info.config_raw:
                why = next(iter(igpu_attach.vm_issues(vmid, gpu.parse_config(info.config_raw), False, False)), "")
            out.append(VmOption(vmid, name, macos, status, not why, why))
        return out

    def stop_vm(self, vmid: int) -> tuple[bool, str]:
        res = self.pve.qm("stop", str(vmid))
        return res.ok, res.output

    def check_vm(self, vmid: int, vendor: str) -> list[str]:
        """Why this VM cannot take the card (empty list when it can)."""
        info = fetch_vm_info(vmid, adapter=self.pve)
        if info is None:
            return [f"VM {vmid} not found."]
        cfg = gpu.parse_config(info.config_raw)
        issues = []
        if "isa-applesmc" not in cfg.get("args", ""):
            issues.append(f"VM {vmid} is not a macOS VM made by this tool.")
        version = nullmoth.detect_macos(cfg)
        if vendor == "intel":
            issues += igpu_attach.vm_issues(vmid, cfg, False, True)
        if vendor == "nvidia":
            if version is None:
                issues.append("Cannot tell which macOS this VM runs. NVIDIA cards need Sequoia 15.5+ or Tahoe.")
            elif version not in ("sequoia", "tahoe"):
                issues.append(f"NVIDIA cards need macOS Sequoia 15.5+ or Tahoe, this VM is {version}. "
                              "Pick another VM or create a Sequoia or Tahoe VM first.")
        return issues

    def choose(self, bdf: str, vmid: int | None, display: str, ack: bool = False, share_ok: bool = False,
               confirmed: Iterable[int] | None = None) -> list[str]:
        """Validate and store the choices. Returns the reasons it was refused (empty on success).

        A card that a stopped VM also lists needs *share_ok* (the user confirmed the warning). When
        *confirmed* is given it names the stopped VMs the user saw: a holder that is not in it (it
        appeared after the scan) needs a new confirmation."""
        cards = {c.bdf: c for c in self.scan(exclude_vm=vmid if vmid is not None else -1)}
        card = cards.get(gpu.normalize_bdf(bdf) or bdf)
        if card is None:
            return [f"No supported graphics card at {bdf} on this host."]
        if not card.selectable:
            return [card.why_not]
        issues: list[str] = []
        unseen = confirmed is not None and not set(card.stopped_users) <= set(confirmed)
        if card.stopped_users and (not share_ok or unseen):
            issues.append(" ".join(gpu.shared_note(v) for v in card.stopped_users)
                          + " Confirm that you want to use it anyway.")
        if vmid is not None:
            issues += self.check_vm(vmid, card.vendor)
        if card.vendor == "intel":
            display = "gpu"
        if card.vendor == "nvidia":
            display = "console"
            if not ack:
                issues.append("NVIDIA cards need the NullMoth notice acknowledged first.")
        if issues:
            return issues
        self.state.update(gpu={"bdf": card.bdf, "vendor": card.vendor, "device": card.device_id,
                               "boot_vga": card.boot_vga, "name": card.name,
                               "identity": host_setup.identity_of(self.root, card.bdf)},
                          vm=vmid, display=display, ack_nullmoth=bool(ack and card.vendor == "nvidia"))
        self.complete(1)
        self.complete(2)
        return []

    # ── 3. BIOS ──────────────────────────────────────────────────────

    def target(self) -> dict:
        """The chosen card in the shape host_setup wants. Raises CardChanged when the device at the saved
        address is no longer the chosen card."""
        chosen = self.state["gpu"]
        reset = "none"
        if chosen["vendor"] == "amd":
            fam = amd_host.family_for(chosen["device"])
            reset = fam.reset if fam else "none"
        target = host_setup.build_target(self.root, chosen["bdf"], chosen["vendor"], reset, chosen.get("identity"))
        if target["mismatch"]:
            raise CardChanged(target["mismatch"])
        target["boot_vga"] = target["boot_vga"] or chosen["boot_vga"]
        return target

    def card_changed(self, exc: Exception) -> str:
        """The chosen card moved or changed: forget the choice and send the user back to the scan."""
        self.state["gpu"] = None
        self.state["vm"] = None
        self.state["verify_retry"] = False
        self.goto(1)
        return str(exc)

    def bios_items(self, facts: dict | None = None) -> list[dict]:
        return bios_guide.check_items(facts or self.facts(), self.target(), self.root)

    def checked_fields(self, items: list[dict]) -> list[str]:
        """What the BIOS check looked at, one short field per item, for the top of the BIOS step."""
        data = bios_guide.load_data()
        fields = [f"[{'ok' if i['ok'] else '!!'}] {data['settings'][i['key']]['label']}" for i in items]
        return ["What we checked:  " + fields[0]] + fields[1:] if fields else []

    def checked_line(self, items: list[dict]) -> str:
        return "   ".join(self.checked_fields(items))

    def bios_card(self, items: list[dict], save: bool = True, max_width: int | None = None) -> dict | None:
        """The photo card for the failing items, or None when nothing needs the BIOS.

        The QR code of the guide sits inside the card, so one photo holds the steps and the code. It goes
        under the steps when the card is wider than *max_width* columns would allow with the code beside them."""
        failing = [i for i in items if not i["ok"]]
        if not failing:
            return None
        facts = self.facts()
        data = bios_guide.load_data()
        vendor = bios_guide.match_vendor(facts, data)
        rebar = bios_guide.rebar_capable(self.root, self.state["gpu"]["bdf"])
        url = bios_guide.doc_url(data, vendor)
        qr = bios_guide.qr_lines(url)
        text = bios_guide.build_card(failing, facts, data, vendor, self.state["resume_cmd"], rebar, qr, max_width)
        path = bios_guide.save_card(text, self.root) if save else ""
        self.state["bios_saved"] = bool(path)
        return {"text": text, "path": path, "url": url, "qr": qr, "vendor": vendor,
                "firmware_reboot": bios_guide.firmware_setup_supported(self.root)}

    def running_vms(self) -> list[tuple[int, str]]:
        res = self.pve.qm("list")
        return bios_guide.parse_running_vms(res.output) if res.ok else []

    def reboot_to_bios(self) -> tuple[bool, str]:
        """Reboot into the firmware setup, only when the firmware allows it."""
        if not bios_guide.firmware_setup_supported(self.root):
            return False, "This firmware does not allow 'reboot into setup'. Reboot and press the setup key yourself."
        self.pause()
        code, out = self.run(bios_guide.REBOOT_ARGV)
        return code == 0, out.strip()

    # ── 4. host ──────────────────────────────────────────────────────

    def host_plan(self, allow_no_console: bool = False, write_optional: bool = False) -> dict:
        return host_setup.plan_host(self.facts(), self.target(),
                                    {"allow_no_console": allow_no_console, "write_optional": write_optional},
                                    self.root, self.run)

    @staticmethod
    def plan_needs_work(plan: dict) -> bool:
        return bool(plan["changes"] or plan["commands"] or plan["manual"])

    def host_apply(self, plan: dict, confirmed: bool = False) -> dict:
        moved = host_setup.check_identity(self.root, plan.get("identity"))
        if moved:
            raise CardChanged(moved)
        res = host_setup.apply_plan(plan, self.state, self.root, self.run, confirmed)
        if res["ok"]:
            self.state["verify_retry"] = False
            self.complete(4)
            if not self.state.get("reboot_needed"):
                self.complete(5, skipped=True)
        else:
            self.save()
        return res

    # ── 5/6. reboot and verify ───────────────────────────────────────

    def verify(self) -> list[dict]:
        checks = host_setup.verify_host(self.target(), self.root, self.run)
        if not any(c["status"] == "fail" for c in checks):
            self.state["reboot_needed"] = False
            if 5 not in self.state["done"]:
                self.complete(5, skipped=True)
            self.complete(6)
        return checks

    def reboot_pending(self) -> bool:
        """Whether the host still has to reboot. A reboot since the changes were made settles step 5."""
        if self.state.get("reboot_needed") and not host_setup.reboot_pending(self.state, self.root):
            self.state["reboot_needed"] = False
            self.complete(5)
        return host_setup.reboot_pending(self.state, self.root)

    def rebooted_since_apply(self) -> bool:
        return host_setup.rebooted_since_apply(self.state, self.root)

    def send_back(self, step: int) -> None:
        """Verify found a problem a earlier step can fix. The flag stops a loop when that step has nothing to do."""
        self.goto(step)
        self.state["verify_retry"] = True
        self.save()

    def mark_reboot_needed(self) -> None:
        """Go back to the reboot step: the saved changes are not active yet."""
        self.goto(5)
        self.state["reboot_needed"] = True
        self.state["boot_id"] = host_setup.boot_id(self.root)
        self.save()

    def reboot_host(self) -> tuple[bool, str]:
        self.pause()
        code, out = self.run(["systemctl", "reboot"])
        return code == 0, out.strip()

    # ── 7. VM ────────────────────────────────────────────────────────

    def vm_plan(self, allow_stop: bool = False) -> nullmoth.AttachPlan:
        chosen, vmid = self.state["gpu"], self.state["vm"]
        self.target()  # raises CardChanged when the card moved
        if vmid is None:
            plan = nullmoth.AttachPlan(0)
            plan.issues.append("No VM chosen yet. Create or pick a macOS VM first.")
            return plan
        bdf = chosen["bdf"]
        if chosen["vendor"] == "nvidia":
            from .defaults import DEFAULT_ISO_DIR
            from .oc_builder import AMFIPASS
            if not self.state["ack_nullmoth"]:
                plan = nullmoth.AttachPlan(vmid)
                plan.issues.append("The NullMoth notice has not been acknowledged.")
                return plan
            return nullmoth.prepare_attach(vmid, bdf, Path(DEFAULT_ISO_DIR) / AMFIPASS.filename,
                                           allow_stop=allow_stop, adapter=self.pve, sysfs_root=self.sysfs)
        if chosen["vendor"] == "intel":
            return self.intel_plan(vmid, allow_stop)
        return gpu_attach.prepare_attach_amd(vmid, bdf, self.state["display"], allow_stop,
                                             adapter=self.pve, sysfs_root=self.sysfs)

    def intel_plan(self, vmid: int, allow_stop: bool = False) -> nullmoth.AttachPlan:
        """The iGPU attach preflight and plan. The downloads are not done here; the plan names where they go."""
        from .defaults import DEFAULT_ISO_DIR
        from .oc_builder import AMFIPASS_MOD, WHATEVERXE
        info = intel_host.lookup(self.state["gpu"]["device"])
        cache = Path(DEFAULT_ISO_DIR)
        rom_file = info["rom_file"] if info else ""
        return igpu_attach.prepare_attach_igpu(
            vmid, cache / WHATEVERXE.filename, cache / AMFIPASS_MOD.filename, cache / rom_file,
            allow_stop=allow_stop, adapter=self.pve, root=Path(self.root), run=self.run)

    def begin_attach(self) -> None:
        """Remember the VM before touching it, so --undo can find a half-done attach.

        Also the last check before anything is written: the card must still be the chosen one."""
        self.target()
        vmid = self.state["vm"]
        if vmid is not None and vmid not in self.state["vm_records"]:
            self.state["vm_records"].append(vmid)
        self.save()

    def vm_applied(self) -> None:
        self.begin_attach()
        self.complete(7)

    # ── 8. first boot ────────────────────────────────────────────────

    def first_boot_text(self) -> str:
        chosen = self.state["gpu"]
        if chosen["vendor"] == "nvidia":
            lines = [nullmoth.NEXT_STEPS[0],
                     "Checklist inside macOS (take a photo of this or scan the QR code):"]
            lines += [f"  {i}. {s}" for i, s in enumerate(nullmoth.NEXT_STEPS[1:], start=1)]
            lines += [f"Driver releases: {nullmoth.NULLMOTH_URL}/releases", f"Our guide: {NULLMOTH_DOC}"]
            return "\n".join(lines)
        if chosen["vendor"] == "intel":
            return "\n".join(igpu_attach.IGPU_NEXT_STEPS)
        where = ("on the monitor plugged into the card" if self.state["display"] == "gpu"
                 else "in the Proxmox console (the card does the acceleration)")
        return "Start the VM. You should see the OpenCore picker and then the macOS desktop " + where + "."

    def first_boot_qr(self) -> list[str]:
        if self.state["gpu"]["vendor"] != "nvidia":
            return []
        return bios_guide.qr_lines(NULLMOTH_DOC)

    def finish(self) -> None:
        self.complete(8)
        self.state["finished"] = True
        host_setup.clear_login_hint(self.root)
        self.save()

    def _vm_name_suffix(self, vmid: int) -> str:
        """" <name>" of VM *vmid* from the macOS VM list, or "" when it is not listed."""
        try:
            lines = self.macos_vms()[1:]
        except Exception:  # noqa: BLE001
            return ""
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == str(vmid):
                return " " + parts[1]
        return ""

    def summary(self) -> str:
        lines = ["Setup summary"]
        gpu_info = self.state["gpu"]
        if gpu_info:
            lines.append(f"  Card: {gpu_info['name']} at {gpu_info['bdf']}")
        if self.state["vm"] is not None:
            lines.append(f"  VM: {self.state['vm']}" + self._vm_name_suffix(self.state["vm"]))
        changes = [c for c in self.state["host_changes"] if not c.get("reverted")]
        for c in changes:
            lines.append(f"  Changed: {c['path']}" + (f" (backup: {c['backup']})" if c["backup"] else " (new file)"))
        if gpu_info:
            lines += ["", {"nvidia": NEXT_NVIDIA, "intel": NEXT_INTEL}.get(gpu_info["vendor"], NEXT_AMD), ""]
        lines.append("  Undo everything with: gpu-setup --undo")
        return "\n".join(lines)

    # ── undo ─────────────────────────────────────────────────────────

    def undo(self, allow_stop: bool = False) -> tuple[bool, list[str]]:
        """Revert VM settings, then host files. Returns (ok, messages). Safe to run twice.

        Only what this tool wrote is reverted; anything changed since stays as the user left it and is
        reported. Nothing to revert (no record, the VM is gone, the attach was refused) is a success."""
        from .executor import apply_plan
        messages: list[str] = []
        ok = True
        self.undo_kept = 0
        chosen = self.state["gpu"]
        if chosen and host_setup.check_identity(self.root, chosen.get("identity")):
            messages.append(f"Note: the card at {chosen['bdf']} is now a different device (your PCI layout "
                            "changed). Undo only removes what this tool wrote, so nothing there is touched.")
        # One record format for every entry point: a record on disk counts whoever wrote it
        # (this walkthrough or nvidia-attach).
        vmids = list(self.state["vm_records"]) + [v for v in gpu.record_vmids() if v not in self.state["vm_records"]]
        for vmid in vmids:
            record = load_record(vmid)
            if record is None:
                self._forget_vm(vmid)
                messages.append(f"VM {vmid}: nothing to revert (the attach did not change it).")
                continue
            info = fetch_vm_info(vmid, adapter=self.pve)
            if info is not None and not (info.config_raw or "").strip():
                # qm config failed: keep the record, undo can be run again
                ok = False
                messages.append(f"VM {vmid}: could not read its config, so nothing was changed. Try again.")
                continue
            if info is not None and host_setup.record_vm_changed(asdict(record), parse_config(info.config_raw)):
                stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
                path = gpu.record_path(vmid)
                try:
                    path.rename(path.with_name(f"{path.name}.stale-{stamp}"))
                except OSError:
                    path.unlink(missing_ok=True)
                self._forget_vm(vmid)
                messages.append(f"Skipped the record for VM {vmid}: the VM changed since the card was attached, "
                                "so nothing was reverted. Check its hostpci0, args and vga by hand.")
                continue
            plan = gpu_attach.prepare_detach(vmid, adapter=self.pve, allow_stop=allow_stop, missing_ok=True)
            if not plan.ok:
                ok = False
                messages += [f"VM {vmid}: {issue}" for issue in plan.issues]
                continue
            if not plan.steps:  # the VM is gone
                gpu.record_path(vmid).unlink(missing_ok=True)
                self._forget_vm(vmid)
                messages += [f"VM {vmid}: {w}" for w in plan.warnings]
                continue
            result = apply_plan(plan.steps, execute=True)
            kept = [ln for r in result.results for ln in r.output.splitlines() if ln.startswith("KEPT:")]
            self.undo_kept += len(kept)
            if result.ok:
                self._forget_vm(vmid)
                messages.append(f"VM {vmid}: GPU settings restored.")
                messages += [f"VM {vmid}: {w}" for w in plan.warnings]
                messages += [f"VM {vmid}: {ln}" for ln in kept]
            else:
                ok = False
                messages.append(f"VM {vmid}: restoring failed. Log: {result.log_path}")
        had_record = host_setup._p(self.root, host_setup.STATE_FILE).exists()
        res = host_setup.undo_host(self.state, self.root, self.run)
        ok = ok and res["ok"]
        self.undo_kept += len(res["kept"])
        messages += [f"Restored {path}" for path in res["restored"]] + res["kept"] + res["errors"]
        self.undo_nothing = not vmids and not (res["restored"] or res["kept"] or res["errors"] or res["reboot_required"])
        if self.undo_nothing:
            messages.append("Removed an empty setup record; nothing else to undo." if had_record else
                            "Nothing to undo: no GPU setup record found.")
            messages += [f"VM {v} has NullMoth settings in its config but no record. Undo it with: nvidia-detach {v}"
                         for v in self.nullmoth_markers()]
        if res["reboot_required"]:
            messages.append("Reboot the host to finish undoing the boot changes.")
        if ok:
            host_setup.clear_login_hint(self.root)
            host_setup.delete_state(self.root)
        else:
            self.save()
        return ok, messages

    def _forget_vm(self, vmid: int) -> None:
        if vmid in self.state["vm_records"]:
            self.state["vm_records"].remove(vmid)

    def forget_attached(self, vmid: int) -> None:
        """nvidia-detach undid this VM: drop it from the walkthrough's list (only when it is listed)."""
        if vmid in self.state["vm_records"]:
            self._forget_vm(vmid)
            self.save()

    def has_undo(self) -> bool:
        return bool(self.state["host_changes"] or self.state["vm_records"] or gpu.record_vmids())

    def nullmoth_markers(self) -> list[int]:
        """VMs whose config carries what a NullMoth attach writes (hostpci0 plus the 64-bit MMIO window)."""
        found: list[int] = []
        listing = self.pve.qm("list")
        if not listing.ok:
            return found
        for line in listing.output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            cfg = self.pve.qm("config", parts[0])
            if cfg.ok:
                parsed = gpu.parse_config(cfg.output)
                args = parsed.get("args", "")
                if "hostpci0" in parsed and gpu.without_mmio_arg(args) != args:
                    found.append(int(parts[0]))
        return found

    def record_for(self, vmid: int) -> AttachRecord | None:
        return load_record(vmid)
