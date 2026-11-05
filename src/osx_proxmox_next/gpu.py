"""Vendor-neutral host GPU detection and passthrough readiness checks.

Everything reads sysfs through a ``root`` argument so tests can point at a
fake tree. Nothing here writes host configuration: when a card is not ready,
``vfio_conf_hint`` returns the exact lines for the user to apply.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from shlex import quote as shquote
from collections.abc import Callable

from . import host_setup
from .domain import PlanStep
from .infrastructure import ProxmoxAdapter
from .preflight import has_initcall_blacklist

__all__ = [
    "BAR1_WARN_BYTES",
    "AttachRecord",
    "GpuInfo",
    "GpuProfile",
    "GpuReport",
    "NVIDIA_PROFILE",
    "STATE_DIR",
    "hostpci_value",
    "load_record",
    "record_path",
    "record_step",
    "with_global_args",
    "without_global_args",
    "check_gpu",
    "detect_gpus",
    "MMIO_ARG",
    "gpu_users",
    "is_host_console",
    "record_vmids",
    "running_user_issue",
    "shared_note",
    "hostpci_bdfs",
    "parse_config",
    "build_hostpci_attach_steps",
    "build_hostpci_detach_steps",
    "normalize_bdf",
    "with_mmio_arg",
    "without_mmio_arg",
    "vfio_conf_hint",
]

BAR1_WARN_BYTES = 256 * 1024 * 1024

_BDF_RE = re.compile(r"^(?:([0-9a-fA-F]{4}):)?([0-9a-fA-F]{2}):([0-9a-fA-F]{2})(?:\.([0-7]))?$")
_SYSFS = Path("/sys")
_NOT_GPU_OK_CLASSES = {0x0604, 0x0600}  # PCI/host bridges never block vfio


@dataclass
class GpuInfo:
    bdf: str  # domain:bus:slot, no function
    device_id: int
    supported: bool
    functions: list[str] = field(default_factory=list)  # full BDFs with function

    @property
    def label(self) -> str:
        state = "supported" if self.supported else "not supported"
        return f"{self.bdf}  device {self.device_id:04x}  {state}"


@dataclass
class GpuReport:
    bdf: str
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def normalize_bdf(raw: str) -> str | None:
    """Return ``domain:bus:slot`` (lowercase, function dropped) or None."""
    m = _BDF_RE.match(raw.strip())
    if not m:
        return None
    domain = (m.group(1) or "0000").lower()
    return f"{domain}:{m.group(2).lower()}:{m.group(3).lower()}"


def _read_hex(path: Path) -> int | None:
    try:
        return int(path.read_text().strip(), 16)
    except (OSError, ValueError):
        return None


def _devices(root: Path) -> Path:
    return root / "bus/pci/devices"


def _functions(root: Path, bdf: str) -> list[str]:
    return sorted(p.name for p in _devices(root).glob(f"{bdf}.*"))


def detect_gpus(
    vendor: int, supported: Callable[[int], bool] = lambda _d: True, root: Path = _SYSFS,
) -> list[GpuInfo]:
    """List display controllers of one PCI *vendor* (once per card)."""
    found: list[GpuInfo] = []
    base = _devices(root)
    if not base.is_dir():
        return found
    for dev in sorted(base.iterdir()):
        if _read_hex(dev / "vendor") != vendor:
            continue
        pci_class = _read_hex(dev / "class")
        if pci_class is None or pci_class >> 16 != 0x03:
            continue
        device_id = _read_hex(dev / "device")
        if device_id is None:
            continue
        bdf = dev.name.rsplit(".", 1)[0]
        if any(g.bdf == bdf for g in found):
            continue
        found.append(GpuInfo(
            bdf=bdf, device_id=device_id,
            supported=supported(device_id),
            functions=_functions(root, bdf),
        ))
    return found


def _bound_driver(dev: Path) -> str | None:
    link = dev / "driver"
    return link.resolve().name if link.exists() else None


def _bar_bytes(dev: Path, index: int = 1) -> int | None:
    try:
        line = dev.joinpath("resource").read_text().splitlines()[index]
        start, end, _flags = line.split()
        size = int(end, 16) - int(start, 16) + 1
    except (OSError, IndexError, ValueError):
        return None
    return size if size > 0 else None


def _group_issues(root: Path, dev: Path, functions: list[str]) -> list[str]:
    group = dev / "iommu_group"
    if not group.exists():
        return ["IOMMU is not active for this card (no iommu_group). Enable VT-d/AMD-Vi "
                "in the BIOS and intel_iommu=on iommu=pt (Intel) or iommu=pt (AMD) on the kernel cmdline."]
    members = sorted(p.name for p in (group.resolve() / "devices").iterdir())
    stray = []
    for name in members:
        if name in functions:
            continue
        pci_class = _read_hex(_devices(root) / name / "class")
        if pci_class is not None and pci_class >> 8 in _NOT_GPU_OK_CLASSES:
            continue
        stray.append(name)
    if stray:
        return [f"IOMMU group {group.resolve().name} also holds non-GPU device(s): "
                f"{', '.join(stray)}. Passing the card through would pull them in."]
    return []


_SOFTDEPS = {
    0x10DE: ["nouveau", "nvidia", "snd_hda_intel"],
    0x1002: ["amdgpu", "radeon", "snd_hda_intel"],
}


def vfio_conf_hint(root: Path, bdf: str) -> list[str]:
    """Exact /etc/modprobe.d/vfio.conf lines plus the commands to apply them."""
    ids: list[str] = []
    usb_c = False
    vendor_id: int | None = None
    for fn in _functions(root, bdf):
        dev = _devices(root) / fn
        vendor, device = _read_hex(dev / "vendor"), _read_hex(dev / "device")
        if vendor is None or device is None:
            continue
        pci_class = _read_hex(dev / "class")
        # Only the card's own functions are suggested: never a storage or network function, never another vendor's.
        if (pci_class is not None and pci_class >> 16 in (0x01, 0x02)) or (vendor_id is not None and vendor != vendor_id):
            continue
        vendor_id = vendor if vendor_id is None else vendor_id
        ids.append(f"{vendor:04x}:{device:04x}")
        usb_c = usb_c or (pci_class is not None and pci_class >> 8 in (0x0C03, 0x0C80))
    softdeps = list(_SOFTDEPS.get(vendor_id or 0x10DE, _SOFTDEPS[0x10DE]))
    if usb_c:
        softdeps += ["xhci_pci"] + (["i2c_nvidia_gpu"] if vendor_id == 0x10DE else [])
    return [
        "Put this in /etc/modprobe.d/vfio.conf:",
        f"  options vfio-pci ids={','.join(ids)}",
        *[f"  softdep {name} pre: vfio-pci" for name in softdeps],
        "Then run: update-initramfs -u -k all",
        "Then reboot the host.",
    ]


def _check_binding(dev_root: Path, functions: list[str]) -> list[str]:
    wrong: list[str] = []
    for fn in functions:
        driver = _bound_driver(dev_root / fn)
        if driver != "vfio-pci":
            wrong.append(f"{fn} ({driver or 'no driver'})")
    if not wrong:
        return []
    return ["Every function of the card must be bound to vfio-pci. Not bound: "
            + ", ".join(wrong) + ". The host GPU driver must not hold the card."]


def check_gpu(
    bdf_raw: str,
    vendor: int,
    supported: Callable[[int], bool],
    unsupported_msg: str | Callable[[int], str],
    root: Path = _SYSFS,
    cmdline_path: Path | None = None,
    bar_index: int = 1,
    iomem_path: Path | None = None,
) -> GpuReport:
    """Run every host-side readiness check for one card of *vendor*.

    *unsupported_msg* is a format string using {device} (4-digit hex id) or a
    callable taking the device id. *bar_index* is the BAR whose size is checked."""
    bdf = normalize_bdf(bdf_raw)
    if bdf is None:
        return GpuReport(bdf_raw, issues=[f"'{bdf_raw}' is not a PCI address like 01:00 or 0000:01:00.0."])
    report = GpuReport(bdf)
    dev = _devices(root) / f"{bdf}.0"
    if not dev.exists():
        report.issues.append(f"No PCI device at {bdf}.0 on this host.")
        return report
    if _read_hex(dev / "vendor") != vendor:
        report.issues.append(f"{bdf}.0 is not a vendor {vendor:04x} device.")
        return report
    device_id = _read_hex(dev / "device") or 0
    if not supported(device_id):
        report.issues.append(
            unsupported_msg(device_id) if callable(unsupported_msg)
            else unsupported_msg.format(device=f"{device_id:04x}"))
        return report
    functions = _functions(root, bdf)
    report.issues += _check_binding(_devices(root), functions)
    if report.issues:
        report.hints = vfio_conf_hint(root, bdf)
    report.issues += _group_issues(root, dev, functions)
    _add_warnings(report, dev, cmdline_path, bar_index, iomem_path)
    return report


_FB_NAMES = ("efifb", "simplefb", "bootfb", "vesafb")


def _fb_ranges(iomem: Path) -> list[tuple[int, int]]:
    """Address ranges /proc/iomem gives to a firmware framebuffer driver (efifb, simplefb, ...)."""
    try:
        text = iomem.read_text()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*([0-9a-f]+)-([0-9a-f]+)\s*:\s*(\S+)", line)
        if m and m.group(3).lower() in _FB_NAMES:
            out.append((int(m.group(1), 16), int(m.group(2), 16)))
    return out


def _fb_holds_bar(dev: Path, iomem: Path) -> bool:
    fbs = _fb_ranges(iomem)
    if not fbs:
        return False
    try:
        rows = (dev / "resource").read_text().splitlines()
    except OSError:
        return False
    for row in rows:
        parts = row.split()
        if len(parts) < 2:
            continue
        try:
            start, end = int(parts[0], 16), int(parts[1], 16)
        except ValueError:
            continue
        if end > start and any(start <= lo and hi <= end for lo, hi in fbs):
            return True
    return False


def is_host_console(dev: Path, iomem: Path | None = None) -> bool:
    """True when the host really uses this card as its console: boot_vga is set and a host display driver
    is bound or a firmware framebuffer (efifb, simplefb) holds one of its BARs (per /proc/iomem).
    A boot_vga card bound to vfio-pci with no framebuffer on it is only where the firmware drew its boot screen."""
    if _read_hex(dev / "boot_vga") != 1:
        return False
    drv = _bound_driver(dev)
    if drv and drv != "vfio-pci":
        return True
    if iomem is None:
        iomem = dev.parents[3].parent / "proc/iomem"
    return _fb_holds_bar(dev, iomem)


def shared_note(vmid: int) -> str:
    """The warning for a card that a stopped VM also lists (same wording in every entry point)."""
    return (f"Also assigned to VM {vmid} (stopped). Only one of them can run at a time; "
            "Proxmox refuses to start the second.")


def running_user_issue(vmid: int) -> str:
    return f"The card is in use by running VM {vmid}."


def record_vmids() -> list[int]:
    """VM ids that have a GPU attach record on disk (written by gpu-setup and nvidia-attach alike)."""
    try:
        names = [p.name for p in STATE_DIR.iterdir()]
    except OSError:
        return []
    ids = []
    for name in names:
        m = re.fullmatch(r"gpu-attach-(\d+)\.json", name)
        if m:
            ids.append(int(m.group(1)))
    return sorted(ids)


def _add_warnings(report: GpuReport, dev: Path, cmdline_path: Path | None, bar_index: int = 1,
                  iomem_path: Path | None = None) -> None:
    if is_host_console(dev, iomem_path) and not has_initcall_blacklist(cmdline_path):
        report.warnings.append(
            "This card is the host boot GPU and initcall_blacklist=sysfb_init is not on the kernel "
            "cmdline. The host framebuffer may hold its memory and the VM can fail to start."
        )
    bar1 = _bar_bytes(dev, bar_index)
    if bar1 is not None and bar1 > BAR1_WARN_BYTES:
        report.warnings.append(
            f"BAR{bar_index} is {bar1 // (1024 * 1024)} MiB. Larger than 256 MiB is untested for "
            "macOS guests and QEMU may refuse to start. No automatic resize is done."
        )


MMIO_ARG = "-global q35-pcihost.pci-hole64-size=64G"
HOTPLUG_ARG = "-global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off"
_MMIO_RE = re.compile(r"-global\s+q35-pcihost\.pci-hole64-size=\S+")
STATE_DIR = Path("/var/lib/osx-proxmox-next")


@dataclass(frozen=True)
class GpuProfile:
    """Vendor-specific VM settings applied when a card is attached."""

    vendor: str
    pcie: int  # hostpci placement: 0 conventional PCI, 1 PCI Express root port
    global_args: tuple[str, ...]  # "-global ..." strings merged into the VM args
    sidecar: str = "gpu"  # OpenCore backup suffix, see data/gpu/oc_boot_args.py


NVIDIA_PROFILE = GpuProfile(vendor="nvidia", pcie=0, global_args=(MMIO_ARG,), sidecar="nullmoth")


def _global_re(arg: str) -> re.Pattern[str]:
    key = arg.split("=", 1)[0].replace("-global ", "", 1)
    return re.compile(r"-global\s+" + re.escape(key) + r"=\S+")


def with_global_args(args: str, wanted: tuple[str, ...]) -> str:
    """Return *args* with each ``-global key=value`` set to the wanted value, never duplicated."""
    out = args
    for arg in wanted:
        rx = _global_re(arg)
        out = rx.sub(arg, out) if rx.search(out) else f"{out.rstrip()} {arg}"
    return out.strip()


def without_global_args(args: str, wanted: tuple[str, ...]) -> str:
    out = args
    for arg in wanted:
        out = re.sub(r"\s*" + _global_re(arg).pattern, "", out)
    return out.strip()


def with_mmio_arg(args: str) -> str:
    """Return *args* with the 64-bit MMIO window set to 64G, never duplicated."""
    if _MMIO_RE.search(args):
        return _MMIO_RE.sub(MMIO_ARG, args)
    return f"{args.rstrip()} {MMIO_ARG}".strip()


def without_mmio_arg(args: str) -> str:
    return re.sub(r"\s*" + _MMIO_RE.pattern, "", args).strip()


def verify_stopped_step(vid: str) -> PlanStep:
    return PlanStep(
        title="Verify VM is stopped",
        argv=[
            "bash", "-c",
            f"qm status {shquote(vid)} | grep -q 'status: stopped' || "
            f"{{ echo 'ERROR: VM {vid} is running. Stop it first: qm stop {vid}'; false; }}",
        ],
    )


def stop_step(vid: str) -> PlanStep:
    return PlanStep(
        title="Stop VM",
        argv=["bash", "-c", f"qm status {shquote(vid)} | grep -q stopped || qm stop {shquote(vid)}"],
        risk="warn",
    )


@dataclass
class AttachRecord:
    """What a VM looked like before the GPU was attached, so undo restores it exactly."""

    vmid: int
    bdf: str
    vendor: str
    hostpci0: str | None = None  # original value, None when the slot was empty
    args: str | None = None
    vga: str | None = None
    vga_changed: bool = False
    oc: str = ""  # which patcher edited the OpenCore disk: "nullmoth", "gpu" or "" (none)
    fp: str = ""  # identifies the VM (SMBIOS uuid or name): a record of a deleted VM never applies to a new one
    after: dict | None = None  # what the attach wrote, so undo only reverts untouched fields


def new_record(vmid: int, bdf: str, vendor: str, cfg: dict[str, str], profile: "GpuProfile", hostpci: str,
               vga_none: bool = False, oc: str = "") -> "AttachRecord":
    """The record for an attach about to run (built by the engine the bash installer shares)."""
    return AttachRecord(**host_setup.make_record(vmid, bdf, vendor, cfg, hostpci, list(profile.global_args),
                                                 vga_none, oc))


def record_path(vmid: int) -> Path:
    return STATE_DIR / f"gpu-attach-{vmid}.json"


def load_record(vmid: int) -> AttachRecord | None:
    try:
        return AttachRecord(**json.loads(record_path(vmid).read_text()))
    except (OSError, ValueError, TypeError):
        return None


def record_step(record: AttachRecord, replace: bool = False) -> PlanStep:
    """Plan step that writes the sidecar. It runs before any change to the VM or its OpenCore disk.

    An existing record of the same attach is kept (it holds the true originals); *replace* is set when
    the record on disk belongs to another VM that once had this id."""
    path = record_path(record.vmid)
    body = json.dumps(asdict(record), sort_keys=True)
    keep = "" if replace else f"[ -e {shquote(str(path))} ] || "
    return PlanStep(
        title="Record the original VM settings (for undo)",
        argv=["bash", "-c",
              f"mkdir -p {shquote(str(path.parent))} && {keep}printf %s {shquote(body)} > {shquote(str(path))}"],
    )


def record_is_stale(record: AttachRecord) -> bool:
    """True when the record on disk was written for a different VM than *record* describes."""
    old = load_record(record.vmid)
    return old is not None and old.fp != record.fp


def hostpci_value(bdf: str, profile: GpuProfile, extra: str = "") -> str:
    return f"{bdf},pcie={profile.pcie}{extra}"


def build_hostpci_attach_steps(
    vmid: int, bdf: str, current_args: str, profile: GpuProfile = NVIDIA_PROFILE,
    vga_none: bool = False, extra_hostpci: str = "",
) -> list[PlanStep]:
    """All functions on hostpci0 (placement from *profile*) plus the profile's global args."""
    vid = str(vmid)
    steps = [PlanStep(
        title=f"Attach GPU (all functions, hostpci0, pcie={profile.pcie})",
        argv=["qm", "set", vid, "--hostpci0", hostpci_value(bdf, profile, extra_hostpci)],
        risk="action",
    )]
    new_args = with_global_args(current_args, profile.global_args)
    if new_args != current_args:
        steps.append(PlanStep(title="Add the profile's QEMU args", argv=["qm", "set", vid, "--args", new_args]))
    if vga_none:
        steps.append(PlanStep(title="Turn the emulated display off (vga none)",
                              argv=["qm", "set", vid, "--vga", "none"], risk="warn"))
    return steps


