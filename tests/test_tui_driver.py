"""The driver of a Mac that has a card: the Driver block, the approval, the run, the first boot question, u."""
from __future__ import annotations

import time

import pytest
from textual.widgets import Input

import tui_fakes as F
from driver_fakes import DPve, comps
from osx_proxmox_next import driver_check, driver_engine as engine, driver_spawn
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.driver_core import DriverError
from osx_proxmox_next.driver_plan import DriverPlan
from osx_proxmox_next.driver_targets import Choice, Target
from osx_proxmox_next.nullmoth import ACK_PHRASE
from osx_proxmox_next.screens import driver as drv
from osx_proxmox_next.screens.driver import DriverScreen, DriverView
from osx_proxmox_next.screens.gpu import ChangesDialog, GpuScreen
from osx_proxmox_next.screens.gpu_before import BeforeDialog
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.widgets.chrome import KeyBar
from osx_proxmox_next.widgets.rows import ChoiceRow

SIZE = (100, 30)
NEW_SNAP = "osx-next-driver-20261121-1030"
CARD = "hostpci0: 0000:01:00,pcie=0\n"


def nv_target(tag="v2.9"):
    return Target("nvidia", {"NullMoth": {"version": tag, "tag": tag, "sha256": "", "source": "latest"}})


def amd_target():
    return Target("amd", {"Lilu": {"version": "1.7.2", "tag": "1.7.2", "sha256": "a", "source": "pinned"},
                          "WhateverGreen": {"version": "1.7.0", "tag": "1.7.0", "sha256": "b", "source": "pinned"}})


def releases():
    return [{"tag": "v2.9", "prerelease": False, "published": "2026-11-18"}, {"tag": "v2.9-rc1", "prerelease": True, "published": "2026-11-10"},
            {"tag": "v2.8.1", "prerelease": False, "published": "2026-10-01"}]


def plan(kind="nvidia", installed=None, target=None, verdict="update", **kw):
    p = DriverPlan(101, "macos-sequoia", kind, "sequoia", False, installed or {}, bool(installed), target or nv_target(), verdict,
                   snapshot=NEW_SNAP, **kw)
    return p


@pytest.fixture
def host(monkeypatch):
    state = {"view": DriverView(plan(installed={"NullMoth": "v2.8.1"}), releases()), "spawned": [], "views": [], "put_back": [], "removed": []}

    def load(vmid, choice=None, releases=True):
        state["views"].append(choice)
        view = state["view"]
        if choice is not None and choice.nullmoth:
            target = nv_target(choice.nullmoth)
            verdict = engine.judge(view.plan.installed, target.versions())["state"] if view.plan.installed else "unknown"
            view = DriverView(plan(installed=view.plan.installed, target=target, verdict=verdict), view.releases)
        return view
    monkeypatch.setattr(drv, "load_view", load)
    monkeypatch.setattr(driver_spawn, "spawn_update", lambda vmid, choice, finish=False, accept_risk=False, no_snapshot=False:
                        state["spawned"].append((vmid, choice, finish, accept_risk, no_snapshot)) or "/var/log/x")
    monkeypatch.setattr(drv, "put_back", lambda vmid, env: state["put_back"].append(vmid) or NEW_SNAP)
    monkeypatch.setattr(drv, "remove_safety_snapshot", lambda vmid, env: state["removed"].append(vmid) or NEW_SNAP)
    monkeypatch.setattr(drv, "get_proxmox_adapter", lambda: DPve())
    F.patch_host(monkeypatch, vms=[F.record(101, "macos-sequoia", "stopped", extra=CARD), F.record(104, "build-sonoma", "stopped")])
    return state


