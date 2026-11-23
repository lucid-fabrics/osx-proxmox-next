"""The Stela choice in New Mac: the rows it hides and shows, its own checks and its build screen."""
from __future__ import annotations

import pytest
import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.models.wizard_state import NewMacState, validate_stela_new_mac
from osx_proxmox_next.presets import build_presets
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.screens.new_mac import NewMacScreen
from osx_proxmox_next.screens.stela_build import StelaBuildScreen
from osx_proxmox_next.stela import StelaConfig
from osx_proxmox_next.stela_gpus import HostGpu
from osx_proxmox_next.widgets.rows import ChoiceRow, FieldRow

SIZE = (100, 30)
GPU = HostGpu("0000:0a:00", "RTX 3060 Ti", "vfio-pci")


def presets():
    return build_presets("tahoe", 8, 16384, 32, 65536)


def state(**kw) -> NewMacState:
    base = dict(macos="stela", vmid="478", storage="local-lvm")
    base.update(kw)
    return NewMacState(**base)


def test_state_knows_the_stela_choice_and_it_is_a_beta_like_arm64():
    st = NewMacState()
    assert not st.is_stela and not st.is_beta
    st.set_macos("stela")
    assert st.is_stela and st.is_beta and not st.is_arm64 and st.name == "macos-stela"
    assert NewMacState(macos="arm64").is_beta


def test_a_good_form_makes_a_stela_config_from_the_preset():
    errors, config = validate_stela_new_mac(state(), presets())
    assert errors == {}
    assert (config.vmid, config.name, config.storage, config.bridge, config.gpu_pci) == (
        478, "macos-stela", "local-lvm", "vmbr0", "")
    assert (config.cores, config.memory_mb, config.disk_gb) == (8, 16384, 160)


def test_the_chosen_gpu_goes_into_the_config():
    _, config = validate_stela_new_mac(state(gpu_pci="0000:0a:00"), presets())
    assert config.gpu_pci == "0000:0a:00"


def test_too_small_values_are_errors_in_screen_order():
    st = state(size="custom", custom_cores="2", custom_memory_gb="4", custom_disk_gb="32", name="ab", vmid="50",
               bridge="br0", storage="", gpu_pci="x;y")
    errors, config = validate_stela_new_mac(st, presets())
    assert config is None
    assert list(errors) == ["name", "cores", "memory", "disk", "storage", "gpu", "vmid", "bridge"]
    assert "At least 4 CPU cores" in errors["cores"] and "VMID" in errors["vmid"]


def test_text_where_a_number_belongs_is_named_not_crashed():
    st = state(size="custom", custom_cores="x", custom_memory_gb="y", custom_disk_gb="z", vmid="")
    errors, config = validate_stela_new_mac(st, presets())
    assert config is None and set(errors) == {"cores", "memory", "disk", "vmid"}


def test_a_vm_that_does_not_fit_the_host_memory_is_refused():
    errors, config = validate_stela_new_mac(state(), presets(), host_memory_limit_mb=8192)
    assert config is None and "only 8 GB free" in errors["memory"]


async def open_stela(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.press("n")
    await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen) and app.screen.is_attached)
    await pilot.pause(0.2)
    screen = app.screen
    for _ in range(5):                       # Sonoma, Ventura, Monterey, the arm64 beta, then Stela
        await pilot.press("right")
    await pilot.pause(0.1)
    return screen


@F.tui
async def test_choosing_stela_shows_the_gpu_row_and_hides_the_opencore_rows(monkeypatch):
    F.patch_host(monkeypatch)
    monkeypatch.setattr("osx_proxmox_next.screens.new_mac.list_host_gpus", lambda: [GPU])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_stela(pilot, app)
        assert screen.state.is_stela and "27 arm64 via Stela (beta)" in screen.query_one("#r-macos").render().plain
        for rid in ("#r-install", "#r-apple", "#r-more"):
            assert not screen.query_one(rid).display
        assert screen.query_one("#r-gpu").display
        after = F.text_of(screen.query_one("#after"))
        assert "Download Stela" in after and "no Mac needed" in after
        screen.query_one("#r-gpu", ChoiceRow).focus()
        await pilot.press("right")
        await pilot.pause(0.1)
        assert screen.state.gpu_pci == "0000:0a:00"
        await pilot.press("left")
        await pilot.pause(0.1)
        assert screen.state.gpu_pci == ""
        screen.query_one("#r-macos", ChoiceRow).focus()
        await pilot.press("left")            # back to the arm64 runner: no GPU row
        await pilot.pause(0.1)
        assert not screen.query_one("#r-gpu").display


@F.tui
async def test_a_bad_value_stops_the_create_and_says_why(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_stela(pilot, app)
        screen.query_one("#r-size", ChoiceRow).focus()
        await pilot.press("right", "right")                   # Heavy, then Custom
        await pilot.pause(0.1)
        screen.query_one("#r-cores", FieldRow).input.value = "2"
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert isinstance(app.screen, NewMacScreen)
        assert "At least 4 CPU cores" in F.text_of(screen.query_one("#errors"))


@F.tui
async def test_enter_installs_through_its_own_build_screen(monkeypatch):
    F.patch_host(monkeypatch)
    seen = []

    def fake_install(config, emit):
        seen.append(config)
        emit("[    0s] [1/6] Check snippet storage")
        return True

    monkeypatch.setattr("osx_proxmox_next.screens.stela_build.install_vm", fake_install)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_stela(pilot, app)
        await pilot.press("enter")
        await F.settle(pilot, lambda: isinstance(app.screen, StelaBuildScreen) and app.screen.finished,
                       what="the Stela build to finish")
        build = app.screen
        assert not any(isinstance(s, NewMacScreen) for s in app.screen_stack)
        assert seen[0].vmid == 105 and seen[0].name == "macos-stela" and build.ok
        assert build.crumb() == "macos-stela"
        log = "\n".join(line.text for line in build.query_one("#stela-log").lines)
        assert "[1/6] Check snippet storage" in log and "is at Setup Assistant" in log
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


def build_screen(monkeypatch, install):
    monkeypatch.setattr("osx_proxmox_next.screens.stela_build.install_vm", install)
    return StelaBuildScreen(StelaConfig(vmid=478, name="macos27-stela", storage="local-lvm"))


@F.tui
async def test_a_failed_install_says_so_and_shows_the_way_out(monkeypatch):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = build_screen(monkeypatch, lambda config, emit: False)
        await app.push_screen(screen)
        await F.settle(pilot, lambda: screen.finished)
        log = " ".join(" ".join(line.text for line in screen.query_one("#stela-log").lines).split())   # wrapped lines
        assert not screen.ok and "osx-next-cli stela uninstall --vmid 478 --execute" in log


@F.tui
async def test_a_crash_in_the_worker_is_shown_not_swallowed(monkeypatch):
    def crashing(config, emit):
        raise RuntimeError("no qm here")

    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = build_screen(monkeypatch, crashing)
        await app.push_screen(screen)
        await F.settle(pilot, lambda: screen.finished)
        log = "\n".join(line.text for line in screen.query_one("#stela-log").lines)
        assert not screen.ok and "Unexpected error: no qm here" in log and "Stopped." in log
