"""The driver of a Mac that has a card: the Driver block, the approval, the run, the first boot question, r and u."""
from __future__ import annotations

import json
import time

import pytest
from textual.widgets import Input, Static

import tui_fakes as F
from driver_fakes import DPve, comps
from osx_proxmox_next import driver_check, driver_engine as engine, driver_spawn, gpu
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
    state = {"view": DriverView(plan(installed={"NullMoth": "v2.8.1"}), releases()), "spawned": [], "views": [], "put_back": [], "removed": [],
             "undo": NEW_SNAP}

    def load(vmid, choice=None, releases=True):
        state["views"].append(choice)
        view = state["view"]
        if choice is not None and choice.nullmoth:
            target = nv_target(choice.nullmoth)
            verdict = engine.judge(view.plan.installed, target.versions())["state"] if view.plan.installed else "unknown"
            view = DriverView(plan(installed=view.plan.installed, target=target, verdict=verdict), view.releases)
        return view
    monkeypatch.setattr(drv, "load_view", load)
    monkeypatch.setattr(driver_spawn, "spawn_update", lambda vmid, choice, finish=False, accept_risk=False, no_snapshot=False, snapshot="":
                        state["spawned"].append((vmid, choice, finish, accept_risk, no_snapshot, snapshot)) or "/var/log/x")
    monkeypatch.setattr(drv, "put_back", lambda vmid, env: state["put_back"].append(vmid) or NEW_SNAP)
    monkeypatch.setattr(drv, "remove_safety_snapshot", lambda vmid, env, name="": state["removed"].append((vmid, name)) or NEW_SNAP)

    def undo_target(vmid, env):
        if state["undo"] != NEW_SNAP:
            raise DriverError(state["undo"])
        return NEW_SNAP
    monkeypatch.setattr(drv, "undo_target", undo_target)
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
        assert "Driver" in row and "NullMoth v2.8.1" in row and "available" not in row     # the installed version has its own row
        assert "Status" in text(app, "#d-status") and "v2.9 available" in text(app, "#d-status")
        picker = screen.query_one("#d-version", ChoiceRow)
        assert picker.value == "v2.9" and [o.value for o in picker.options] == ["v2.9", "v2.9-rc1", "v2.8.1"]
        assert "(latest)" in picker.options[0].label and "(pre-release)" in picker.options[1].label
        facts = F.text_of(screen.query_one("#d-facts"))
        assert "The Mac stops for about 10 minutes." in facts and "The card comes off and goes back on." in facts and NEW_SNAP in facts
        items = footer(app)
        assert "Update driver" in items and "Version" in items and "See every change" in items
        assert "Undo driver update" not in items and "Put everything back" not in items     # nothing to undo before an update ran
        assert "A snapshot is saved first, so the update can be undone." in text(app, "#g-safe") and " u " not in text(app, "#g-safe")
        assert screen.crumb() == "macos-sequoia › Graphics card"       # the update itself is not beta


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
        assert "Lilu 1.7.2" in text(app, "#d-driver") and "WhateverGreen 1.7.0" in text(app, "#d-driver-2")
        assert "✓ up to date" in text(app, "#d-status")
        assert not screen.query("#d-version") and "pins" in text(app, "#d-pinned")
        assert not screen.query("#d-go") and "Nothing to update." in text(app, "#g-note")      # no greyed button that reads as broken
        assert "enter" not in footer(app) and "See every change" in footer(app) and "esc" in footer(app)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, DriverScreen)
        assert not screen.query("#g-side") and not screen.query("#d-facts")     # nothing to do: no "What happens" panel
        assert "saved first" not in text(app, "#g-safe") and "A snapshot called" not in F.text_of(screen)


@F.tui
async def test_an_up_to_date_screen_shows_no_panel_and_no_new_snapshot_name(host):
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.9"}, verdict="current", older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert not screen.query("#g-side") and "What happens" not in F.text_of(screen)
        assert "osx-next-driver-" not in F.text_of(screen)                  # no name of a snapshot that nobody would save
        assert "Driver snapshot of 2026-11-21 10:30" in text(app, "#g-safe")    # the one that exists is told by its date
        await pilot.press("right")                                          # picking another release makes an update to show again
        await F.settle(pilot, lambda: screen.touched and not screen.working and screen.query("#g-side"))
        assert "What happens" in F.text_of(screen.query_one("#g-side .side-title"))


