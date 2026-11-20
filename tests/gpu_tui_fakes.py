"""Helpers shared by the graphics card screen tests (test_tui_gpu.py and test_tui_gpu_run.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

import tui_fakes as F
from gpu_fakes import add_card, write
from osx_proxmox_next import gpu_setup
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.gpu_flow import approval_record, build_preview
from osx_proxmox_next.screens.gpu import GpuScreen
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.widgets.chrome import KeyBar
from test_gpu_setup import Env, ready

SIZE = (110, 34)
HINT = "etc/profile.d/osx-proxmox-next-gpu.sh"
# the card sits on the host driver of its vendor until the host setup has run: that is what a real host looks like
HOST_DRIVER = {0x1002: "amdgpu", 0x10DE: "nvidia", 0x8086: "i915"}


@pytest.fixture(autouse=True)
def _vm_list(monkeypatch):
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["VMID NAME STATUS", "100 macos-sequoia stopped"])
    F.patch_host(monkeypatch, vms=[F.record(101, "macos-sequoia", "running")])


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The NVIDIA driver releases come from GitHub: tests answer from a fixed list."""
    monkeypatch.setattr("osx_proxmox_next.nullmoth_finish.versions",
                        lambda: [{"tag": "v2.9", "prerelease": False}, {"tag": "v3.0-rc", "prerelease": True}])
    monkeypatch.setattr("osx_proxmox_next.nullmoth_cli.version_lines", lambda found: [r["tag"] for r in found])


@pytest.fixture
def applied(monkeypatch):
    calls = []
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan",
                        lambda steps, execute=False, **k: calls.append(steps) or ApplyResult(
                            True, [StepResult("s", "c", True, 0, "")], Path("/tmp/log")))
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)
    return calls


def host_card(root: Path, bdf: str = "0000:06:00", vendor: int = 0x1002, device: int = 0x73FF, audio: int = 0xAB28,
              group: int = 7, **kw) -> None:
    """A card as it is on a fresh host: bound to its vendor's own driver, not to vfio-pci."""
    add_card(root, bdf=bdf, vendor=vendor, device=device, audio=audio, group=group,
             driver=kw.pop("driver", HOST_DRIVER[vendor]), **kw)


def nvidia_card(root: Path, **kw) -> None:
    host_card(root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3, **kw)


def amd_env(tmp_path, **kw) -> Env:
    env = Env(tmp_path, **kw)
    host_card(env.root)
    return env


def rebind(env: Env) -> None:
    """The host restarted: the vfio setup it wrote now holds every card that had a host driver."""
    for dev in (env.root / "sys/bus/pci/devices").iterdir():
        link = dev / "driver"
        if link.is_symlink() and link.resolve().name != "vfio-pci":
            link.unlink()
            target = env.root / "sys/bus/pci/drivers/vfio-pci"
            target.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)


def reboot_host(env: Env, boot: str = "later\n") -> None:
    write(env.root, "/proc/sys/kernel/random/boot_id", boot)
    ready(env)
    rebind(env)


def approved(s, card_bdf: str = "06:00", vmid: int | None = 100, display: str = "gpu", **over) -> dict:
    """Choose *card_bdf* for *vmid* and store the approval the screen would have stored for it."""
    card = next(c for c in s.scan() if card_bdf in c.bdf)
    prev = build_preview(s, card, vmid, display)
    s.choose(card_bdf, vmid, display, ack=card.vendor == "nvidia")
    record = {**approval_record(prev, typed=False, release=""), **over}
    s.approve(record)
    return record


async def start(pilot, app, env, vmid=100, undo=False, session=None) -> GpuScreen:
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    screen = GpuScreen(vmid=vmid, undo=undo, session=session or env.session())
    app.push_screen(screen)
    await F.settle(pilot, lambda: screen.is_attached)
    return screen


async def at_choose(pilot, app, env, **kw) -> GpuScreen:
    screen = await start(pilot, app, env, **kw)
    await F.settle(pilot, lambda: screen.mode == "choose" and screen.preview is not None)
    await pilot.pause(0.1)
    return screen


async def at_choose_unusable(pilot, app, env) -> GpuScreen:
    screen = await start(pilot, app, env)
    await F.settle(pilot, lambda: screen.mode == "choose" and not screen.working and bool(screen.query("#g-card")))
    await pilot.pause(0.2)
    return screen


async def mode(pilot, screen, name: str) -> None:
    await F.settle(pilot, lambda: screen.mode == name and not screen.working)
    await pilot.pause(0.15)


def row(screen, rid: str) -> str:
    return screen.query_one(rid).render().plain


def flat(text: str) -> str:
    """Wrapped lines read as one: a sentence that wraps at this width is still the same sentence."""
    return " ".join(text.split())


def panel(screen) -> str:
    return F.text_of(screen.query_one("#g-need"))


def keys(app) -> str:
    return app.screen.query_one(KeyBar).render().plain


def box(screen) -> str:
    return F.text_of(screen.query_one("#g-box-title")) + "\n" + F.text_of(screen.query_one("#g-box-text"))


def timeline(screen) -> str:
    return F.text_of(screen.query_one("#g-timeline"))


def dialog_text(dialog) -> str:
    return " ".join(F.text_of(w) for w in dialog.query(".dialog-line"))

