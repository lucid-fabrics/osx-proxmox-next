"""The restart box: nothing restarts before the box is on screen, and it names every guest the restart stops."""
from __future__ import annotations

import asyncio

import tui_fakes as F
from gpu_tui_fakes import (  # noqa: F401 - the underscore names are autouse fixtures
    SIZE, _no_network, _vm_list, amd_env, applied, at_choose, box, mode)
from osx_proxmox_next import bios_guide
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens import gpu_restart
from osx_proxmox_next.screens.gpu import GpuScreen

PCT = "VMID       Status     Lock         Name\n200        running                 web\n201        stopped                 db\n"


@F.tui
async def test_an_enter_typed_ahead_never_restarts_before_the_box_is_on_screen(tmp_path, applied, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(gpu_restart, "_now", lambda: clock[0])
    reached, release = asyncio.Event(), asyncio.Event()
    original = GpuScreen.show_run

    async def slow(self, title, lines, buttons, *a, **k):
        if title == "Time for the one restart":      # a slow host: the box is not on screen yet
            reached.set()
            await release.wait()
        await original(self, title, lines, buttons, *a, **k)

    monkeypatch.setattr(GpuScreen, "show_run", slow)
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await F.settle(pilot, reached.is_set)
        clock[0] += 5.0                              # longer than the guard, spent waiting for the box
        await pilot.press("enter")
        await pilot.pause()
        assert not env.run.ran("systemctl", "reboot")
        release.set()
        await F.settle(pilot, lambda: box(screen).startswith("Time for the one restart"))
        await F.settle(pilot, lambda: screen._primary_action is not None)
        await pilot.press("enter")                   # the box just appeared: the guard starts now
        await pilot.pause()
        assert not env.run.ran("systemctl", "reboot")
        clock[0] += 2.0
        await F.settle(pilot, lambda: not screen.query_one("#g-restart").has_class("-active"))   # button press effect
        await pilot.press("enter")
        await F.settle(pilot, lambda: env.run.ran("systemctl", "reboot"))


@F.tui
async def test_the_restart_box_lists_the_running_containers(tmp_path, applied):
    env = amd_env(tmp_path)
    env.run.answers["pct list"] = (0, PCT)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await F.settle(pilot, lambda: box(screen).startswith("Time for the one restart"))
        text = box(screen)
        assert "● web (CT 200) is shut down cleanly first" in text and "db" not in text
        assert "No VM is running." not in text


@F.tui
async def test_a_missing_or_failing_pct_shows_no_container_lines(tmp_path, applied):
    env = amd_env(tmp_path)
    env.run.answers["pct list"] = (127, "pct: command not found")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await F.settle(pilot, lambda: box(screen).startswith("Time for the one restart"))
        assert "CT " not in box(screen) and "No VM is running." in box(screen)


def test_running_containers_come_from_pct_list_and_a_raising_pct_is_none(tmp_path):
    from gpu_fakes import FakeRun
    from osx_proxmox_next import gpu_setup
    assert bios_guide.parse_running_cts(PCT) == [(200, "web")]
    assert bios_guide.parse_running_cts("VMID Status Lock Name\n300 running web two\nx running y\n") == [(300, "two")]

    def boom(argv, timeout=20):
        raise OSError("no pct")
    s = gpu_setup.Session(str(tmp_path), FakeRun({"pct list": (0, PCT)}), None)
    assert s.running_containers() == [(200, "web")]
    assert gpu_setup.Session(str(tmp_path), boom, None).running_containers() == []