@F.tui
async def test_a_mac_set_up_before_records_has_an_unknown_installed_version(host):
    host["view"] = DriverView(plan(installed={}, verdict="unknown"), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "installed version unknown" in text(app, "#d-driver") and "NullMoth v2.9 available" in text(app, "#d-status")
        assert "Update driver" in footer(app) and not screen.query_one("#d-go").disabled


@F.tui
async def test_an_up_to_date_nvidia_mac_offers_a_reinstall_once_the_version_row_moved(host):
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.9"}, verdict="current"), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "✓ up to date" in text(app, "#d-status") and "Update driver" not in footer(app) and "Reinstall" not in footer(app)
        assert not screen.query("#d-go") and "Nothing to update. ←→ picks another release" in text(app, "#g-note")
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
        assert "v2.9-rc1 available" in text(app, "#d-status")


@F.tui
async def test_p_shows_every_change_and_x_the_commands(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("p")
        await F.settle_screen(pilot, app, ChangesDialog)
        body = F.text_of(app.screen.query_one("#changes-text"))
        assert "Update the NVIDIA driver of macos-sequoia (VM 101)" in body and "  3. Stage NullMoth v2.9" in body
        await pilot.press("x")
        await pilot.pause(0.1)
        commands = F.text_of(app.screen.query_one("#changes-text"))
        assert "qm snapshot 101 " in commands and "qm shutdown" not in commands          # the Mac is stopped: nothing to shut down
        assert "--sata1 " in commands and "--sata2 " in commands and "AMFIPass.kext" in commands and "(r, driver-undo)" in commands
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
        assert "asked once, never again" not in text_all      # a driver update is approved every time
        dialog.query_one("#g-ack", Input).value = ACK_PHRASE
        await pilot.pause(0.1)
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run", what="the update to be started")
        vmid, choice, finish, risk, nosnap, snapshot = host["spawned"][0]
        assert (vmid, choice.nullmoth, finish, risk, nosnap) == (101, "", False, True, False)
        assert snapshot == NEW_SNAP                  # the run takes the name the plan and the confirmation showed


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
    assert engine.acquire_lock(101)                # a live process runs it
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
        assert "Safe to leave" in text(app, "#g-run-note") and " u " not in text(app, "#g-run-note")
    engine.release_lock(101)


@F.tui
async def test_a_run_that_stopped_offers_to_finish_or_put_back(host):
    write_state(phase="failed", states=("done", "done", "done", "fail", "pending", "pending", "pending"),
                error="Staging failed: the script left no result.")
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, interrupted=True), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert screen.mode == "attached"           # nobody runs it: the Mac's screen says it was cut off
        assert "The driver update stopped before it finished." in text(app, "#d-unfinished")
        assert "enter finishes it, r undoes the driver update." in " ".join(text(app, "#d-unfinished").split())
        assert "Finish" in footer(app) and "Undo driver update" in footer(app) and screen.query_one("#d-go").label.plain == "Finish"
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run")
        assert host["spawned"][0][2] is True and host["spawned"][0][3] is True       # finish, the notice was accepted before


@F.tui
async def test_a_run_that_died_shows_a_failure_and_enter_finishes_it(host):
    write_state(phase="running")
    assert engine.acquire_lock(101)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        engine.release_lock(101)
        await F.settle(pilot, lambda: screen.error, what="the run to be seen as stopped")
        await F.settle(pilot, lambda: "The update did not finish" in text(app, "#g-box-title"))
        assert "The update stopped before it finished." in text(app, "#g-box-text") and "r undoes the driver update." in text(app, "#g-box-text")
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
async def test_yes_then_remove_removes_the_driver_snapshot_the_screen_named(host):
    write_state(phase="done", states=("done",) * 7)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("y")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("tab", "enter")
        await F.settle_screen(pilot, app, HomeScreen)
        assert host["removed"] == [(101, NEW_SNAP)]


@F.tui
async def test_a_removal_that_fails_is_reported(host, monkeypatch):
    def fail(vmid, env, name=""):
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
async def test_r_undoes_the_driver_update_after_a_confirmation_with_cancel_focused(host):
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "Undo driver update" in footer(app) and "r" in text(app, "#g-safe") and "2026-11-21 10:30" in text(app, "#g-safe")
        await pilot.press("r")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        said = " ".join(" ".join(F.text_of(w).split()) for w in app.screen.query("Static"))
        assert "its disks go back to the snapshot of 2026-11-21 10:30" in said and drv.UNDO_LOSS_LINE in said
        assert app.screen.query_one("#choice-no").has_focus
        keys = app.screen.query_one(KeyBar).render().plain
        assert "enter Cancel" in keys and "tab, then enter Undo driver update" in keys     # the footer names the key that confirms
        assert "Changes made in macOS since the update are lost" in said and "Enter keeps the Mac as it is" in said
        await pilot.press("escape")                             # cancel is the default: nothing happens
        await F.settle_screen(pilot, app, DriverScreen)
        assert host["put_back"] == []
        await pilot.press("r")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("tab", "enter")
        await F.settle(pilot, lambda: host["put_back"] == [101] and not screen.working, what="the update to be undone")


@F.tui
async def test_r_without_a_snapshot_says_there_is_nothing_to_undo(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("r")
        await pilot.pause(0.2)
        assert isinstance(app.screen, DriverScreen) and host["put_back"] == []


@F.tui
async def test_an_undo_that_the_checks_refuse_is_said_and_asks_nothing(host):
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    host["undo"] = engine.REFUSE_CARD_CHANGED
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("r")
        await F.settle(pilot, lambda: not screen.working, what="the check to finish")
        await pilot.pause(0.2)
        assert isinstance(app.screen, DriverScreen) and host["put_back"] == []


@F.tui
async def test_u_is_not_on_the_driver_screen_because_it_acts_host_wide(host):
    gpu.record_path(101).parent.mkdir(parents=True, exist_ok=True)
    gpu.record_path(101).write_text(json.dumps({"vmid": 101, "bdf": "0000:01:00", "vendor": "nvidia"}))
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    assert "u" not in [b.key for b in DriverScreen.BINDINGS] and not hasattr(DriverScreen, "action_undo_card")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "Put everything back" not in footer(app) and "Undo driver update" in footer(app)   # even with a card setup record
        pushed = []
        app.push_screen = lambda target, *a, **k: pushed.append(target)
        await pilot.press("u")
        await pilot.pause(0.2)
        assert pushed == [] and isinstance(app.screen, DriverScreen) and screen.mode == "attached"


def hold_put_back(monkeypatch, outcome, steps=("stop",)):
    """Replace put_back by one that reports *steps* as done, then waits for the test before it ends with *outcome*."""
    import threading
    gate = threading.Event()

    def put_back(vmid, env):
        for sid in steps:
            env.on_step(sid, "active")
            env.on_step(sid, "done")
        env.on_step("release", "active")
        gate.wait(10)
        if isinstance(outcome, Exception):
            env.on_step("release", "fail")
            raise outcome
        return NEW_SNAP
    monkeypatch.setattr(drv, "put_back", put_back)
    return gate


async def confirm_undo(pilot, screen):
    await pilot.press("r")
    await F.settle(pilot, lambda: type(screen.app.screen).__name__ == "ChoiceDialog")
    await pilot.press("tab", "enter")


@F.tui
async def test_the_undo_shows_its_steps_from_the_first_second(host, monkeypatch):
    gate = hold_put_back(monkeypatch, None)
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await confirm_undo(pilot, screen)
        await F.settle(pilot, lambda: screen.mode == "undo" and "◐ Release the disks" in text(app, "#g-timeline"), what="the undo steps")
        line = text(app, "#g-timeline")
        assert "✓ Stopped the Mac" in line and "· Put the snapshot back" in line and "· Start the Mac" in line     # like the update
        assert "Undoing the driver update of macos-sequoia" in text(app, "#g-run-title") and "Keep this screen open" in text(app, "#g-box-text")
        await pilot.press("escape")                                   # leaving mid-undo waits
        await pilot.pause(0.1)
        assert screen.mode == "undo"
        gate.set()
        await F.settle(pilot, lambda: screen.mode == "attached" and not screen.working, what="the screen to come back")


@F.tui
async def test_a_put_back_that_fails_shows_an_error_screen_and_enter_goes_back(host, monkeypatch):
    gate = hold_put_back(monkeypatch, DriverError("Device maps still hold the disks of VM 101 (x). Remove them with: dmsetup remove x"))
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await confirm_undo(pilot, screen)
        gate.set()
        await F.settle(pilot, lambda: screen.undo_error and "The undo did not finish" in text(app, "#g-box-title"), what="the error screen")
        assert "dmsetup remove x" in text(app, "#g-box-text") and "✗ Release the disks" in text(app, "#g-timeline")
        assert "Enter" not in footer(app) and "Back" in footer(app) and screen.query_one("#g-box").has_class("fail")
        before = len(host["views"])
        await pilot.press("enter")
        await F.settle(pilot, lambda: len(host["views"]) > before and screen.mode == "attached" and not screen.working)
        assert not screen.undo_error


@F.tui
async def test_a_refused_mac_shows_why_and_cannot_be_updated(host):
    host["view"] = DriverView(plan(refusals=["This Mac is still being installed. Let that finish first."]), [])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "still being installed" in text(app, "#g-reason") and not screen.query("#d-go")
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
        await F.settle(pilot, lambda: "update" in F.text_of(app.screen.cards()[0].query_one(".card-status")))
        marked = F.text_of(app.screen.cards()[0].query_one(".card-status"))
        assert marked.startswith("○ stopped") and "● driver update" in marked and len(marked) <= 27     # the card's text is 27 cells wide
        assert "update" not in F.text_of(app.screen.cards()[1].query_one(".card-status"))
        await pilot.press("v")
        await F.settle(pilot, lambda: app.screen.cards() and app.screen.cards()[0].compact)
        assert "● driver update" in F.text_of(app.screen.cards()[0].query_one(".card-status"))     # the list has the room for the whole word


@F.tui
async def test_home_asks_github_in_the_background_only_when_a_mac_has_a_record(host, monkeypatch):
    engine.write_record(101, "nvidia", comps(NullMoth="v2.8.1"))
    asked = []
    monkeypatch.setattr(driver_check, "latest_known", lambda get=None: asked.append(1) or {"NullMoth": "v2.9"})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: asked, what="the daily check")
        await F.settle(pilot, lambda: "update" in F.text_of(app.screen.cards()[0].query_one(".card-status")))
    assert asked == [1]


# ── an update that stopped with the card off (B2) ────────────────────

@F.tui
async def test_home_shows_an_update_that_stopped_and_g_opens_its_driver_screen_though_the_card_is_off(host):
    write_state(phase="failed", states=("done", "done", "done", "fail", "pending", "pending", "pending"))
    engine.write_update(104, {"kind": "nvidia", "phase": "failed", "snapshot": NEW_SNAP, "steps": []})     # build-sonoma: no card on it now
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, interrupted=True, older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await F.settle(pilot, lambda: "driver update stopped" in F.text_of(app.screen.cards()[1].query_one(".card-status")))
        stopped = F.text_of(app.screen.cards()[1].query_one(".card-status"))
        assert stopped == "● driver update stopped" and len(stopped) <= 26
        await pilot.press("right")
        await pilot.press("g")
        await F.settle_screen(pilot, app, DriverScreen)             # not the choice of a card: the update is waiting
        await F.settle(pilot, lambda: app.screen.mode == "attached")
        await pilot.pause(0.1)
        assert "Finish" in footer(app) and "Undo driver update" in footer(app)
        await pilot.press("v")


@F.tui
async def test_the_screen_of_an_update_that_stopped_says_the_card_is_off(host):
    p = plan(installed={"NullMoth": "v2.8.1"}, interrupted=True, older_snapshots=[NEW_SNAP])
    p.cfg = {"name": "macos-sequoia"}                                # no hostpci0
    host["view"] = DriverView(p, releases())
    write_state(phase="failed", states=("done", "done", "done", "fail", "pending", "pending", "pending"))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "Finish goes on from the step that stopped." in text(app, "#g-note")
        said = " ".join(text(app, "#d-unfinished").split())
        assert said == "! The driver update stopped before it finished. The card is off this Mac. enter finishes it, r undoes the driver update."
        assert not screen.query("#d-version") and screen.query_one("#d-go").label.plain == "Finish"


@F.tui
async def test_a_passed_through_device_that_is_no_graphics_card_keeps_the_card_choice(host, monkeypatch):
    monkeypatch.setattr(engine, "live_kind", lambda cfg, root="/sys": ("", True))       # an Intel USB controller on hostpci0
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await pilot.press("g")
        await F.settle_screen(pilot, app, GpuScreen)


# ── the Driver row never wraps (design) ──────────────────────────────

@pytest.mark.parametrize("size", [(100, 30), (80, 24)])
@F.tui
async def test_the_driver_and_status_rows_stay_on_one_line(host, size):
    host["view"] = DriverView(plan("intel", installed={}, verdict="unknown", target=Target("intel", {
        "WhateverXe": {"version": "1.3.0", "tag": "v1.3.0", "sha256": "", "source": "latest"},
        "WhateverXe Graphics": {"version": "0.9.2", "tag": "v0.9.2", "sha256": "", "source": "latest"}})), [])
    app = NextApp()
    async with app.run_test(size=size) as pilot:
        screen = await open_driver(pilot, app)
        for row in ("#d-driver", "#d-status"):
            assert screen.query_one(row).size.height == 1, (row, size, text(app, row))
        assert "installed version unknown" in text(app, "#d-driver") and "update available" in text(app, "#d-status")
        host["view"] = DriverView(plan("intel", installed={"WhateverXe": "1.2.0", "WhateverXe Graphics": "0.9.1"}, target=host["view"].plan.target), [])
        screen.load()
        await F.settle(pilot, lambda: screen.query("#d-driver-2") and not screen.working and "1.2.0" in text(app, "#d-driver"))
        for row in ("#d-driver", "#d-driver-2", "#d-status"):
            assert screen.query_one(row).size.height == 1, (row, size, text(app, row))
        assert "WhateverXe 1.2.0" in text(app, "#d-driver") and "WhateverXe Graphics 0.9.1" in text(app, "#d-driver-2")


# ── fix wave 2: the keyboard after a TUI-launched update, state text that follows state, Intel's second row ──

def stale_focus(screen):
    """What the Textual race leaves after a TUI-launched update: the focus on the version row that the run view removed."""
    from osx_proxmox_next.widgets.rows import Option
    screen.focused = ChoiceRow("Version", [Option("a", "a")], "a")


async def launch(pilot, app, host):
    screen = await open_driver(pilot, app)
    await pilot.press("enter")
    await F.settle_screen(pilot, app, BeforeDialog)
    app.screen.query_one("#g-ack", Input).value = ACK_PHRASE
    await pilot.pause(0.1)
    await pilot.press("enter")
    await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run", what="the update to be started")
    return screen


@F.tui
async def test_the_keys_work_after_an_update_the_tui_started(host):
    """r, y, n, esc and p all did nothing after a TUI-launched update (the mouse still worked): the focus sat on the
    removed version row, and a key goes to the focused widget first, which is no longer on the screen."""
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await launch(pilot, app, host)
        stale_focus(screen)
        await F.settle(pilot, lambda: screen.focused is None, what="the focus to be taken off the removed row")
        await pilot.press("p")                                      # the running screen
        await F.settle_screen(pilot, app, ChangesDialog)
        await pilot.press("escape")
        await F.settle_screen(pilot, app, DriverScreen)
        write_state(phase="failed", states=("done", "done", "done", "fail", "pending", "pending", "pending"), error="Staging failed.")
        await F.settle(pilot, lambda: "The update did not finish" in text(app, "#g-box-title"), what="the failure screen")
        stale_focus(screen)
        await F.settle(pilot, lambda: screen.focused is None)
        await pilot.press("r")                                      # the failure screen: undo is offered
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("escape")
        await F.settle_screen(pilot, app, DriverScreen)
        write_state(phase="done", states=("done",) * 7)
        await F.settle(pilot, lambda: screen.mode == "first_boot", what="the first boot question")
        stale_focus(screen)
        await F.settle(pilot, lambda: screen.focused is None)
        await pilot.press("n")
        await F.settle(pilot, lambda: screen.helping)
        await pilot.press("enter")
        await F.settle(pilot, lambda: not screen.helping)
        await pilot.press("y")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        await pilot.press("enter")
        await F.settle_screen(pilot, app, HomeScreen)


@F.tui
async def test_a_mode_change_never_leaves_the_focus_on_a_widget_it_removed(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await launch(pilot, app, host)
        stale_focus(screen)
        await screen._show(Static("x"), mode="run")                 # every mode change heals it
        assert screen.focused is None


@F.tui
async def test_the_failure_screen_says_the_update_stopped_and_the_run_screen_says_it_keeps_going(host):
    write_state(phase="running")
    assert engine.acquire_lock(101)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        assert "Safe to leave: the update keeps running." in text(app, "#g-run-note")
        engine.release_lock(101)
        await F.settle(pilot, lambda: screen.error, what="the run to be seen as stopped")
        await F.settle(pilot, lambda: "The update stopped. Nothing is running." in text(app, "#g-run-note"), what="the note to follow")
        assert "keeps running" not in text(app, "#g-run-note") and screen.query_one("#g-box").has_class("fail")


@F.tui
async def test_a_finish_that_just_started_is_not_shown_as_failed_by_the_state_the_last_run_left(host):
    write_state(phase="running", error="")                          # a run that was killed: it says running, nobody holds the lock
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, interrupted=True), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("enter")                                  # Finish
        await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run")
        await pilot.pause(1.3)                                      # ticks while the spawned process has not taken its lock yet
        assert not screen.error and "did not finish" not in text(app, "#g-box-title") and "Updating the NVIDIA driver" in text(app, "#g-run-title")
        assert "Safe to leave" in text(app, "#g-run-note")
        assert engine.acquire_lock(101)                              # the process is up: it holds the lock and writes its state
        write_state(phase="running")
        await pilot.pause(1.3)
        assert not screen.error and "Updating the driver" in text(app, "#g-box-title")
        engine.release_lock(101)
        screen.spawned_at = time.time() - 1000                      # it stopped long after it started: now it is a failure
        await F.settle(pilot, lambda: screen.error, what="the stop to be noticed")
        assert "did not finish" in text(app, "#g-box-title")


@F.tui
async def test_the_run_title_has_no_double_space_before_the_state_is_written(host):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        screen._spawn(Choice())
        await F.settle(pilot, lambda: screen.mode == "run")
        title = text(app, "#g-run-title")
        assert title == "Updating the NVIDIA driver of macos-sequoia" and "  " not in title      # the plan knows the kind
        screen.view = None
        assert screen._run_head({}) == "Updating the driver of macos-sequoia" and "  " not in screen._run_head({})


def intel_view(**kw):
    target = Target("intel", {"WhateverXe": {"version": "0.8.3", "tag": "v0.8.3", "sha256": "", "source": "latest"},
                              "WhateverXe Graphics": {"version": "0.4.4", "tag": "v0.4.4", "sha256": "", "source": "latest"}})
    gfx = [{"tag": "v0.4.4", "prerelease": False, "published": "2026-11-18"}, {"tag": "v0.4.3", "prerelease": False, "published": "2026-11-10"}]
    wex = [{"tag": "v0.8.3", "prerelease": False, "published": "2026-11-18"}, {"tag": "v0.8.0", "prerelease": False, "published": "2026-10-01"}]
    return DriverView(plan("intel", {"WhateverXe": "0.8.0", "WhateverXe Graphics": "0.4.1"}, target, **kw), wex, gfx)


@F.tui
async def test_an_intel_mac_picks_the_graphics_package_release_on_a_second_row(host):
    host["view"] = intel_view()
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        rows = list(screen.query(ChoiceRow))
        assert [r.id for r in rows] == ["d-version", "d-graphics"] and rows[0].label == "Version" and rows[1].label == "Package"
        assert [o.value for o in rows[1].options] == ["v0.4.4", "v0.4.3"] and rows[1].value == "v0.4.4"
        assert rows[0].has_focus and "Row" in footer(app)
        await pilot.press("down")
        await F.settle(pilot, lambda: rows[1].has_focus)
        await pilot.press("right")                                  # v0.4.3
        await F.settle(pilot, lambda: screen.choice.graphics == "v0.4.3" and not screen.working)
        assert screen.choice.wex == "" and host["views"][-1].graphics == "v0.4.3"
        await pilot.press("up")
        await F.settle(pilot, lambda: screen.query_one("#d-version").has_focus)
        await pilot.press("right")
        await F.settle(pilot, lambda: screen.choice.wex == "v0.8.0" and screen.choice.graphics == "v0.4.3" and not screen.working)
        await pilot.press("enter")
        await F.settle_screen(pilot, app, BeforeDialog)
        await pilot.press("escape")
        await F.settle_screen(pilot, app, DriverScreen)
        screen._approved({})
        await F.settle(pilot, lambda: host["spawned"])
        choice = host["spawned"][0][1]
        assert (choice.wex, choice.graphics, choice.nullmoth) == ("v0.8.0", "v0.4.3", "")


def test_load_view_reads_the_graphics_releases_for_an_intel_mac(monkeypatch):
    monkeypatch.setattr(drv, "prepare", lambda *a, **k: plan("intel", target=amd_target()))
    monkeypatch.setattr(drv.igpu_engine, "list_releases", lambda kind: [{"tag": kind + "-1", "prerelease": False}])
    view = drv.load_view(101)
    assert [r["tag"] for r in view.releases] == ["whateverxe-1"] and [r["tag"] for r in view.graphics] == ["graphics-1"]
    assert drv.load_view(101, releases=False).graphics == []


@F.tui
async def test_a_finish_that_was_refused_before_it_wrote_anything_is_not_shown_with_the_old_error(host):
    write_state(phase="failed", error="OLD ERROR from the last run")
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, interrupted=True), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await pilot.press("enter")
        await F.settle(pilot, lambda: host["spawned"] and screen.mode == "run")
        import os
        os.utime(engine.update_path(101), (time.time() - 2000, time.time() - 2000))     # the old run's file, never touched since
        screen.spawned_at = time.time() - 1000          # the spawned run exited at once
        await F.settle(pilot, lambda: screen.error, what="the missing start to be noticed")
        assert "did not start" in screen.error and "OLD ERROR" not in text(app, "#g-box-text")


