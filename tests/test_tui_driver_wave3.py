"""Fix wave 3, the Driver screen: each state shows only its own news, a Finish starts a fresh list, enter does what the
failure box says, and the keys the screen lists are the keys that work."""
from __future__ import annotations

import time

import tui_fakes as F
from osx_proxmox_next import driver_engine as engine
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens.base import HelpScreen
from osx_proxmox_next.screens.driver import DriverScreen, DriverView
from osx_proxmox_next.screens.gpu import ChangesDialog
from test_tui_driver import (  # noqa: F401 - fixtures and helpers
    NEW_SNAP, SIZE, footer, host, open_driver, plan, releases, text, write_state)

FAILED = ("done", "done", "done", "fail", "pending", "pending", "pending")


def stopped_view(**kw):
    return DriverView(plan(installed={"NullMoth": "v2.8.1"}, interrupted=True, older_snapshots=[NEW_SNAP], **kw), releases())


def drop_stopped_run(host):
    write_state(phase="failed", states=FAILED, error="Staging failed: the script left no result.")
    host["view"] = stopped_view()


@F.tui
async def test_the_screen_of_a_stopped_update_shows_the_stop_not_a_new_update(host):
    drop_stopped_run(host)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert text(app, "#d-status").split()[-2:] == ["update", "stopped"]
        assert "available" not in text(app, "#d-status")
        facts = F.text_of(screen.query_one("#d-facts"))
        assert "No new snapshot is saved" in facts and NEW_SNAP in facts
        assert "is saved first" not in facts and "A snapshot called" not in facts and "stops for about" not in facts
        assert "What Finish does" in F.text_of(screen.query_one("#g-side .side-title"))
        safe = text(app, "#g-safe")
        assert "Driver snapshot of" in safe and "saved first" not in safe


@F.tui
async def test_a_stopped_update_without_a_snapshot_says_it_cannot_be_undone(host):
    write_state(phase="failed", states=FAILED, error="x", snapshot="", no_snapshot=True)
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, interrupted=True), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "cannot be undone" in text(app, "#g-safe") and "saved first" not in text(app, "#g-safe")
        assert "Undo driver update" not in footer(app)
        assert "this update ran without one" in F.text_of(screen.query_one("#d-facts"))


@F.tui
async def test_a_mac_with_no_stopped_update_still_shows_the_new_update_plan(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "v2.9 available" in text(app, "#d-status") and "What happens" in F.text_of(screen.query_one("#g-side .side-title"))
        assert "is saved first" in F.text_of(screen.query_one("#d-facts"))


@F.tui
async def test_the_finish_screen_starts_a_fresh_list_without_the_clocks_of_the_run_before(host):
    drop_stopped_run(host)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("enter")                                  # Finish: the process has not written anything yet
        await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run")
        line = text(app, "#g-timeline")
        assert "✓ Stopped the Mac" in line and "✓ Took the card off" in line
        assert "1:" not in line and "0:" not in line                # no time of the run before, not even 1:00
        assert "✗" not in line and "· Stage NullMoth v2.9" in line  # the step that failed waits again
        assert "is saved first" not in text(app, "#g-run-note") + text(app, "#g-box-text")


@F.tui
async def test_enter_on_a_failed_run_does_what_the_box_says_and_the_footer_lists_it(host):
    write_state(phase="failed", states=FAILED, error="Staging failed.")
    assert engine.acquire_lock(101)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert screen.mode == "run"
        engine.release_lock(101)
        write_state(phase="failed", states=FAILED, error="Staging failed.")
        await F.settle(pilot, lambda: "The update did not finish" in text(app, "#g-box-title"), what="the failure box")
        assert "enter finishes it." in " ".join(text(app, "#g-box-text").split())
        await F.settle(pilot, lambda: "Finish" in footer(app), what="the footer to offer Finish")
        assert footer(app).index("Finish") < footer(app).index("See every change")
        screen.error = ""                                           # the state file alone says it failed
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"], what="enter to finish the run")
        assert host["spawned"][0][2] is True


@F.tui
async def test_the_plan_of_a_failed_run_is_what_finish_does(host):
    write_state(phase="failed", states=FAILED, error="Staging failed.")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        screen.error = "Staging failed."
        screen.mode = "run"
        await pilot.press("p")
        await F.settle_dialog(pilot, app, ChangesDialog)
        shown = F.text_of(app.screen.query_one("#changes-text"))
        assert "Finish the NVIDIA driver update" in shown and "No new snapshot is saved" in shown
        assert "Save a snapshot" not in shown and "is saved first" not in shown


async def help_text(pilot, app):
    await pilot.press("question_mark")
    await F.settle_screen(pilot, app, HelpScreen)
    return F.text_of(app.screen.query_one("#help-keys"))


@F.tui
async def test_help_lists_y_and_n_only_when_they_work(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        keys = await help_text(pilot, app)
        assert "Update driver" in keys and "See every change" in keys
        assert "Yes, all good" not in keys and "No, help me" not in keys and "Undo driver update" not in keys
        await pilot.press("escape")
        await F.settle_screen(pilot, app, DriverScreen)
        write_state(phase="done", states=("done",) * 7)
        screen.spawned_at = time.time()
        await screen._show_first_boot()
        keys = await help_text(pilot, app)
        assert "Yes, all good" in keys and "No, help me" in keys and "Update driver" not in keys and "Undo driver update" in keys


@F.tui
async def test_help_of_a_failed_run_offers_finish_and_undo(host):
    write_state(phase="failed", states=FAILED, error="x")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        screen.mode, screen.error = "run", "x"
        keys = await help_text(pilot, app)
        assert "Finish" in keys and "Undo driver update" in keys and "Yes, all good" not in keys


@F.tui
async def test_a_running_mac_shows_that_its_versions_are_the_records(host):
    p = plan("amd", {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}, None, "current", from_record=True)
    from test_tui_driver import amd_target
    p.target = amd_target()
    host["view"] = DriverView(p, [])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        assert text(app, "#d-source").strip() == "(from the last update)"


@F.tui
async def test_a_stopped_mac_read_from_its_disk_carries_no_marker(host):
    from test_tui_driver import amd_target
    host["view"] = DriverView(plan("amd", {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}, amd_target(), "current"), [])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert not screen.query("#d-source")
