"""Fakes for the driver update tests: a scripted Proxmox that also knows snapshots, and the Macs it hosts."""
from __future__ import annotations

from collections import deque

from osx_proxmox_next import driver_engine as engine
from osx_proxmox_next.infrastructure import CommandResult
from test_igpu_finish import Clock, Console, Pve as BasePve

NV_ARGS = ('-device isa-applesmc,osk="x" -smbios type=2 -global q35-pcihost.pci-hole64-size=64G')
CFG_BASE = {
    "name": "macos-sequoia", "machine": "q35", "vga": "none", "args": NV_ARGS, "boot": "order=ide0;virtio0",
    "ide0": "local-lvm:vm-9-disk-1,media=disk", "virtio0": "local-lvm:vm-9-disk-2,size=64G",
    "efidisk0": "local-lvm:vm-9-disk-0,size=4M", "hostpci0": "0000:01:00,pcie=0", "net0": "vmxnet3=BC:24:11:AA:BB:CC,bridge=vmbr0",
}
STORAGE = "Name Type Status Total Used Available %\nlocal dir active 1 1 1 1%\nlocal-lvm lvmthin active 1 1 1 1%\n"


def mac(**over):
    cfg = dict(CFG_BASE)
    cfg.update(over)
    return {k: v for k, v in cfg.items() if v is not None}


class DPve(BasePve):
    """BasePve plus the commands of a driver update: shutdown, snapshots, rollback, pvesm status, OpenCore scripts."""

    def __init__(self, cfg=None, status="stopped", snapshots=(), storage=STORAGE, snap_ok=True, shutdown_ok=True,
                 bash_output="", bash_ok=True, **kw):
        super().__init__(mac() if cfg is None else cfg, status=status, **kw)
        self.snapshots, self.storage, self.snap_ok, self.shutdown_ok = list(snapshots), storage, snap_ok, shutdown_ok
        self.bash_output, self.bash_ok, self.runs = bash_output, bash_ok, []
        self.rollbacks = []

    def qm(self, *a):
        sub = a[0]
        if sub == "shutdown":
            self.calls.append(a)
            if self.shutdown_ok:
                self.status = "stopped"
            return CommandResult(self.shutdown_ok, 0 if self.shutdown_ok else 255, "" if self.shutdown_ok else "timeout")
        if sub == "snapshot":
            self.calls.append(a)
            if not self.snap_ok:
                return CommandResult(False, 1, "snapshot feature is not available")
            self.snapshots.append(a[2])
            return CommandResult(True, 0, "")
        if sub == "listsnapshot":
            self.calls.append(a)
            lines = [f"`-> {n}  2026-11-20 10:00:00  before driver update to v2" for n in self.snapshots]
            return CommandResult(True, 0, "\n".join(lines + ["`-> current  You are here!"]))
        if sub == "rollback":
            self.calls.append(a)
            self.rollbacks.append(a[2])
            return CommandResult(True, 0, "")
        if sub == "delsnapshot":
            self.calls.append(a)
            self.snapshots.remove(a[2])
            return CommandResult(True, 0, "")
        return super().qm(*a)

    def run(self, argv):
        self.runs.append(list(argv))
        if argv[:2] == ["pvesm", "status"]:
            return CommandResult(True, 0, self.storage)
        if argv[0] == "qm":
            return self.qm(*argv[1:])
        if argv[0] == "bash":
            out = self.bash_output
            if isinstance(out, list):
                out = out.pop(0) if len(out) > 1 else out[0]
            return CommandResult(self.bash_ok, 0 if self.bash_ok else 1, out)
        return CommandResult(True, 0, "")

    def calls_of(self, name):
        return [c for c in self.calls if c[0] == name]


def comps(**versions):
    return [engine.component(n.replace("_", " "), v, v, "c" * 64, "latest", "2026-11-01T00:00:00Z") for n, v in versions.items()]


__all__ = ["Clock", "Console", "DPve", "NV_ARGS", "STORAGE", "comps", "deque", "mac"]