def build_hostpci_detach_steps(
    vmid: int, current_args: str, has_hostpci0: bool,
    record: AttachRecord | None = None, profile: GpuProfile = NVIDIA_PROFILE,
    cfg: dict[str, str] | None = None, notes: list[str] | None = None,
) -> list[PlanStep]:
    """Undo an attach. With a record the VM returns to what it was, field by field.

    A field that no longer holds what the attach wrote keeps the user's value (reported in *notes*).
    Without a record (attached by an older version) only the known additions go away."""
    vid = str(vmid)
    steps: list[PlanStep] = []
    if record is not None:
        current = dict(cfg or {})
        if cfg is None:
            current = {"args": current_args, **({"hostpci0": "?"} if has_hostpci0 else {})}
        res = host_setup.vm_restore(asdict(record), current)
        if notes is not None:
            notes.extend(res["notes"])
        titles = {"hostpci0": "Restore the original hostpci0", "args": "Restore the original args",
                  "vga": "Restore the display setting"}
        for act in res["actions"]:
            key = act["key"]
            if act["op"] == "set":
                argv = ["qm", "set", vid, f"--{key}", act["value"]]
                title = titles[key]
            else:
                argv = ["qm", "set", vid, "--delete", key]
                title = {"hostpci0": "Remove GPU (hostpci0)", "args": "Remove args (there were none before)",
                         "vga": "Restore the display setting"}[key]
            steps.append(PlanStep(title=title, argv=argv, risk="action" if key == "hostpci0" else "info"))
        steps.append(PlanStep(title="Forget the recorded settings",
                              argv=["rm", "-f", str(record_path(vmid))]))
        return steps
    if has_hostpci0:
        steps.append(PlanStep(
            title="Remove GPU (hostpci0)", argv=["qm", "set", vid, "--delete", "hostpci0"], risk="action",
        ))
    stripped = without_global_args(current_args, profile.global_args)
    if stripped != current_args:
        argv = ["qm", "set", vid, "--args", stripped] if stripped else ["qm", "set", vid, "--delete", "args"]
        steps.append(PlanStep(title="Remove the profile's QEMU args", argv=argv))
    return steps


def parse_config(raw: str) -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            cfg[key.strip()] = value.strip()
    return cfg


def hostpci_bdfs(cfg: dict[str, str], mappings: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
    """Map hostpci key -> normalized card addresses it passes through (resource mappings resolved)."""
    out: dict[str, set[str]] = {}
    for key, value in cfg.items():
        if not re.fullmatch(r"hostpci\d+", key):
            continue
        ids = set()
        for token in re.split(r"[,;]", value):
            token = token.strip().removeprefix("host=")
            if token.startswith("mapping="):
                ids |= set((mappings or {}).get(token[len("mapping="):], ()))
                continue
            bdf = normalize_bdf(token)
            if bdf:
                ids.add(bdf)
        out[key] = ids
    return out


def pci_mappings(adapter: ProxmoxAdapter, root: str = "/") -> dict[str, set[str]]:
    """Cluster PCI resource mappings (name -> card addresses), from the config file or pvesh."""
    def run(argv: list[str], timeout: int = 20) -> tuple[int, str]:
        res = adapter.run(argv)
        return res.returncode, res.output
    return host_setup.load_mappings(root, run)


def gpu_users(adapter: ProxmoxAdapter, bdf: str, vmid: int, root: str = "/") -> list[tuple[int, bool]]:
    """Other VMs that already hold this card (directly or through a resource mapping), and whether they run."""
    listing = adapter.qm("list")
    users: list[tuple[int, bool]] = []
    if not listing.ok:
        return users
    mappings = pci_mappings(adapter, root)
    for line in listing.output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) == vmid:
            continue
        cfg = adapter.qm("config", parts[0])
        if cfg.ok and any(bdf in ids for ids in hostpci_bdfs(parse_config(cfg.output), mappings).values()):
            users.append((int(parts[0]), parts[2] == "running"))
    return users