@F.tui
async def test_an_undo_that_dies_of_an_unexpected_error_shows_the_error_screen(host, monkeypatch):
    gate = hold_put_back(monkeypatch, OSError("disk full"))
    host["view"] = DriverView(plan(installed={"NullMoth": "v2.8.1"}, older_snapshots=[NEW_SNAP]), releases())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_driver(pilot, app)
        await confirm_undo(pilot, screen)
        gate.set()
        await F.settle(pilot, lambda: screen.undo_error == "disk full" and not screen.working)


def intel_target():
    return Target("intel", {"WhateverXe": {"version": "0.8.3", "tag": "v0.8.3", "sha256": "", "source": "latest"},
                            "WhateverXe Graphics": {"version": "0.4.4", "tag": "v0.4.4", "sha256": "", "source": "latest"}})


@F.tui
async def test_the_dialogs_default_follows_the_risk_cancel_for_the_undo_update_for_the_update(host):
    host["view"] = DriverView(plan("intel", {"WhateverXe": "0.8.0"}, intel_target(), older_snapshots=[NEW_SNAP]), [])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_driver(pilot, app)
        await pilot.press("r")                                      # the undo loses what was done in the Mac: Cancel is the default
        await F.settle(pilot, lambda: type(app.screen).__name__ == "ChoiceDialog")
        assert app.screen.focus_value == "no" and app.screen.query_one("#choice-no").has_focus
        await pilot.press("enter")
        await F.settle_screen(pilot, app, DriverScreen)
        assert host["put_back"] == []                               # enter did not undo anything
        await pilot.press("enter")                                  # the update loses nothing: Update is the default
        await F.settle(pilot, lambda: isinstance(app.screen, BeforeDialog))
        assert app.screen.query_one("#before-go").has_focus
        assert "enter Update driver" in app.screen.query_one("#before-keys").render().plain
