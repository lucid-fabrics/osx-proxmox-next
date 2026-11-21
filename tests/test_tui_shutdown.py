"""A shutdown that macOS ignores: `qm shutdown` blocks on the VM lock, so the screens must not wait for it."""

from __future__ import annotations

from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.screens.shutdown import shutdown_timed_out, still_running_line

import tui_fakes as F

SIZE = (120, 40)
STILL = "! " + still_running_line("macos-sequoia")


def patch_qm(monkeypatch, qm) -> None:
    monkeypatch.setattr("osx_proxmox_next.screens.vm_actions.get_proxmox_adapter", lambda: qm)


async def home(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.pause(0.1)
    return app.screen


def errors(app) -> list[str]:
    return [n.message for n in app._notifications if n.severity == "error"]


def status(screen) -> str:
    return F.text_of(screen.cards()[0].query_one(".card-status"))


def test_only_a_timeout_of_qm_shutdown_counts_as_the_mac_ignoring_it():
    assert shutdown_timed_out("VM quit/powerdown failed - got timeout")
    assert shutdown_timed_out("Command timed out after 300s: qm shutdown 101")
    assert not shutdown_timed_out("can't lock file '/var/lock/qemu-server/lock-101.conf' - got timeout")
    assert not shutdown_timed_out("VM 101 not running")


@F.tui
async def test_shutting_down_shows_at_once_while_qm_shutdown_blocks_then_the_hint_and_no_red_error(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.BlockingQm()
    patch_qm(monkeypatch, qm)
    app = NextApp()
    now = {"t": 1000.0}
    app.shutdowns.clock = lambda: now["t"]
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("s")
        await pilot.press("enter")                                          # Shut down
        await F.settle(pilot, lambda: ("shutdown", "101") in qm.calls)
        assert not qm.release.is_set()                                      # qm shutdown is still blocking
        assert 101 in app.shutdowns.asked
        await F.settle(pilot, lambda: "◐ shutting down" in status(screen))
        await pilot.press("right")                                          # the UI still answers
        now["t"] += 61
        screen.refresh_now()
        await F.settle(pilot, lambda: screen.query_one("#stuck").display)
        assert F.text_of(screen.query_one("#stuck")) == STILL
        qm.release.set()                                                    # now qm shutdown returns its timeout
        await F.settle(pilot, lambda: not any(t.is_running for t in app.workers))
        await pilot.pause(0.1)
        assert errors(app) == []                                            # a timeout is the hint, not a red error
        assert 101 in app.shutdowns.asked and screen.query_one("#stuck").display
        assert [c for c in qm.calls if c[0] == "stop"] == []                # nothing forced by itself


@F.tui
async def test_force_stop_goes_through_the_blocked_shutdown_with_overrule(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.BlockingQm()
    patch_qm(monkeypatch, qm)
    app = NextApp()
    now = {"t": 1000.0}
    app.shutdowns.clock = lambda: now["t"]
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: ("shutdown", "101") in qm.calls)
        now["t"] += 61
        screen.refresh_now()
        await F.settle(pilot, lambda: screen.query_one("#stuck").display)
        await pilot.press("s")
        assert app.focused.name == "stop"                                   # Force stop has the focus
        await pilot.press("enter")
        await F.settle(pilot, lambda: 101 not in app.shutdowns.asked)
        stops = [c for c in qm.calls if c[0] == "stop"]
        assert stops == [("stop", "101", "--overrule-shutdown", "1")]       # no plain stop waiting on the lock
        await F.settle(pilot, lambda: not any(t.is_running for t in app.workers))
        await pilot.pause(0.1)
        assert errors(app) == []                                            # the shutdown that ended is not an error


@F.tui
async def test_force_stop_falls_back_to_plain_stop_on_a_proxmox_without_the_option(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.BlockingQm(overrule=False, locked=False)
    patch_qm(monkeypatch, qm)
    app = NextApp()
    now = {"t": 1000.0}
    app.shutdowns.clock = lambda: now["t"]
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: ("shutdown", "101") in qm.calls)
        qm.release.set()                                                    # the shutdown timed out, the lock is free
        now["t"] += 61
        screen.refresh_now()
        await F.settle(pilot, lambda: screen.query_one("#stuck").display)
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: 101 not in app.shutdowns.asked)
        assert [c for c in qm.calls if c[0] == "stop"] == [("stop", "101", "--overrule-shutdown", "1"), ("stop", "101")]
        assert errors(app) == []


@F.tui
async def test_a_failed_force_stop_keeps_the_hint_because_the_mac_still_runs(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.BlockingQm(overrule=False)                                        # old Proxmox, lock still held
    patch_qm(monkeypatch, qm)
    app = NextApp()
    now = {"t": 1000.0}
    app.shutdowns.clock = lambda: now["t"]
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: ("shutdown", "101") in qm.calls)
        now["t"] += 61
        screen.refresh_now()
        await F.settle(pilot, lambda: screen.query_one("#stuck").display)
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: bool(errors(app)))
        assert "stop failed" in errors(app)[0]
        assert 101 in app.shutdowns.asked and screen.query_one("#stuck").display
        qm.release.set()


@F.tui
async def test_a_mac_that_powers_off_in_time_shows_no_hint(monkeypatch):
    vms = F.three_vms()
    F.patch_host(monkeypatch, vms=vms)
    qm = F.QmRecorder()                                                      # qm shutdown returns when it is off
    patch_qm(monkeypatch, qm)
    app = NextApp()
    now = {"t": 1000.0}
    app.shutdowns.clock = lambda: now["t"]
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: ("shutdown", "101") in qm.calls)
        vms[0].status = "stopped"
        now["t"] += 61
        screen.refresh_now()
        await F.settle(pilot, lambda: 101 not in app.shutdowns.asked)
        await pilot.pause(0.1)
        assert not screen.query_one("#stuck").display
        assert "stopped" in status(screen)


@F.tui
async def test_a_shutdown_that_fails_for_a_real_reason_is_a_red_error_and_no_hint(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.QmRecorder(ok=False)                                              # "boom": not a timeout
    patch_qm(monkeypatch, qm)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: bool(errors(app)))
        assert "shutdown failed" in errors(app)[0]
        assert 101 not in app.shutdowns.asked
        assert not screen.query_one("#stuck").display
