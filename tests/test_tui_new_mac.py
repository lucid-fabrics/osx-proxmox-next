"""New Mac: one screen, every row, the keyboard, the capacity bars and the create path."""
from __future__ import annotations

import pytest
from textual.widgets import Button, Input, Static

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.screens.new_mac import NewMacScreen, version_options
from osx_proxmox_next.widgets.capacity import CapacityBar, bar_state
from osx_proxmox_next.widgets.rows import ChoiceRow, ExpandRow, FieldRow

SIZE = (100, 30)


async def new_mac(pilot, app, **kw):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.press("n")
    await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen) and app.screen.is_attached)
    await pilot.pause(0.2)
    return app.screen


def row_text(screen, rid):
    return screen.query_one(rid).render().plain


def inp(screen, rid) -> Input:
    return screen.query_one(rid, FieldRow).input


def test_bar_state():
    assert bar_state(1, 0) == "unknown" and bar_state(5, 10) == "ok" and bar_state(8, 10) == "warn"
    assert bar_state(11, 10) == "over"


def test_version_options_are_newest_first():
    assert [o.value for o in version_options()] == ["tahoe", "sequoia", "sonoma", "ventura", "monterey"]


@F.tui
async def test_everything_is_prefilled_from_the_host(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        assert "Sequoia 15" in row_text(screen, "#r-macos") and "Tahoe 26" in row_text(screen, "#r-macos")
        assert inp(screen, "#r-name").value == "macos-sequoia" and "VM 105" in F.text_of(screen.query_one("#r-name .field-suffix"))
        size = row_text(screen, "#r-size")
        assert "Balanced" in size and "8 cores · 16 GB · 128 GB disk" in size
        assert "local-lvm" in row_text(screen, "#r-storage") and "network vmbr0" in row_text(screen, "#r-storage")
        install = row_text(screen, "#r-install")
        assert "Hands-off" in install and "BETA" in install
        assert "Off" in row_text(screen, "#r-apple") and "no iMessage" in row_text(screen, "#r-apple")
        assert not screen.query_one("#more-box").display and not screen.query_one("#custom-box").display
        assert app.focused is screen.query_one("#r-macos")
        assert "New Mac" in F.text_of(screen.query_one("#new-title"))
        assert "New Mac" in screen.query_one("#topbar").render().plain


@F.tui
async def test_the_capacity_bars_and_the_summary_follow_the_choices(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        cpu, mem, disk = (screen.query_one(i, CapacityBar) for i in ("#bar-cpu", "#bar-mem", "#bar-disk"))
        assert cpu.summary() == "8 of 32 cores" and mem.summary() == "16 of 64 GB" and disk.summary() == "128 of 412 GB free"
        assert cpu.state == mem.state == disk.state == "ok"
        after = F.text_of(screen.query_one("#after"))
        assert "Check, download, rehearse" in after and "Create VM 105" in after and "30 to 45 min" in after
        screen.query_one("#r-install", ChoiceRow).focus()
        await pilot.press("right")
        after = F.text_of(screen.query_one("#after"))
        assert "Install macOS yourself" in after and "30 to 45 min" not in after
        assert "You finish it in the Proxmox console" in row_text(screen, "#r-install")
        await pilot.press("left")


@F.tui
async def test_left_and_right_change_choice_rows_and_the_name_follows_the_version(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        await pilot.press("left")
        await pilot.pause()
        assert "Tahoe 26" in row_text(screen, "#r-macos") and inp(screen, "#r-name").value == "macos-tahoe"
        assert screen.state.macos == "tahoe"
        await pilot.press("right", "right")
        await pilot.pause()
        assert screen.state.macos == "sonoma" and inp(screen, "#r-name").value == "macos-sonoma"
        await pilot.press("l")
        await pilot.pause()
        assert screen.state.macos == "ventura"
        await pilot.press("h")
        await pilot.pause()
        assert screen.state.macos == "sonoma"
        # an edited name is kept
        await pilot.press("down")
        assert app.focused is inp(screen, "#r-name")
        await pilot.press("x")
        assert inp(screen, "#r-name").value == "macos-sonomax" and screen.state.name_edited
        await pilot.press("up")
        await pilot.press("right")
        await pilot.pause()
        assert inp(screen, "#r-name").value == "macos-sonomax" and screen.state.macos == "ventura"


@F.tui
async def test_a_bigger_version_changes_the_balanced_disk(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        await pilot.press("left")
        assert "160 GB disk" in row_text(screen, "#r-size")


@F.tui
async def test_up_and_down_walk_every_row_and_stop_at_the_ends(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        seen = [app.focused.id]
        for _ in range(8):
            await pilot.press("down")
            fid = app.focused.id or app.focused.parent.id
            if fid != seen[-1]:
                seen.append(fid)
        assert seen == ["r-macos", "r-name-input", "r-size", "r-storage", "r-install", "r-apple", "r-more"]
        await pilot.press("up")
        assert app.focused.id == "r-apple"
        for _ in range(10):
            await pilot.press("up")
        assert app.focused.id == "r-macos"


@F.tui
async def test_size_presets_that_do_not_fit_are_skipped(monkeypatch):
    F.patch_host(monkeypatch, facts=F.host_facts(host_cores=8, storage_free_gb={"local-lvm": 100, "local": 90}))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        size = screen.query_one("#r-size", ChoiceRow)
        screen.query_one("#r-size").focus()
        enabled = {o.value: o.enabled for o in size.options}
        assert enabled == {"light": True, "balanced": True, "heavy": False, "custom": True}
        await pilot.press("right")                       # skips Heavy
        assert size.value == "custom"
        await pilot.press("left")
        assert size.value == "balanced"
        await pilot.press("left")
        assert size.value == "light" and "4 cores · 8 GB · 64 GB disk" in row_text(screen, "#r-size")
        assert "Heavy: too big" in row_text(screen, "#r-size")


@F.tui
async def test_heavy_fits_a_big_host_and_light_is_skipped_on_a_tiny_one(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-size").focus()
        await pilot.press("right")
        assert "Heavy" in row_text(screen, "#r-size") and "16 cores · 32 GB · 256 GB disk" in row_text(screen, "#r-size")
    F.patch_host(monkeypatch, facts=F.host_facts(host_cores=2, host_memory_mb=6144, vm_memory_limit_mb=5000))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        assert not next(o for o in screen.query_one("#r-size", ChoiceRow).options if o.value == "light").enabled
        assert screen.query_one("#bar-cpu", CapacityBar).state == "over"


@F.tui
async def test_custom_reveals_three_fields_in_gb(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-size").focus()
        await pilot.press("left", "left")                 # balanced -> light -> custom (wraps)
        assert screen.state.size == "custom" and screen.query_one("#custom-box").display
        assert inp(screen, "#r-cores").value == "8" and inp(screen, "#r-memory").value == "16"
        inp(screen, "#r-cores").value = "6"
        inp(screen, "#r-memory").value = "12"
        inp(screen, "#r-disk").value = "200"
        await pilot.pause()
        assert "6 cores · 12 GB · 200 GB disk" in row_text(screen, "#r-size")
        assert screen.query_one("#bar-mem", CapacityBar).summary() == "12 of 64 GB"
        # custom fields are reachable by the arrows
        screen.query_one("#r-size").focus()
        await pilot.press("down")
        assert app.focused is inp(screen, "#r-cores")


@F.tui
async def test_storage_choice_changes_the_disk_bar(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-storage").focus()
        await pilot.press("right")
        assert screen.state.storage == "local"
        bar = screen.query_one("#bar-disk", CapacityBar)
        assert bar.summary() == "128 of 90 GB free ✗ does not fit" and bar.state == "over"


@F.tui
async def test_memory_over_what_the_host_can_give_is_red_with_a_glyph(monkeypatch):
    F.patch_host(monkeypatch, facts=F.host_facts(vm_memory_limit_mb=10 * 1024))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        bar = screen.query_one("#bar-mem", CapacityBar)
        assert bar.state == "over" and "✗" in bar.summary()


@F.tui
async def test_apple_id_on_reveals_the_vmgenid_and_mac_fields_under_more(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        more = screen.query_one("#r-more", ExpandRow)
        screen.query_one("#r-apple").focus()
        await pilot.press("right")
        assert screen.state.apple_services and "iMessage, FaceTime and iCloud" in row_text(screen, "#r-apple")
        more.focus()
        await pilot.press("enter")
        assert more.open and screen.query_one("#more-box").display
        assert screen.query_one("#r-vmgenid").display and screen.query_one("#r-mac").display
        screen.query_one("#r-apple").focus()
        await pilot.press("left")
        assert not screen.query_one("#r-vmgenid").display and not screen.query_one("#r-mac").display
        more.focus()
        await pilot.press("left")                        # closes it again
        assert not more.open and not screen.query_one("#more-box").display
        await pilot.press("right")
        assert more.open
        await pilot.press("space")
        assert not more.open


@F.tui
async def test_more_holds_the_detection_defaults_and_the_hint_rules(monkeypatch):
    legacy = F.cpu()
    legacy.needs_penryn = True
    F.patch_host(monkeypatch, facts=F.host_facts(cpu=legacy))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-more", ExpandRow).set_open(True)
        await pilot.pause()
        assert inp(screen, "#r-vmid").value == "105" and inp(screen, "#r-bridge").value == "vmbr0"
        assert inp(screen, "#r-iso").value == "/var/lib/vz/template/iso" and inp(screen, "#r-vlan").value == ""
        penryn = row_text(screen, "#r-penryn")
        assert "On" in penryn and "Older Intel CPU detected" in penryn         # the hint follows detection
        assert "e1000" in row_text(screen, "#r-nic")
        assert screen.state.use_penryn and screen.state.e1000
        screen.query_one("#r-penryn").focus()
        await pilot.press("left")                                              # off: 4 -> balanced cores, custom size
        assert not screen.state.use_penryn and "Older Intel CPU detected" in row_text(screen, "#r-penryn")
        assert screen.state.size == "custom" and screen.state.custom_cores == "8"
        await pilot.press("right")
        assert screen.state.custom_cores == "4" and inp(screen, "#r-cores").value == "4"


@F.tui
async def test_a_modern_cpu_gets_the_neutral_hints(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-more", ExpandRow).set_open(True)
        await pilot.pause()
        assert "Only for older Intel CPUs" in row_text(screen, "#r-penryn")
        assert not screen.state.use_penryn and "vmxnet3" in row_text(screen, "#r-nic")
        assert "vmxnet3 is the default" not in row_text(screen, "#r-nic")
        screen.query_one("#r-penryn").focus()
        await pilot.press("right")                                             # ticking it must not change the hint
        assert "Only for older Intel CPUs" in row_text(screen, "#r-penryn") and "Older Intel CPU detected" not in row_text(screen, "#r-penryn")
        screen.query_one("#r-force").focus()
        await pilot.press("right")
        assert screen.state.force_download
        screen.query_one("#r-nic").focus()
        await pilot.press("right")
        assert screen.state.e1000


@F.tui
async def test_enter_creates_the_mac_and_opens_the_build_screen(monkeypatch):
    F.patch_host(monkeypatch)
    started = []
    from osx_proxmox_next.screens import build

    class FakeBuild(build.BuildScreen):
        def on_mount(self) -> None:
            started.append((self.config, self.pipe.unattended, self.pipe.force_download))
            self.pipe.deps.preflight = lambda: []

    monkeypatch.setattr("osx_proxmox_next.screens.build.BuildScreen", FakeBuild)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        await pilot.press("enter")
        await F.settle(pilot, lambda: bool(started))
        config, unattended, force = started[0]
        assert (config.vmid, config.name, config.macos) == (105, "macos-sequoia", "sequoia")
        assert (config.cores, config.memory_mb, config.disk_gb, config.storage) == (8, 16384, 128, "local-lvm")
        assert unattended is True and force is False and config.smbios_serial and config.net_model == "vmxnet3"
        assert not any(isinstance(s_, NewMacScreen) for s_ in app.screen_stack)   # the build replaced the form
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_the_button_creates_a_manual_install_with_a_forced_download(monkeypatch):
    F.patch_host(monkeypatch)
    started = []
    from osx_proxmox_next.screens import build

    class FakeBuild(build.BuildScreen):
        def on_mount(self) -> None:
            started.append((self.config, self.pipe.unattended, self.pipe.force_download))

    monkeypatch.setattr("osx_proxmox_next.screens.build.BuildScreen", FakeBuild)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-install").focus()
        await pilot.press("right")
        screen.query_one("#r-more", ExpandRow).set_open(True)
        await pilot.pause()
        screen.query_one("#r-force").focus()
        await pilot.press("right")
        await pilot.click("#create")
        await F.settle(pilot, lambda: bool(started))
        assert started[0][1:] == (False, True)


@F.tui
async def test_errors_show_in_two_rows_and_focus_goes_to_the_first_bad_row(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-name").focus()
        inp(screen, "#r-name").focus()
        inp(screen, "#r-name").value = "a"
        screen.query_one("#r-more", ExpandRow).set_open(True)
        await pilot.pause()
        inp(screen, "#r-bridge").value = "br0"
        inp(screen, "#r-vlan").value = "9999"
        await pilot.press("enter")
        await pilot.pause(0.2)
        shown = F.text_of(screen.query_one("#errors", Static))
        assert "✗ Name must be at least 3 characters." in shown and "✗ Bridge must match" in shown
        assert "(+1 more)" in shown and screen.query_one("#errors").size.height == 2
        assert app.focused is inp(screen, "#r-name")
        assert inp(screen, "#r-name").has_class("invalid") and inp(screen, "#r-bridge").has_class("invalid")
        assert isinstance(app.screen, NewMacScreen)
        inp(screen, "#r-name").value = "good-name"
        await pilot.pause()
        assert F.text_of(screen.query_one("#errors", Static)) == "" and not inp(screen, "#r-bridge").has_class("invalid")


@F.tui
async def test_a_bad_value_in_more_opens_it_and_a_bad_custom_size_focuses_the_size_row(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.state.vlan = "9999"                       # bad value in a row that is still folded away
        screen.action_create()
        await pilot.pause(0.2)
        assert screen.query_one("#r-more", ExpandRow).open and app.focused is inp(screen, "#r-vlan")
        screen.state.vlan = ""
        screen.state.size, screen.state.custom_cores = "balanced", "1"
        screen.state.size = "custom"
        screen.query_one("#r-size", ChoiceRow).set_value("custom")
        screen._sync()
        screen.state.custom_cores = "1"
        screen.action_create()
        await pilot.pause(0.2)
        assert "cores" in screen.errors and app.focused is inp(screen, "#r-cores")
        screen.state.size = "balanced"
        screen.query_one("#r-size", ChoiceRow).set_value("balanced")
        screen._sync()
        screen.state.custom_cores = "8"
        screen.state.size = "custom"
        screen.state.custom_memory_gb = "1"
        screen._sync()
        screen.action_create()
        await pilot.pause(0.2)
        assert "memory" in screen.errors


@F.tui
async def test_a_short_terminal_drops_the_blank_rows(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = await new_mac(pilot, app)
        assert screen.has_class("short")
        assert screen.query_one("#new-action").region.bottom <= 23
    app = NextApp()
    async with app.run_test(size=(100, 34)) as pilot:
        screen = await new_mac(pilot, app)
        assert not screen.has_class("short")


@F.tui
async def test_esc_cancels_back_to_home_and_typing_a_letter_in_a_field_is_text(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        await pilot.press("down")
        await pilot.press("q", "n", "h", "?")           # single letters are text inside a field
        assert app.is_running and isinstance(app.screen, NewMacScreen)
        assert inp(screen, "#r-name").value.endswith("qnh?")
        await pilot.press("f1")                          # but f1 still opens help
        from osx_proxmox_next.screens.base import HelpScreen
        assert isinstance(app.screen, HelpScreen)
        assert "Create this Mac" in F.text_of(app.screen.query_one("#help-keys"))
        await pilot.press("escape")
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_the_choice_row_can_be_clicked(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        row = screen.query_one("#r-macos", ChoiceRow)
        await pilot.click("#r-macos", offset=(row.size.width - 3, 0))   # right half: next
        assert screen.state.macos == "sonoma"
        await pilot.click("#r-macos", offset=(15, 0))                    # left half: previous
        assert screen.state.macos == "sequoia"
        await pilot.click("#r-macos", offset=(2, 0))                     # on the label: just focus
        assert screen.state.macos == "sequoia"
        more = screen.query_one("#r-more", ExpandRow)
        await pilot.click("#r-more")
        assert more.open


@F.tui
async def test_a_single_storage_or_option_row_does_not_cycle(monkeypatch):
    F.patch_host(monkeypatch, facts=F.host_facts(storage_targets=["local-lvm"]))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        screen.query_one("#r-storage").focus()
        await pilot.press("right")
        assert screen.state.storage == "local-lvm"
        assert "‹" not in row_text(screen, "#r-storage")


@F.tui
async def test_the_usual_vm_disk_storage_comes_first_whatever_pvesm_lists_first(monkeypatch):
    F.patch_host(monkeypatch, facts=F.host_facts(storage_targets=["Tower", "local", "local-lvm"],
                                                  storage_free_gb={"Tower": 9000, "local": 80, "local-lvm": 412}))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        row = screen.query_one("#r-storage", ChoiceRow)
        assert [o.value for o in row.options] == ["local-lvm", "Tower", "local"] and screen.state.storage == "local-lvm"
        assert screen.query_one("#bar-disk", CapacityBar).summary() == "128 of 412 GB free"