async def open_driver(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.press("g")
    await F.settle_screen(pilot, app, DriverScreen)
    await F.settle(pilot, lambda: app.screen.mode in ("attached", "run", "first_boot"), what="the Driver screen to load")
    await pilot.pause(0.1)
    return app.screen


def text(app, selector):
    return F.text_of(app.screen.query_one(selector))


def footer(app):
    return app.screen.query_one(KeyBar).render().plain


@F.tui
async def test_g_on_a_mac_with_a_card_opens_the_driver_block_with_the_update(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert screen.mode == "attached" and screen.mac == "macos-sequoia"
        row = text(app, "#d-driver")
        assert "Driver" in row and "NullMoth v2.8.1" in row and "NullMoth v2.9 available" in row
        picker = screen.query_one("#d-version", ChoiceRow)
        assert picker.value == "v2.9" and [o.value for o in picker.options] == ["v2.9", "v2.9-rc1", "v2.8.1"]
        assert "(latest)" in picker.options[0].label and "(pre-release)" in picker.options[1].label
        facts = F.text_of(screen.query_one("#d-facts"))
        assert "The Mac stops for about 10 minutes." in facts and "The card comes off and goes back on." in facts and NEW_SNAP in facts
        items = footer(app)
        assert "Update driver" in items and "Version" in items and "See every change" in items and "Put it back" in items
        assert screen.crumb() == "macos-sequoia › Graphics card BETA"


@F.tui
async def test_g_on_a_mac_without_a_card_still_opens_the_card_choice(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await pilot.press("right", "right")
        await pilot.press("g")
        await F.settle_screen(pilot, app, GpuScreen)


@F.tui
async def test_an_amd_mac_that_is_up_to_date_has_nothing_to_update(host):
    host["view"] = DriverView(plan("amd", {"Lilu": "1.7.2", "WhateverGreen": "1.7.0"}, amd_target(), "current"), [])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        row = text(app, "#d-driver")
        assert "Lilu 1.7.2 and WhateverGreen 1.7.0" in row and "✓ up to date" in row
        assert not screen.query("#d-version") and "pins" in text(app, "#d-pinned")
        assert "Update driver" not in footer(app) and screen.query_one("#d-go").disabled
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, DriverScreen)
        assert "The Mac stops for about 1 minute." in F.text_of(screen.query_one("#d-facts"))


@F.tui
async def test_a_mac_set_up_before_records_has_an_unknown_installed_version(host):
    host["view"] = DriverView(plan(installed={}, verdict="unknown"), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        row = text(app, "#d-driver")
        assert "installed version unknown" in row and "NullMoth v2.9 available" in row
        assert "Update driver" in footer(app) and not screen.query_one("#d-go").disabled


@F.tui
async def test_an_up_to_date_nvidia_mac_offers_a_reinstall_once_the_version_row_moved(host):
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.9"}, verdict="current"), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "✓ up to date" in text(app, "#d-driver") and "Update driver" not in footer(app) and "Reinstall" not in footer(app)
        await pilot.press("right")
        await F.settle(pilot, lambda: screen.touched and screen.mode == "attached" and not screen.working)
        await pilot.press("left")
        await F.settle(pilot, lambda: not screen.working and screen.query("#d-go"))
        assert "Reinstall" in footer(app) and screen.query_one("#d-go").label.plain == "Reinstall"


@F.tui
async def test_picking_another_release_makes_a_new_plan(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("right")
        await F.settle(pilot, lambda: len(host["views"]) >= 2 and not screen.working and screen.mode == "attached")
        await pilot.pause(0.2)
        assert host["views"][-1] == Choice(nullmoth="v2.9-rc1")
        assert "NullMoth v2.9-rc1 available" in text(app, "#d-driver")


@F.tui
async def test_p_shows_every_change_and_x_the_commands(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("p")
        await F.settle_screen(pilot, app, ChangesDialog)
        body = F.text_of(app.screen.query_one("#changes-text"))
        assert "Update the NVIDIA driver of macos-sequoia (VM 101)" in body and "  4. Stage NullMoth v2.9" in body
        await pilot.press("x")
        await pilot.pause(0.1)
        assert "qm shutdown 101 --timeout 120" in F.text_of(app.screen.query_one("#changes-text"))
        await pilot.press("escape")
        await F.settle_screen(pilot, app, DriverScreen)


@F.tui
async def test_enter_asks_once_and_the_approved_update_runs_detached(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("enter")
        await F.settle_screen(pilot, app, BeforeDialog)
        dialog = app.screen
        assert dialog.query_one("#before-go").disabled and "Update driver" in str(dialog.query_one("#before-go").label)
        text_all = " ".join(F.text_of(w) for w in dialog.query("Static"))
        assert "The Mac stops for about 10 minutes." in text_all and "NVIDIA GPU support (Beta)" in text_all and NEW_SNAP in text_all
        dialog.query_one("#g-ack", Input).value = ACK_PHRASE
        await pilot.pause(0.1)
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run", what="the update to be started")
        vmid, choice, finish, risk, nosnap = host["spawned"][0]
        assert (vmid, choice.nullmoth, finish, risk, nosnap) == (101, "", False, True, False)


@F.tui
async def test_cancelling_the_approval_starts_nothing(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("enter")
        await F.settle_screen(pilot, app, BeforeDialog)
        await pilot.press("escape")
        await F.settle_screen(pilot, app, DriverScreen)
        assert host["spawned"] == []


@F.tui
async def test_storage_without_snapshots_asks_for_the_typed_phrase(host):
    p = plan("amd", {"Lilu": "1.6.8"}, amd_target(), "update", snapshot_ok=False, snapshot_reason="virtio0 is on storage x (type lvm)")
    host["view"] = DriverView(p, [])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("enter")
        await F.settle_screen(pilot, app, BeforeDialog)
        text_all = " ".join(F.text_of(w) for w in app.screen.query("Static"))
        flat = " ".join(text_all.split())
        assert "cannot take snapshots (virtio0 is on storage x (type lvm))" in flat and "A snapshot called" not in flat
        assert not app.screen.query("#g-ack")
        app.screen.query_one("#g-confirm", Input).value = "no snapshot"
        await pilot.pause(0.1)
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"], what="the update to be started")
        assert host["spawned"][0][4] is True and host["spawned"][0][3] is False
        assert screen.mode == "run"


def write_state(phase="running", states=("done", "done", "active", "pending", "pending", "pending", "pending"), **kw):
    steps = [{"id": i, "title": t, "done": d, "state": s, "started": time.time() - 70, "ended": time.time() - 10}
             for (i, t), (_i, d), s in zip(engine.step_titles("nvidia", "NullMoth v2.9"),
                                           engine.step_titles("nvidia", "NullMoth v2.9", done=True), states)]
    engine.write_update(101, {"kind": "nvidia", "phase": phase, "steps": steps, "snapshot": NEW_SNAP, "events": [
        "10:00:00 Saved the snapshot " + NEW_SNAP, "10:01:00 Downloading NullMoth v2.9"], **kw})


@F.tui
async def test_a_run_in_progress_is_shown_as_steps_and_follows_the_state_file(host):
    write_state()
    engine.lock_path(101).write_text("1\n")        # a live process runs it
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert screen.mode == "run"
        line = text(app, "#g-timeline")
        assert "✓ Stopped the Mac" in line and "✓ Saved a snapshot" in line and "◐ Take the card off" in line
        assert "· Stage NullMoth v2.9" in line and "1:00" in line
        assert "Downloading NullMoth v2.9" in text(app, "#g-box-text")
        write_state(states=("done", "done", "done", "done", "active", "pending", "pending"))
        await F.settle(pilot, lambda: "◐ Put the card back" in text(app, "#g-timeline"), what="the timeline to follow the state")
        assert "Safe to leave" in text(app, "#g-run-note")


@F.tui
async def test_a_run_that_stopped_offers_to_finish_or_put_back(host):
    write_state(phase="failed", states=("done", "done", "done", "fail", "pending", "pending", "pending"),
                error="Staging failed: the script left no result.")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert screen.mode == "attached"           # nobody runs it: the Mac's screen says it was cut off
        assert "An earlier update was cut off" in text(app, "#d-unfinished")
        assert "Finish" in footer(app) and screen.query_one("#d-go").label.plain == "Finish"
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run")
        assert host["spawned"][0][2] is True and host["spawned"][0][3] is True       # finish, the notice was accepted before


@F.tui
async def test_a_run_that_died_shows_a_failure_and_enter_finishes_it(host):
    write_state(phase="running")
    engine.lock_path(101).write_text("1\n")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        engine.release_lock(101)
        await F.settle(pilot, lambda: screen.error, what="the run to be seen as stopped")
        await F.settle(pilot, lambda: "The update did not finish" in text(app, "#g-box-title"))
        assert "The update stopped before it finished." in text(app, "#g-box-text")
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"], what="the finish to start")
        assert host["spawned"][0][2] is True and not screen.error


@F.tui
async def test_a_spawned_update_that_never_wrote_a_state_is_a_failure(host, monkeypatch):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        screen._spawn(Choice())
        await F.settle(pilot, lambda: screen.mode == "run")
        screen.spawned_at = time.time() - 1000
        await F.settle(pilot, lambda: screen.error, what="the missing start to be noticed")
        assert "did not start" in screen.error


@F.tui
async def test_a_spawn_that_cannot_start_says_so(host, monkeypatch):
    def boom(*a, **k):
        raise OSError("no /var/log")
    monkeypatch.setattr(driver_spawn, "spawn_update", boom)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        screen._spawn(Choice())
        await pilot.pause(0.2)
        assert screen.mode == "attached"


@F.tui
async def test_after_the_update_the_first_boot_question_comes_and_yes_offers_to_remove_the_snapshot(host):
    write_state(phase="done", states=("done",) * 7)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert screen.mode == "first_boot"
        assert "Do you see the macOS desktop?" in text(app, "#g-box-text") and "✓ Driver installed" in text(app, "#g-timeline")
        assert "y" in footer(app) and "Yes, all good" in footer(app)
        await pilot.press("y")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        assert engine.read_update(101) == {}
        assert "Keep" in " ".join(str(b.label) for b in app.screen.query("Button"))
        await pilot.press("enter")                              # Keep is the default
        await F.settle_screen(pilot, app, HomeScreen)
        assert host["removed"] == []


@F.tui
async def test_yes_then_remove_removes_the_safety_snapshot(host):
    write_state(phase="done", states=("done",) * 7)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("y")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("tab", "enter")
        await F.settle_screen(pilot, app, HomeScreen)
        assert host["removed"] == [101]


@F.tui
async def test_a_removal_that_fails_is_reported(host, monkeypatch):
    def fail(vmid, env):
        raise DriverError("Could not remove the snapshot x: busy")
    monkeypatch.setattr(drv, "remove_safety_snapshot", fail)
    write_state(phase="done", states=("done",) * 7)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("y")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("tab", "enter")
        await F.settle_screen(pilot, app, HomeScreen)


@F.tui
async def test_yes_without_a_snapshot_just_ends(host):
    write_state(phase="done", states=("done",) * 7, snapshot="")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("y")
        await F.settle_screen(pilot, app, HomeScreen)


@F.tui
async def test_no_opens_the_no_signal_help_and_back_returns(host):
    write_state(phase="done", states=("done",) * 7)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("n")
        await F.settle(pilot, lambda: screen.helping and "No signal? Try these" in text(app, "#g-box-title"))
        assert "Use an HDMI cable" in text(app, "#g-box-text")
        await pilot.press("y")                                   # y does nothing while the help is open
        await pilot.pause(0.1)
        assert screen.helping
        await pilot.press("escape")
        await F.settle(pilot, lambda: not screen.helping and "Do you see" in text(app, "#g-box-text"))
        await pilot.press("n")
        await F.settle(pilot, lambda: screen.helping)
        await pilot.press("enter")
        await F.settle(pilot, lambda: not screen.helping)


@F.tui
async def test_u_puts_the_mac_back_after_a_confirmation(host):
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("u")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        assert "goes back to " + NEW_SNAP in " ".join(F.text_of(w) for w in app.screen.query("Static"))
        await pilot.press("escape")                             # cancel is the default: nothing happens
        await F.settle_screen(pilot, app, DriverScreen)
        assert host["put_back"] == []
        await pilot.press("u")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("tab", "enter")
        await F.settle(pilot, lambda: host["put_back"] == [101] and not screen.working, what="the Mac to be put back")


@F.tui
async def test_u_without_a_snapshot_says_there_is_nothing_to_put_back(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("u")
        await pilot.pause(0.2)
        assert isinstance(app.screen, DriverScreen) and host["put_back"] == []


@F.tui
async def test_a_put_back_that_fails_is_shown_and_the_screen_reloads(host, monkeypatch):
    def fail(vmid, env):
        raise DriverError("Another driver update is running for this Mac.")
    monkeypatch.setattr(drv, "put_back", fail)
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("u")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("tab", "enter")
        await F.settle(pilot, lambda: len(host["views"]) >= 2 and screen.mode == "attached" and not screen.working)


@F.tui
async def test_a_refused_mac_shows_why_and_cannot_be_updated(host):
    host["view"] = DriverView(plan(refusals=["This Mac is still being installed. Let that finish first."]), [])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "still being installed" in text(app, "#g-reason") and screen.query_one("#d-go").disabled
        assert "See every change" not in footer(app)
        await pilot.press("enter", "p")
        await pilot.pause(0.1)
        assert isinstance(app.screen, DriverScreen)


@F.tui
async def test_a_mac_that_cannot_be_read_says_so_and_leaving_works(host, monkeypatch):
    def broken(vmid, choice=None, releases=True):
        raise RuntimeError("qm not found")
    monkeypatch.setattr(drv, "load_view", broken)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await pilot.press("g")
        await F.settle_screen(pilot, app, DriverScreen)
        await F.settle(pilot, lambda: "Could not read this Mac: qm not found" in text(app, "#g-loading"))
        await pilot.press("escape")
        await F.settle_screen(pilot, app, HomeScreen)


@F.tui
async def test_leaving_while_it_loads_waits_for_the_step(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        screen.working = True
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert isinstance(app.screen, DriverScreen) and screen.busy()
        screen.working = False
        await pilot.press("escape")
        await F.settle_screen(pilot, app, HomeScreen)


def test_before_lists_the_facts_the_notice_and_the_phrases():
    nv = drv.build_before(plan(installed={"NullMoth": "v2.8.1"}))
    assert nv.nvidia and nv.notice_title and not nv.phrase and nv.go_label == "Update driver"
    assert nv.notes[0].splitlines()[0] == "The Mac stops for about 10 minutes."
    same = drv.build_before(plan(verdict="current", warnings=["Tahoe is experimental."]))
    assert same.go_label == "Reinstall" and same.warnings == ["Warning: Tahoe is experimental."]
    no = drv.build_before(plan("amd", target=amd_target(), snapshot_ok=False, snapshot_reason="why"))
    assert no.phrase and no.phrase_text == "no snapshot" and not no.nvidia and "why" in no.warnings[0]


def test_the_release_list_for_the_picker_is_at_most_ten_and_may_be_missing(monkeypatch):
    from osx_proxmox_next import driver_plan, nullmoth_finish
    many = [{"tag": f"v{i}", "prerelease": False, "published": "2026"} for i in range(30)]
    monkeypatch.setattr(driver_plan, "prepare", lambda vmid, choice, pve=None, reinstall=False: plan())
    monkeypatch.setattr(drv, "prepare", driver_plan.prepare)
    monkeypatch.setattr(nullmoth_finish, "versions", lambda: many)
    assert len(drv.load_view(101).releases) == 10
    assert drv.load_view(101, releases=False).releases == []

    def offline():
        raise RuntimeError("offline")
    monkeypatch.setattr(nullmoth_finish, "versions", offline)
    assert drv.load_view(101).releases == []
    monkeypatch.setattr(drv, "prepare", lambda *a, **k: plan("intel", target=amd_target()))
    monkeypatch.setattr(drv.igpu_engine, "list_releases", lambda kind: many)
    assert len(drv.load_view(101).releases) == 10


@F.tui
async def test_home_marks_a_mac_with_a_newer_driver(host):
    engine.write_record(101, "nvidia", comps(NullMoth="v2.8.1"))
    engine.write_check({"NullMoth": "v2.9"}, time.time())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await F.settle(pilot, lambda: "driver update" in F.text_of(app.screen.cards()[0].query_one(".card-status")))
        marked = F.text_of(app.screen.cards()[0].query_one(".card-status"))
        assert marked.startswith("○ stopped") and "● driver update" in marked
        assert "driver update" not in F.text_of(app.screen.cards()[1].query_one(".card-status"))
        await pilot.press("v")
        await F.settle(pilot, lambda: app.screen.cards() and app.screen.cards()[0].compact)
        assert "● driver update" in F.text_of(app.screen.cards()[0].query_one(".card-status"))


@F.tui
async def test_home_asks_github_in_the_background_only_when_a_mac_has_a_record(host, monkeypatch):
    engine.write_record(101, "nvidia", comps(NullMoth="v2.8.1"))
    asked = []
    monkeypatch.setattr(driver_check, "latest_known", lambda get=None: asked.append(1) or {"NullMoth": "v2.9"})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: asked, what="the daily check")
        await F.settle(pilot, lambda: "driver update" in F.text_of(app.screen.cards()[0].query_one(".card-status")))
    assert asked == [1]
