#!/usr/bin/env python3
"""Run the real osx-next TUI against a fake Proxmox host, for recording the README demo (scripts/demo/tui.tape).

Nothing here touches a real host: every lookup the screens make is answered by the test fakes (tests/*_fakes.py),
the graphics card flow runs against a fake sysfs tree in a temp directory, the build pipeline is a scripted
pipeline that advances one phase about every 1.5 s, and any attempt to start a subprocess is refused and recorded.

    PYTHONPATH=src python3 scripts/demo/run_demo.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "tests", Path(__file__).resolve().parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import tui_fakes as F  # noqa: E402
from gpu_tui_fakes import host_card  # noqa: E402
from nullmoth_fakes import MAC_CFG, FakeQm  # noqa: E402
from test_gpu_setup import Env  # noqa: E402
from png_ppm import png_to_ppm  # noqa: E402

from osx_proxmox_next import gpu_setup, host_setup  # noqa: E402
from osx_proxmox_next.executor import ApplyResult, StepResult  # noqa: E402
from osx_proxmox_next.services.pipeline import BuildPipeline, PipelineDeps  # noqa: E402
from osx_proxmox_next.services.unattended_service import PHASES, DriverState  # noqa: E402

DESKTOP = ROOT / "docs/static/img/macos-vnc-desktop.png"
SIZE = (100, 30)
CARD = "0000:03:00"
LSPCI = {f"lspci -nn -s {CARD}.0": (0, "03:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. "
                                       "[AMD/ATI] Navi 21 [Radeon RX 6800/6800 XT / 6900 XT] [1002:73bf] (rev c1)")}
SEQUOIA_CFG = MAC_CFG.replace("macos-tahoe", "macos-sequoia") + "vga: std\n"
NEW_VMID = 107
INSTALL_EVENTS = ["recovery booted", "erased the new disk", "installer started", "installing, 38 percent",
                  "restart handled, recovery detached", "first start, setting up"]


class DemoQm(FakeQm):
    """The scripted qm, answering `qm list` with each Mac's real name (the plain fake calls every VM vm<ID>)."""

    def run(self, argv):
        if argv[:2] == ["qm", "list"]:
            from osx_proxmox_next.infrastructure import CommandResult
            rows = [f"{vmid} {cfg.split('name: ')[1].split(chr(10))[0]} {state} 4096 64 1" for vmid, (state, cfg) in self.vms.items()]
            return CommandResult(True, 0, "VMID NAME STATUS MEM BOOTDISK PID\n" + "\n".join(rows))
        return super().run(argv)


class Spawned(list):
    """Every subprocess the demo was asked to start (always empty: the demo refuses them)."""


