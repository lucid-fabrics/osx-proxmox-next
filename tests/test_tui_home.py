"""Home, the VM view and the keys every screen shares."""
from __future__ import annotations

import pytest
from textual.widgets import Static

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens.base import HelpScreen
from osx_proxmox_next.screens.home import HomeScreen, NewStrip, VmCard, status_line
from osx_proxmox_next.widgets.chrome import KeyBar, TopBar
from osx_proxmox_next.widgets.live_screen import LiveScreen

SIZE = (100, 30)


async def home(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.pause(0.1)
    return app.screen


def keys_text(app) -> str:
    return app.screen.query_one(KeyBar).render().plain


def top_text(app) -> str:
    return app.screen.query_one(TopBar).render().plain


def test_status_line_glyphs():
    rec = F.record(1, "a", "running")
    assert status_line(rec, None) == ("●", "running")
    assert status_line(F.record(2, "b", "stopped"), None) == ("○", "stopped")
    assert status_line(rec, F.installing_driver(18, now=2000.0), now=2000.0) == ("◐", "installing · 18 min in")
    done = F.installing_driver()
    done.alive = False
    assert status_line(rec, done)[1] == "running"


@F.tui
async def test_home_lists_the_macs_as_cards_with_status_lines(monkeypatch):
    F.patch_host(monkeypatch, drivers={107: F.installing_driver(18)})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        cards = screen.cards()
        assert [c.record.vmid for c in cards] == [101, 107, 104]
        assert app.focused is cards[0]
        assert "3 on this host" in F.text_of(screen.query_one("#home-sub"))
        statuses = [F.text_of(c.query_one(".card-status")) for c in cards]
        assert statuses[0].startswith("● running") and "8 cores" in statuses[0]
        assert statuses[1].startswith("◐ installing · 18 min in")
        assert statuses[2].startswith("○ stopped") and "4 cores · 8 GB" in statuses[2]
        assert "macos-sequoia selected" in F.text_of(screen.query_one("#selected-line"))
        assert "host ready" in top_text(app) and "pve" in top_text(app)
        assert screen.columns() == 3
        for key in ("Select", "Open", "Stop", "Edit", "GPU", "Remove", "New Mac", "Host", "Help", "Quit"):
            assert key in keys_text(app)


@F.tui
async def test_thumbnails_show_the_screen_or_a_placeholder(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        lives = {c.record.vmid: c.query_one(LiveScreen) for c in screen.cards()}
        await F.settle(pilot, lambda: lives[101].grabs > 0 and lives[107].grabs > 0)
        assert lives[101].placeholder == "" and lives[107].placeholder == ""
        assert lives[104].placeholder == "powered off" and lives[104].grabs == 0  # only running VMs are grabbed


@F.tui
async def test_a_failed_grab_says_no_signal_and_a_gpu_vm_says_so(monkeypatch):
    gpu_vm = F.record(110, "gpu-mac", "running", extra="hostpci0: 0000:0a:00,pcie=1\nvga: none\n")
    F.patch_host(monkeypatch, vms=[F.record(101, "a", "running"), gpu_vm], grab=lambda vmid, probe: b"")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        lives = {c.record.vmid: c.query_one(LiveScreen) for c in screen.cards()}
        await F.settle(pilot, lambda: lives[101].grabs > 0)
        assert lives[101].placeholder == "no signal"
        assert lives[110].placeholder == "screen is on the GPU" and lives[110].grabs == 0


@F.tui
async def test_arrows_move_between_cards_and_the_strip(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        cards = screen.cards()
        await pilot.press("right")
        assert app.focused is cards[1]
        await pilot.press("l")
        assert app.focused is cards[2]
        await pilot.press("right")
        assert app.focused is cards[2]            # clamped
        await pilot.press("left")
        assert app.focused is cards[1]
        await pilot.press("down")                 # below the last row: the New Mac strip
        assert isinstance(app.focused, NewStrip)
        await pilot.press("up")
        assert app.focused is cards[-1]
        await pilot.press("k")
        assert isinstance(app.focused, VmCard)
        await pilot.press("j")
        assert isinstance(app.focused, NewStrip)
        await pilot.press("left")                 # sideways on the strip does nothing
        assert isinstance(app.focused, NewStrip)
        assert "selected" in F.text_of(screen.query_one("#selected-line"))


@F.tui
async def test_the_grid_has_as_many_columns_as_cards_fit_and_scrolls(monkeypatch):
    many = [F.record(200 + i, f"mac-{i}", "stopped") for i in range(7)]
    F.patch_host(monkeypatch, vms=many)
    app = NextApp()
    async with app.run_test(size=(70, 24)) as pilot:
        screen = await home(pilot, app)
        assert screen.columns() == 2
        cards = screen.cards()
        await pilot.press("down")
        assert app.focused is cards[2]            # one row down is two cards on
        for _ in range(2):
            await pilot.press("down")
        assert app.focused is cards[6]
        await pilot.press("down")
        assert isinstance(app.focused, NewStrip)
        assert screen.query_one("#cards").max_scroll_y > 0


@F.tui
async def test_a_narrow_terminal_and_the_v_key_use_compact_rows(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=(60, 24)) as pilot:
        screen = await home(pilot, app)
        assert all(c.compact for c in screen.cards()) and not screen.query(LiveScreen)
        assert screen.columns() == 1
        text = F.text_of(screen.cards()[0].query_one(".card-status"))
        assert "101" in text and "macos-sequoia" in text and "running" in text
        await pilot.press("v")
        await F.settle(pilot, lambda: not screen.cards()[0].compact)
        assert screen.query(LiveScreen)
        await pilot.press("v")
        await F.settle(pilot, lambda: screen.cards()[0].compact)


@F.tui
async def test_v_switches_a_wide_terminal_to_the_list(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("v")
        await F.settle(pilot, lambda: all(c.compact for c in screen.cards()))
        assert screen.cards()[0].has_focus
        await pilot.resize_terminal(60, 24)  # resizing across the threshold rebuilds the cards
        await pilot.pause(0.3)
        assert all(c.compact for c in screen.cards())


@F.tui
async def test_resizing_across_the_threshold_switches_the_layout(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert not screen.cards()[0].compact
        await pilot.resize_terminal(60, 24)
        await F.settle(pilot, lambda: screen.cards()[0].compact)
        await pilot.resize_terminal(100, 30)
        await F.settle(pilot, lambda: not screen.cards()[0].compact)


@F.tui
async def test_an_empty_host_makes_the_new_mac_strip_the_main_element(monkeypatch):
    F.patch_host(monkeypatch, vms=[])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        strip = screen.query_one(NewStrip)
        assert strip.empty and isinstance(app.focused, NewStrip)
        assert not screen.query_one("#cards").display
        assert "no Macs yet" in strip.render().plain and "Sequoia 15" in strip.render().plain
        assert "128 GB fits this host" in strip.render().plain
        await pilot.press("e", "d", "s")  # nothing is selected: these do nothing
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("enter")
        from osx_proxmox_next.screens.new_mac import NewMacScreen
        await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen))


@F.tui
async def test_the_strip_says_when_the_disk_does_not_fit(monkeypatch):
    F.patch_host(monkeypatch, vms=[])
    monkeypatch.setattr("osx_proxmox_next.screens.home.detect_storage_free_gb", lambda: {"local-lvm": 90})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert "only 90 GB free" in screen.query_one(NewStrip).render().plain
    monkeypatch.setattr("osx_proxmox_next.screens.home.detect_storage_free_gb", lambda: {})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert "128 GB disk" in screen.query_one(NewStrip).render().plain


@F.tui
async def test_a_failing_host_shows_how_many_checks_to_fix(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await F.settle(pilot, lambda: "2 to fix" in top_text(app))


@F.tui
async def test_preflight_runs_in_the_background_and_never_gates_the_app(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        assert isinstance(app.screen, HomeScreen)  # usable at once
        await F.settle(pilot, lambda: app.host_checks is not None)
        app.host_checks = None
        app._refresh_bars()
        assert "checking host" in top_text(app)


@F.tui
async def test_resume_banner_for_a_gpu_setup_in_the_middle(monkeypatch):
    F.patch_host(monkeypatch)
    state = {"finished": False, "gpu": {"bdf": "0000:0a:00"}, "step": 6, "vm": 101}
    monkeypatch.setattr("osx_proxmox_next.screens.home.host_setup.load_state", lambda root="/": state)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        banner = F.text_of(screen.query_one("#resume"))
        assert "Graphics card setup for macos-sequoia is waiting at step 6 of 8" in banner and "g to continue" in banner
        assert screen.query_one("#resume").display
    state["vm"] = None
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert "setup is waiting" in F.text_of(screen.query_one("#resume"))
    state["finished"] = True
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert not screen.query_one("#resume").display


@F.tui
async def test_a_broken_gpu_state_file_is_ignored(monkeypatch):
    F.patch_host(monkeypatch)

    def boom(root="/"):
        raise OSError("unreadable")

    monkeypatch.setattr("osx_proxmox_next.screens.home.host_setup.load_state", boom)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert not screen.query_one("#resume").display


@F.tui
async def test_a_failing_vm_list_shows_the_empty_state(monkeypatch):
    F.patch_host(monkeypatch)

    def boom():
        raise OSError("qm is gone")

    monkeypatch.setattr("osx_proxmox_next.screens.home.list_macos_vm_records", boom)
    monkeypatch.setattr("osx_proxmox_next.screens.home.detect_storage_free_gb", boom)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert screen.records == [] and screen.query_one(NewStrip).empty


@F.tui
async def test_refresh_updates_cards_in_place_and_picks_up_new_macs(monkeypatch):
    vms = F.three_vms()
    F.patch_host(monkeypatch, vms=vms)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        first = screen.cards()[0]
        vms[0] = F.record(101, "macos-sequoia", "stopped")
        await pilot.press("r")
        await F.settle(pilot, lambda: screen.cards()[0].record.status == "stopped")
        assert screen.cards()[0] is first            # same card, updated in place
        vms.append(F.record(120, "new-one", "stopped"))
        await pilot.press("r")
        await F.settle(pilot, lambda: len(screen.cards()) == 4)
        assert screen.cards()[0].has_focus or isinstance(app.focused, VmCard)
        assert "Start" in keys_text(app)


@F.tui
async def test_esc_never_quits_and_q_quits(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("escape")
        assert app.is_running and isinstance(app.screen, HomeScreen)
        await pilot.press("q")
    assert not app.is_running


@F.tui
async def test_help_lists_the_keys_of_the_screen_and_closes(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        shown = F.text_of(app.screen.query_one("#help-keys"))
        for word in ("Open", "Start or Stop", "Edit", "Graphics card", "Remove", "New Mac", "Host check", "Refresh",
                     "Switch cards and list", "Quit", "Command palette", "Stay here (esc never quits)"):
            assert word in shown
        await pilot.press("question_mark")
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("f1")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("f1")  # does not stack a second help screen
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("f1")
        await pilot.press("escape")
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("f1")
        await pilot.press("enter")
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("q")  # q is not swallowed by the help overlay's absence


@F.tui
async def test_q_inside_a_dialog_does_not_quit(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("f1")
        await pilot.press("q")
        assert app.is_running and isinstance(app.screen, HelpScreen)


@F.tui
async def test_the_command_palette_lists_every_destination_and_every_mac(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        from osx_proxmox_next.app import OsxCommands
        provider = OsxCommands(app.screen)
        found = [hit.text async for hit in provider.discover()]
        assert found[:3] == ["New Mac", "Host check", "GPU passthrough"]
        assert "Open macos-ventura" in found and "Open build-sonoma" in found
        hits = [hit async for hit in provider.search("ventura")]
        assert any("ventura" in str(h.match_display) for h in hits)
        hits = [hit async for hit in provider.search("host")]
        assert hits
        await pilot.press("ctrl+p")
        await pilot.pause(0.2)
        assert type(app.screen).__name__ == "CommandPalette"
        await pilot.press("escape")


@F.tui
async def test_footer_and_top_bar_are_clickable_and_one_row(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert screen.query_one(KeyBar).size.height == 1 and screen.query_one(TopBar).size.height == 1
        await pilot.click(KeyBar, offset=(2, 0))  # the arrows: no action
        assert isinstance(app.screen, HomeScreen)


@F.tui
async def test_clicking_a_card_selects_it_and_a_second_click_opens_it(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.click("#vm-104")
        assert app.focused is screen.cards()[2] and isinstance(app.screen, HomeScreen)
        try:
            await pilot.click("#vm-104", times=2)
        except TypeError:  # an older Textual cannot send a double click: hand the handler its chain
            from types import SimpleNamespace
            screen.cards()[2].on_click(SimpleNamespace(chain=2))
        from osx_proxmox_next.screens.vm_view import VmViewScreen
        await F.settle(pilot, lambda: isinstance(app.screen, VmViewScreen))
        assert app.screen.record.vmid == 104


@F.tui
async def test_clicking_the_strip_opens_new_mac(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.click(NewStrip)
        from osx_proxmox_next.screens.new_mac import NewMacScreen
        await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen))


@F.tui
async def test_n_h_and_g_open_their_screens_and_esc_comes_back(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        from osx_proxmox_next.screens.host_check import HostCheckScreen
        from osx_proxmox_next.screens.new_mac import NewMacScreen
        await pilot.press("n")
        await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen))
        await pilot.press("escape")
        await F.settle(pilot, lambda: app.screen is screen)
        await pilot.press("h")
        await F.settle(pilot, lambda: isinstance(app.screen, HostCheckScreen))
        await pilot.press("escape")
        await F.settle(pilot, lambda: app.screen is screen)


@F.tui
async def test_enter_opens_the_vm_view_with_the_big_screen_and_facts(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("enter")
        from osx_proxmox_next.screens.vm_view import VmViewScreen
        await F.settle(pilot, lambda: isinstance(app.screen, VmViewScreen))
        view = app.screen
        facts = F.text_of(view.query_one("#vm-facts"))
        assert "101" in facts and "8" in facts and "16 GB" in facts and "128 GB on local-lvm" in facts
        assert "vmxnet3 on vmbr0" in facts and "virtual, shown here" in facts
        assert "macos-sequoia" in top_text(app)
        live = view.query_one("#vm-live", LiveScreen)
        await F.settle(pilot, lambda: live.grabs > 0)
        assert live.interval == 2 and live.placeholder == ""
        assert "live from VM 101" in F.text_of(view.query_one("#vm-live-caption"))
        keys = keys_text(app)
        assert "Stop" in keys and "Edit" in keys and "Remove" in keys and "Back" in keys
        await pilot.press("escape")
        await F.settle(pilot, lambda: app.screen is screen)


@F.tui
async def test_the_vm_view_of_a_stopped_mac_and_its_reload(monkeypatch):
    F.patch_host(monkeypatch)
    from osx_proxmox_next.services import VmInfo
    info = {"value": VmInfo(104, "build-sonoma", "stopped", F.three_vms()[2].config_raw)}
    monkeypatch.setattr("osx_proxmox_next.screens.vm_view.fetch_vm_info", lambda vmid, adapter=None: info["value"])
    monkeypatch.setattr("osx_proxmox_next.screens.vm_view.get_proxmox_adapter", lambda: None)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        await pilot.press("l", "l", "enter")
        from osx_proxmox_next.screens.vm_view import VmViewScreen
        await F.settle(pilot, lambda: isinstance(app.screen, VmViewScreen))
        view = app.screen
        assert view.query_one("#vm-live", LiveScreen).placeholder == "powered off" and "Start" in keys_text(app)
        info["value"] = VmInfo(104, "renamed", "running", F.three_vms()[0].config_raw)
        view._reload()
        await F.settle(pilot, lambda: view.record.name == "renamed")
        assert "Stop" in keys_text(app)
        info["value"] = None
        view._reload()
        await pilot.pause(0.3)
        assert view.record.name == "renamed"  # a failed read keeps what is shown
        view.after_change(removed=True)
        await F.settle(pilot, lambda: app.screen is screen)


@F.tui
async def test_a_vm_with_a_running_install_opens_the_build_screen(monkeypatch):
    F.patch_host(monkeypatch, drivers={107: F.installing_driver(18)})
    monkeypatch.setattr("osx_proxmox_next.screens.vm_view.read_driver_state", lambda vmid: F.installing_driver(18))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("right", "enter")
        from osx_proxmox_next.screens.build import BuildScreen
        await F.settle(pilot, lambda: isinstance(app.screen, BuildScreen))
        assert app.screen.pipe.safe_to_close and app.screen.pipe.vmid == 107


@F.tui
async def test_open_vm_for_an_unknown_id_says_so(monkeypatch):
    F.patch_host(monkeypatch)
    monkeypatch.setattr("osx_proxmox_next.screens.vm_view.fetch_vm_info", lambda vmid, adapter=None: None)
    monkeypatch.setattr("osx_proxmox_next.screens.vm_view.get_proxmox_adapter", lambda: None)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        app.open_vm(999)
        await pilot.pause(0.2)
        assert app.screen is screen
        from osx_proxmox_next.services import VmInfo
        monkeypatch.setattr("osx_proxmox_next.screens.vm_view.fetch_vm_info",
                            lambda vmid, adapter=None: VmInfo(999, "late", "stopped", "name: late\ncores: 4\nmemory: 8192\n"))
        app.open_vm(999)
        from osx_proxmox_next.screens.vm_view import VmViewScreen
        await F.settle(pilot, lambda: isinstance(app.screen, VmViewScreen))
        assert app.screen.record.cores == 4


@F.tui
async def test_s_starts_a_stopped_mac(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.QmRecorder()
    monkeypatch.setattr("osx_proxmox_next.screens.vm_actions.get_proxmox_adapter", lambda: qm)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("l", "l", "s")
        await F.settle(pilot, lambda: ("start", "104") in qm.calls)


@F.tui
async def test_s_on_a_running_mac_asks_shut_down_or_force_stop(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.QmRecorder()
    monkeypatch.setattr("osx_proxmox_next.screens.vm_actions.get_proxmox_adapter", lambda: qm)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("s")
        from osx_proxmox_next.screens.base import ChoiceDialog
        assert isinstance(app.screen, ChoiceDialog)
        assert [b.name for b in app.screen.query("Button")] == ["cancel", "shutdown", "stop"]
        assert app.focused.name == "shutdown"
        await pilot.press("escape")
        assert qm.calls == []
        await pilot.press("s")
        await pilot.press("enter")
        await F.settle(pilot, lambda: ("shutdown", "101") in qm.calls)
        await pilot.press("s")
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: ("stop", "101") in qm.calls)


@F.tui
async def test_a_failing_qm_command_is_reported(monkeypatch):
    F.patch_host(monkeypatch)
    qm = F.QmRecorder(ok=False)
    monkeypatch.setattr("osx_proxmox_next.screens.vm_actions.get_proxmox_adapter", lambda: qm)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("l", "l", "s")
        await F.settle(pilot, lambda: bool(qm.calls))
        await pilot.pause(0.2)
        assert any("failed" in n.message for n in app._notifications)


@F.tui
async def test_installing_vms_are_seen_through_the_driver_state(monkeypatch):
    F.patch_host(monkeypatch, drivers={107: F.installing_driver(5)})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await home(pilot, app)
        assert screen.drivers[107].installing
