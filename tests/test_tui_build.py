"""Build: the pipeline on screen, the live VM view, quitting, retrying and resuming."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from textual.widgets import RichLog

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.assets import AssetCheck
from osx_proxmox_next.domain import PlanStep, VmConfig
from osx_proxmox_next.preflight import PreflightCheck
from osx_proxmox_next.rollback import RollbackSnapshot
from osx_proxmox_next.screens.base import ChoiceDialog
from osx_proxmox_next.screens.build import BuildScreen, LogScreen, ZoomScreen, format_clock, timeline_text
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.services import VmInfo
from osx_proxmox_next.services.pipeline import BuildPipeline, PipelineDeps
from osx_proxmox_next.services.unattended_service import DriverState
from osx_proxmox_next.widgets.chrome import KeyBar
from osx_proxmox_next.widgets.live_screen import LiveScreen

SIZE = (100, 30)
CONFIG = VmConfig(vmid=105, name="macos-sequoia", macos="sequoia", cores=8, memory_mb=16384, disk_gb=128,
                  bridge="vmbr0", storage="local-lvm")
STEPS = [PlanStep(f"step {i}", ["true"]) for i in range(14)]


def ok_result():
    return SimpleNamespace(ok=True, log_path=Path("/tmp/x.log"))


def deps(**over) -> PipelineDeps:
    d = PipelineDeps(
        preflight=lambda: [PreflightCheck("qm available", True, "")],
        assets=lambda cfg: [AssetCheck("OpenCore", Path("/x"), True, "")],
        download=lambda *a, **k: [],
        plan=lambda cfg: STEPS,
        dry_run=lambda steps, on_step=None: ok_result(),
        live=lambda vmid, steps, on_step=None: (ok_result(), RollbackSnapshot(vmid, Path("/tmp/snap"))),
        vm_exists=lambda vmid: False,
        destroy=lambda *a, **k: (ok_result(), None),
        spawn=lambda vmid, disk: "/var/log/osx-next-unattended-105.log",
    )
    for key, value in over.items():
        setattr(d, key, value)
    return d


def patch_build(monkeypatch, state=None, status="running"):
    box = {"state": state or F.installing_driver(), "status": status}
    monkeypatch.setattr("osx_proxmox_next.screens.build.read_driver_state", lambda vmid: box["state"])
    monkeypatch.setattr("osx_proxmox_next.screens.build.fetch_vm_info",
                        lambda vmid, adapter=None: VmInfo(vmid, "macos-sequoia", box["status"], ""))
    monkeypatch.setattr("osx_proxmox_next.screens.build.get_proxmox_adapter", lambda: None)
    monkeypatch.setattr("osx_proxmox_next.screens.build.POLL_EVERY", 1)
    return box


async def open_build(pilot, app, screen):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    app.push_screen(screen)
    await F.settle(pilot, lambda: app.screen is screen and screen.is_attached)


def timeline(screen) -> str:
    return F.text_of(screen.query_one("#timeline"))


def note(screen) -> str:
    return F.text_of(screen.query_one("#build-note"))


def keys(app) -> str:
    return app.screen.query_one(KeyBar).render().plain


def test_format_clock():
    assert format_clock(0) == "0:00" and format_clock(1100.4) == "18:20" and format_clock(-5) == "0:00"


@F.tui
async def test_a_hands_off_build_reaches_the_install_phase_with_the_live_screen(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps()))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.safe_to_close and bool(screen.query("#build-live")))
        text = timeline(screen)
        for line in ("✓ Checked this host", "✓ Downloaded macOS", "✓ Rehearsed all 14 steps",
                     "✓ Created VM 105", "◐ Installing macOS", "· Ready"):
            assert line in text
        assert "◐ Booting the recovery" in text or "Booting" in text
        assert "Safe to close." in note(screen) and "this view is back when you reopen osx-next" in note(screen)
        assert "elapsed" in F.text_of(screen.query_one("#build-elapsed")) and "usually 30 to 45 min" in F.text_of(screen.query_one("#build-elapsed"))
        assert F.text_of(screen.query_one("#build-title")) == "Building macos-sequoia"
        live = screen.query_one("#build-live", LiveScreen)
        assert live.interval == 2 and live.vmid == 105
        assert "live from VM 105" in F.text_of(screen.query_one("#build-caption"))
        assert "macos-sequoia" in app.screen.query_one("#topbar").render().plain
        for key in ("Full log", "Zoom screen", "Back to your Macs", "Help"):
            assert key in keys(app)
        assert not screen.busy()


@F.tui
async def test_quit_is_free_once_the_install_runs_in_its_own_process(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps()))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.safe_to_close)
        await pilot.press("q")
    assert not app.is_running


@F.tui
async def test_quit_asks_while_an_in_process_phase_runs_and_esc_waits(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    gate = threading.Event()

    def slow_dry_run(steps, on_step=None):
        gate.wait(10)
        return ok_result()

    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps(dry_run=slow_dry_run)))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.busy())
        assert "Nothing on the host changes until the rehearsal has passed." in note(screen)
        assert "◐ Rehearse all 14 steps" in timeline(screen)
        await pilot.press("escape")
        assert app.screen is screen                       # leaving is refused while it runs
        await pilot.press("q")
        assert isinstance(app.screen, ChoiceDialog)
        await pilot.press("escape")                        # "Cancel": the build goes on
        assert app.is_running and app.screen is screen
        await pilot.press("q")
        await pilot.press("tab")
        await pilot.press("enter")                         # "Quit anyway"
        gate.set()
    assert not app.is_running


@F.tui
async def test_a_failed_host_check_stops_at_phase_one_and_offers_the_host_check(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        bad = deps(preflight=lambda: [PreflightCheck("dmg2img available", False, "")])
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=bad))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.failed == "check")
        assert "✗ Check this host" in timeline(screen) and "· Download macOS" in timeline(screen)
        assert "1 host check failed: dmg2img available" in F.text_of(screen.query_one("#latest"))
        assert "Stopped." in note(screen) and "h opens the host check" in note(screen)
        for key in ("Retry", "Open host check", "Full log", "Back to your Macs"):
            assert key in keys(app)
        assert not screen.query("#build-live")
        await pilot.press("h")
        from osx_proxmox_next.screens.host_check import HostCheckScreen
        await F.settle(pilot, lambda: isinstance(app.screen, HostCheckScreen))
        await pilot.press("escape")
        await F.settle(pilot, lambda: app.screen is screen)
        # fix the host, then retry from the failed phase
        screen.pipe.deps.preflight = lambda: [PreflightCheck("dmg2img available", True, "")]
        await pilot.press("r")
        await F.settle(pilot, lambda: screen.pipe.safe_to_close)
        assert screen.pipe.failed == ""
        await pilot.press("r")                             # nothing to retry now
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_h_does_nothing_when_the_failure_is_not_the_host(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        bad = deps(dry_run=lambda steps, on_step=None: SimpleNamespace(ok=False, log_path=Path("/tmp/x.log")))
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=bad))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.failed == "rehearse")
        await pilot.press("h")
        assert app.screen is screen
        assert "Retry" in keys(app) and "Open host check" not in keys(app)
        assert "The rehearsal failed" in F.text_of(screen.query_one("#latest"))


@F.tui
async def test_a_failed_create_shows_the_log_tail_and_the_rollback_hints(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)

    def live(vmid, steps, on_step=None):
        on_step(3, 14, steps[2], SimpleNamespace(ok=False, returncode=5))
        return SimpleNamespace(ok=False, log_path=Path("/tmp/live.log")), RollbackSnapshot(vmid, Path("/tmp/snap"))

    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps(live=live)))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.failed == "create")
        latest = F.text_of(screen.query_one("#latest"))
        assert "Creating the VM failed. Log: /tmp/live.log" in latest
        assert "FAIL 3/14: step 2 (rc=5)" in latest and "Log tail" in latest
        assert "Review snapshot: /tmp/snap" in latest and "qm destroy 105 --purge" in latest
        assert "✗ Created VM 105" not in timeline(screen) and "✗ Create VM 105" in timeline(screen)


@F.tui
async def test_a_manual_install_ends_with_a_line_about_the_proxmox_console(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, False, pipeline=BuildPipeline(CONFIG, False, deps=deps()))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.done)
        assert "The VM is created." in note(screen) and "Proxmox console of VM 105" in note(screen)
        assert "post-install --vmid 105 --execute" in note(screen)
        assert "Install macOS" not in timeline(screen) and "✓ Ready" in timeline(screen)
        assert screen.query_one("#build-live", LiveScreen) and "usually" not in F.text_of(screen.query_one("#build-elapsed"))


@F.tui
async def test_the_full_log_is_a_scrolling_screen_with_every_line(monkeypatch, tmp_path):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    driver_log = tmp_path / "driver.log"
    driver_log.write_text("BETA: unattended install for VM 105\n  [13:30:01] Waiting for the OpenCore boot picker\n")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        pipe = BuildPipeline(CONFIG, True, deps=deps(spawn=lambda vmid, disk: str(driver_log)))
        screen = BuildScreen(CONFIG, True, pipeline=pipe)
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: pipe.safe_to_close)
        await pilot.press("l")
        assert isinstance(app.screen, LogScreen)
        await pilot.pause(0.2)
        lines = [strip.text for strip in app.screen.query_one(RichLog).lines]
        assert any("Rehearsing all 14 steps" in line for line in lines)
        assert any("Waiting for the OpenCore boot picker" in line for line in lines)
        assert "Scroll" in keys(app)
        await pilot.press("escape")
        assert app.screen is screen


@F.tui
async def test_the_log_screen_copes_with_a_missing_driver_log(monkeypatch, tmp_path):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        pipe = BuildPipeline(CONFIG, False, deps=deps())
        screen = BuildScreen(CONFIG, False, pipeline=pipe)
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: pipe.done)
        app.push_screen(LogScreen(pipe))
        await pilot.pause(0.2)
        assert isinstance(app.screen, LogScreen)


@F.tui
async def test_z_zooms_the_live_screen_and_esc_comes_back(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)
    gate = threading.Event()

    def slow_live(vmid, steps, on_step=None):
        gate.wait(10)
        return ok_result(), RollbackSnapshot(vmid, Path("/tmp/snap"))

    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps(live=slow_live)))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.phase("create").status == "active")
        assert "Creating the VM. This takes about a minute." in note(screen)
        await pilot.press("z")                              # no VM yet: nothing to zoom
        assert app.screen is screen and "The Mac's screen shows here once the VM exists." in F.text_of(screen.query_one("#build-caption"))
        gate.set()
        await F.settle(pilot, lambda: screen.pipe.safe_to_close and bool(screen.query("#build-live")))
        await pilot.press("z")
        assert isinstance(app.screen, ZoomScreen)
        live = app.screen.query_one("#zoom-live", LiveScreen)
        await F.settle(pilot, lambda: live.grabs > 0)
        assert live.size.width >= 98 and live.placeholder == ""
        await pilot.press("escape")
        assert app.screen is screen
        await pilot.press("z")
        await pilot.press("z")
        assert app.screen is screen


@F.tui
async def test_the_driver_log_drives_the_substeps_and_the_latest_lines(monkeypatch):
    F.patch_host(monkeypatch)
    box = patch_build(monkeypatch, state=F.installing_driver(2))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps()))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: "✓ Booted the recovery" in timeline(screen)
                       and "13:42" in F.text_of(screen.query_one("#latest")))
        text = timeline(screen)
        assert "✓ Booted the recovery" in text and "✓ Erased the new disk" in text
        assert "✓ Started the installer" in text and "◐ Installing, restarting" in text
        assert "· First start" in text and "· Ready at Setup Assistant" in text
        latest = F.text_of(screen.query_one("#latest"))
        assert latest.index("13:42") < latest.index("13:40")          # newest first
        # the install finishes
        box["state"] = DriverState(seen=["recovery", "erase", "installer", "restarts", "first_start", "ready"],
                                   started_at=1.0, finished=True, alive=False, exists=True)
        await F.settle(pilot, lambda: screen.pipe.done)
        assert "✓ Ready at Setup Assistant" in timeline(screen)
        assert "Finish Setup Assistant on the screen above" in note(screen)
        assert "post-install --vmid 105 --execute" in note(screen)


@F.tui
async def test_a_driver_failure_is_shown_with_what_to_do_by_hand(monkeypatch):
    F.patch_host(monkeypatch)
    box = patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps()))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.safe_to_close)
        box["state"] = DriverState(seen=["recovery", "erase"], error="Timed out after 900s waiting for recovery to start",
                                   started_at=1.0, exists=True)
        await F.settle(pilot, lambda: screen.pipe.failed == "install")
        assert "✗ Erasing the new disk" in timeline(screen)
        assert "Timed out after 900s" in F.text_of(screen.query_one("#latest"))
        assert "Press Enter in the VM console" in note(screen)
        assert "Retry" not in keys(app)                       # a half-done install is never driven twice
        await pilot.press("r")
        assert screen.pipe.failed == "install"


@F.tui
async def test_reopening_rebuilds_the_build_screen_from_the_log(monkeypatch):
    F.patch_host(monkeypatch, drivers={107: F.installing_driver(18)})
    patch_build(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen.resume(107, F.installing_driver(18))
        await open_build(pilot, app, screen)
        assert screen.config is None and screen.pipe.safe_to_close
        await F.settle(pilot, lambda: bool(screen.query("#build-live")))
        assert F.text_of(screen.query_one("#build-title")) == "Building VM 107"
        text = timeline(screen)
        assert "✓ Created VM 107" in text and "◐ Installing macOS" in text
        assert "VM 107" in app.screen.query_one("#topbar").render().plain
        assert "Safe to close." in note(screen) and "18:" in F.text_of(screen.query_one("#build-elapsed"))
        await pilot.press("r")                                 # a rebuilt view cannot retry
        await pilot.press("q")
    assert not app.is_running


@F.tui
async def test_a_crashing_pipeline_becomes_a_failed_phase(monkeypatch):
    F.patch_host(monkeypatch)
    patch_build(monkeypatch)

    def boom(cfg):
        raise KeyError("surprise")

    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = BuildScreen(CONFIG, True, pipeline=BuildPipeline(CONFIG, True, deps=deps(assets=boom)))
        await open_build(pilot, app, screen)
        await F.settle(pilot, lambda: screen.pipe.failed != "")
        assert "Unexpected error" in screen.pipe.failure and not screen.pipe.running


def test_timeline_text_without_a_driver_has_no_substeps():
    pipe = BuildPipeline(CONFIG, False, deps=deps())
    pal = {"success": "g", "accent": "a", "error": "e", "muted": "m"}
    text = timeline_text(pipe, pal).plain
    assert text.splitlines()[0].startswith("· Check this host") and "Install" not in text