class DemoHost:
    """The state of the fake host: its Macs, the new Mac a build adds, and the pace of the scripted build."""

    def __init__(self, tmp: Path, phase_seconds: float = 1.5) -> None:
        self.phase_seconds = phase_seconds
        self.spawned = Spawned()
        self.vms = [F.record(105, "macos-sequoia", "running", cores=8, mem_gb=16, disk=128),
                    F.record(106, "tahoe-studio", "stopped", cores=8, mem_gb=16, disk=128)]
        self.install_started = 0.0
        # tahoe-studio lists the card too (it is stopped): that is what the "Before you continue" question is about
        shared = SEQUOIA_CFG.replace("macos-sequoia", "tahoe-studio") + f"hostpci0: {CARD}.0,pcie=1\n"
        self.env = Env(tmp, vms={105: ("running", SEQUOIA_CFG), 106: ("stopped", shared)}, answers=LSPCI, cpu="intel")
        self.env.qm = DemoQm(self.env.qm.vms)
        host_card(self.env.root, bdf=CARD, device=0x73BF)   # a Radeon RX 6800 on the host's own driver, free
        self.desktop = png_to_ppm(DESKTOP)

    # ── fake pieces ──────────────────────────────────────────────────

    def pause(self, share: float = 1.0) -> None:
        time.sleep(self.phase_seconds * share)

    def driver_state(self, vmid: int) -> DriverState:
        """The hands-off install of the new Mac: one more phase every few phases of the build."""
        if vmid != NEW_VMID or not self.install_started:
            return DriverState()
        elapsed = time.time() - self.install_started
        count = min(len(PHASES), 1 + int(elapsed / (self.phase_seconds * 2)))
        events = [(time.strftime("%H:%M:%S", time.localtime(self.install_started + i * self.phase_seconds * 2)), msg)
                  for i, msg in enumerate(INSTALL_EVENTS[:count])]
        return DriverState(seen=list(PHASES[:count]), started_at=self.install_started, events=events, alive=True, exists=True)

    def deps(self, pipe: BuildPipeline) -> PipelineDeps:
        host = self
        steps = [SimpleNamespace(title=t) for t in (
            "Create the VM", "Set the Mac hardware", "Add the OpenCore disk", "Add the recovery disk", "Add the main disk",
            "Add the network card", "Set the boot order", "Write the SMBIOS", "Start the VM")]

        def assets(_config):
            return [SimpleNamespace(name="macOS Sequoia recovery", ok=False, stale=False, downloadable=True)]

        def download(_config, _todo, on_progress=None, **_kw):
            for pct in (20, 55, 90, 100):
                on_progress("macOS Sequoia", pct)
                host.pause(0.25)
            return []

        def dry_run(plan, on_step=None, **_kw):
            for idx, step in enumerate(plan, 1):
                on_step(idx, len(plan), step, StepResult(step.title, "demo", True, 0, ""))
                host.pause(1 / len(plan))
            return SimpleNamespace(ok=True, log_path=Path("/dev/null"))

        def live(vmid, plan, on_step=None, **_kw):
            for idx, step in enumerate(plan, 1):
                on_step(idx, len(plan), step, StepResult(step.title, "demo", True, 0, ""))
                host.pause(1 / len(plan))
            host.vms.append(F.record(vmid, pipe.config.name, "running", cores=pipe.config.cores,
                                     mem_gb=pipe.config.memory_mb // 1024, disk=pipe.config.disk_gb))
            return SimpleNamespace(ok=True, log_path=Path("/dev/null")), None

        def spawn(_vmid, _disk):
            host.install_started = time.time()
            return f"/var/log/osx-next-unattended-{NEW_VMID}.log"

        def preflight():
            host.pause()
            return F.good_checks()

        return PipelineDeps(preflight=preflight, assets=assets, download=download, plan=lambda _c: steps, dry_run=dry_run,
                            live=live, vm_exists=lambda _vmid: False, spawn=spawn)


def _frames(host: DemoHost):
    return lambda vmid, _probe: host.desktop if vmid == 105 else F.installer_ppm()


def install(mp: pytest.MonkeyPatch, tmp: Path, phase_seconds: float = 1.5) -> DemoHost:
    """Point every host lookup of the app at the fake host. *mp* undoes it all."""
    host = DemoHost(tmp, phase_seconds)
    facts = F.host_facts(host_cores=16, host_memory_mb=64 * 1024, vm_memory_limit_mb=60 * 1024, next_vmid=NEW_VMID)
    F.patch_host(mp, vms=host.vms, grab=_frames(host), facts=facts)
    mp.setattr("osx_proxmox_next.screens.home.THUMB_REFRESH_SECONDS", 2)   # a card's picture is back quickly after a screen change
    mp.setattr("osx_proxmox_next.defaults.max_vm_memory_mb", lambda: 0)
    mp.setattr("osx_proxmox_next.services.host_facts.max_vm_memory_mb", lambda: 0)
    mp.setattr("osx_proxmox_next.screens.home.read_driver_state", host.driver_state)
    mp.setattr("osx_proxmox_next.screens.build.read_driver_state", host.driver_state)
    mp.setattr("osx_proxmox_next.screens.build.fetch_vm_info", lambda vmid, adapter=None: SimpleNamespace(status="running"))
    mp.setattr("osx_proxmox_next.screens.build.get_proxmox_adapter", lambda: None)

    class DemoPipeline(BuildPipeline):
        def __init__(self, config, unattended, force_download=False, deps=None, vmid=0):
            super().__init__(config, unattended, force_download, deps=deps, vmid=vmid)
            if deps is None:
                self.deps = host.deps(self)

    mp.setattr("osx_proxmox_next.screens.build.BuildPipeline", DemoPipeline)

    # the graphics card flow: a fake sysfs tree, a scripted qm, and a host setup that finishes without touching anything
    mp.setattr("osx_proxmox_next.screens.gpu.Session", lambda: host.env.session())
    real_load = host_setup.load_state
    mp.setattr(host_setup, "load_state", lambda root="/": real_load(host.env.root if root == "/" else root))
    mp.setattr(gpu_setup, "list_macos_vms", lambda: ["VMID NAME STATUS", "105 macos-sequoia running", "106 tahoe-studio stopped"])
    mp.setattr("osx_proxmox_next.nullmoth_finish.versions", lambda: [{"tag": "v2.9", "prerelease": False}])
    mp.setattr("osx_proxmox_next.nullmoth_cli.version_lines", lambda found: [r["tag"] for r in found])

    real_apply = gpu_setup.Session.host_apply

    def host_apply(session, *args, **kwargs):
        host.pause(2.0)                                 # long enough for the run screen to tick
        return real_apply(session, *args, **kwargs)     # writes into the fake host tree only

    mp.setattr(gpu_setup.Session, "host_apply", host_apply)

    def apply(steps, execute=False, **_kw):
        return ApplyResult(True, [StepResult("s", "c", True, 0, "")], Path("/dev/null"))

    mp.setattr("osx_proxmox_next.executor.apply_plan", apply)
    mp.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)

    def refuse(*args, **kwargs):
        host.spawned.append(args[0] if args else kwargs)
        raise RuntimeError("the demo never starts a process")

    mp.setattr(subprocess, "Popen", refuse)
    mp.setattr(subprocess, "run", refuse)
    return host


def main() -> None:
    from osx_proxmox_next.app import NextApp

    with tempfile.TemporaryDirectory(prefix="osx-next-demo-") as tmp, pytest.MonkeyPatch.context() as mp:
        install(mp, Path(tmp))
        NextApp().run(size=SIZE)


if __name__ == "__main__":
    main()
