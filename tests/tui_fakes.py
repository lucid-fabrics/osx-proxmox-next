"""Fake hosts for the TUI tests and the screenshot script: no Proxmox, no qm, no network."""

from __future__ import annotations

import asyncio
import functools
import time

from osx_proxmox_next.defaults import CpuInfo
from osx_proxmox_next.preflight import PreflightCheck, FixCommand
from osx_proxmox_next.services import VmRecord
from osx_proxmox_next.services.host_facts import HostFacts
from osx_proxmox_next.services.unattended_service import DriverState

CONFIG = """acpi: 1
args: -device isa-applesmc,osk="x"
cores: {cores}
memory: {mem}
name: {name}
net0: vmxnet3=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=0
virtio0: local-lvm:vm-{vmid}-disk-1,iothread=1,size={disk}G
ide0: local:iso/opencore.iso,media=disk,size=40M
"""


def record(vmid: int, name: str, status: str, cores: int = 8, mem_gb: int = 16, disk: int = 128, extra: str = "") -> VmRecord:
    cfg = CONFIG.format(vmid=vmid, name=name, cores=cores, mem=mem_gb * 1024, disk=disk) + extra
    return VmRecord(vmid, name, status, cores, mem_gb * 1024, cfg)


def three_vms() -> list[VmRecord]:
    return [record(101, "macos-sequoia", "running"),
            record(107, "macos-ventura", "running", disk=96),
            record(104, "build-sonoma", "stopped", cores=4, mem_gb=8, disk=96)]


def installing_driver(minutes: int = 18, now: float | None = None) -> DriverState:
    now = time.time() if now is None else now
    return DriverState(seen=["recovery", "erase", "installer", "restarts"], started_at=now - minutes * 60,
                       events=[("13:40", "restart handled, recovery detached"), ("13:42", "screen changed, still installing")],
                       alive=True, exists=True)


def cpu() -> CpuInfo:
    return CpuInfo(vendor="Intel", model_name="Intel Core i9-14900K", family=6, model=183, needs_emulated_cpu=True)


def host_facts(**kw) -> HostFacts:
    base = dict(cpu=cpu(), storage_targets=["local-lvm", "local"], iso_dirs=["/var/lib/vz/template/iso"],
                storage_free_gb={"local-lvm": 412, "local": 90}, host_cores=32, host_memory_mb=64 * 1024,
                vm_memory_limit_mb=60 * 1024, next_vmid=105)
    base.update(kw)
    return HostFacts(**base)


def good_checks() -> list[PreflightCheck]:
    names = ["qm", "pvesm", "pvesh", "qemu-img", "dmg2img", "sgdisk", "partprobe", "losetup", "mkfs.fat", "blkid"]
    out = [PreflightCheck(f"{n} available", True, f"/usr/bin/{n}") for n in names]
    out += [PreflightCheck("KVM ignore_msrs", True, "ok"), PreflightCheck("IOMMU enabled", True, "ok"),
            PreflightCheck("/dev/kvm present", True, "ok"), PreflightCheck("Root privileges", True, "uid 0")]
    return out


MSRS_FIX = (FixCommand("echo Y > /sys/module/kvm/parameters/ignore_msrs", ("sh", "-c", "echo Y > /sys/module/kvm/parameters/ignore_msrs")),
            FixCommand("echo 'options kvm ignore_msrs=Y' >> /etc/modprobe.d/kvm.conf", ("sh", "-c", "echo 'options kvm ignore_msrs=Y' >> /etc/modprobe.d/kvm.conf")),
            FixCommand("update-initramfs -k all -u", ("update-initramfs", "-k", "all", "-u")))


def failing_checks() -> list[PreflightCheck]:
    checks = [c for c in good_checks() if c.name not in ("KVM ignore_msrs", "dmg2img available")]
    checks.insert(0, PreflightCheck("KVM ignore_msrs", False, "off", fix=MSRS_FIX))
    checks.insert(1, PreflightCheck("dmg2img available", False, "missing",
                                    fix=(FixCommand("apt install -y dmg2img", ("apt", "install", "-y", "dmg2img")),)))
    return checks


def desktop_ppm(width: int = 320, height: int = 200) -> bytes:
    """A small P6 frame: dark bar on top, a coloured desktop below."""
    rows = bytearray()
    for y in range(height):
        for x in range(width):
            if y < height // 12:
                rows += bytes((10, 15, 20))
            else:
                t = (x + y) / (width + height)
                rows += bytes((int(39 + 150 * t), int(70 - 10 * t), int(140 - 40 * t)))
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(rows)


def installer_ppm(width: int = 320, height: int = 200) -> bytes:
    rows = bytearray()
    for y in range(height):
        for x in range(width):
            on_bar = abs(y - int(height * 0.7)) < 2 and width * 0.3 < x < width * 0.7
            rows += bytes((215, 221, 228)) if on_bar else bytes((0, 0, 0))
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(rows)


def patch_host(monkeypatch, vms=None, checks=None, drivers=None, grab=None, facts=None) -> None:
    """Make every host lookup of the app answer from fakes."""
    from osx_proxmox_next.widgets.live_screen import LiveScreen

    vms = three_vms() if vms is None else vms
    drivers = drivers or {}
    monkeypatch.setattr("osx_proxmox_next.screens.home.list_macos_vm_records", lambda: list(vms))
    monkeypatch.setattr("osx_proxmox_next.screens.home.read_driver_state",
                        lambda vmid: drivers.get(vmid, DriverState()))
    monkeypatch.setattr("osx_proxmox_next.screens.home.detect_storage_free_gb", lambda: {"local-lvm": 412})
    monkeypatch.setattr("osx_proxmox_next.app.run_preflight", lambda: list(good_checks() if checks is None else checks))
    monkeypatch.setattr("osx_proxmox_next.app.load_host_facts", lambda: facts or host_facts())
    monkeypatch.setattr("osx_proxmox_next.app.socket.gethostname", lambda: "pve")
    for mod in ("home", "new_mac"):
        monkeypatch.setattr(f"osx_proxmox_next.screens.{mod}.detect_cpu_cores", lambda: 8)
        monkeypatch.setattr(f"osx_proxmox_next.screens.{mod}.detect_memory_mb", lambda: 16384)
    frames = grab or (lambda vmid, probe: installer_ppm() if vmid == 107 else desktop_ppm())
    monkeypatch.setattr(LiveScreen, "grabber", staticmethod(frames))


async def settle(pilot, predicate, tries: int = 150, step: float = 0.04) -> None:
    """Wait until *predicate()* is true; a worker thread and a redraw may be needed first."""
    for _ in range(tries):
        await pilot.pause(step)
        if predicate():
            return
    raise AssertionError("the screen never reached the expected state")


def text_of(widget) -> str:
    """The plain text a Static or custom widget shows (Textual 1.x keeps it in .renderable, 8.x in .content)."""
    if hasattr(widget, "render_text"):
        return widget.render_text()
    for attr in ("content", "renderable"):
        shown = getattr(widget, attr, None)
        if shown is not None:
            return getattr(shown, "plain", None) or str(shown)
    shown = widget.render()
    return getattr(shown, "plain", None) or str(shown)


class QmRecorder:
    """Stands in for the Proxmox adapter: records qm calls and answers from a table."""

    def __init__(self, ok: bool = True):
        self.calls: list[tuple] = []
        self.ok = ok

    def qm(self, *args):
        from osx_proxmox_next.infrastructure import CommandResult
        self.calls.append(args)
        return CommandResult(self.ok, 0 if self.ok else 1, "" if self.ok else "boom")


def tui(fn):
    """Run an async test body to completion: one fresh event loop per test, no plugin needed."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper
