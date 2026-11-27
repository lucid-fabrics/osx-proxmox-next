"""The macOS 27 arm64 (beta) choice in New Mac: the rows it hides, its own checks and the runner build screen."""
from __future__ import annotations

import tui_fakes as F
import pytest
from arm64_fakes import pin_release
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.models.wizard_state import NewMacState, validate_arm64_new_mac
from osx_proxmox_next.presets import build_presets
from osx_proxmox_next.screens.arm64_build import Arm64BuildScreen
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.screens.new_mac import NewMacScreen
from osx_proxmox_next.widgets.rows import ChoiceRow, ExpandRow, FieldRow

SIZE = (100, 30)


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    pin_release(monkeypatch)


def presets():
    return build_presets("tahoe", 8, 16384, 32, 65536)


def state(**kw) -> NewMacState:
    base = dict(macos="arm64", vmid="478", storage="sn850x")
    base.update(kw)
    return NewMacState(**base)


def test_state_knows_the_arm64_choice_and_names_it():
    st = NewMacState()
    assert not st.is_arm64
    st.set_macos("arm64")
    assert st.is_arm64 and st.name == "macos-arm64"


def test_a_good_form_makes_a_runner_config_from_the_preset():
    errors, config = validate_arm64_new_mac(state(), presets())
    assert errors == {}
    assert (config.vmid, config.name, config.storage, config.bridge) == (478, "macos-arm64", "sn850x", "vmbr0")
    assert (config.cores, config.memory_mb, config.disk_gb, config.vlan) == (8, 16384, 160, 0)


def test_the_custom_size_and_a_vlan_are_used():
    st = state(size="custom", custom_cores="6", custom_memory_gb="8", custom_disk_gb="120", vlan="30")
    _, config = validate_arm64_new_mac(st, presets())
    assert (config.cores, config.memory_mb, config.disk_gb, config.vlan) == (6, 8192, 120, 30)


def test_too_small_values_are_errors_in_screen_order():
    st = state(size="custom", custom_cores="2", custom_memory_gb="4", custom_disk_gb="64", name="ab", vmid="50",
               bridge="br0", storage="")
    errors, config = validate_arm64_new_mac(st, presets())
    assert config is None
    assert list(errors) == ["name", "cores", "memory", "disk", "storage", "vmid", "bridge"]
    assert "At least 4 CPU cores" in errors["cores"] and "VMID" in errors["vmid"]


def test_text_where_a_number_belongs_is_named_not_crashed():
    st = state(size="custom", custom_cores="x", custom_memory_gb="y", custom_disk_gb="z", vmid="", vlan="q")
    errors, config = validate_arm64_new_mac(st, presets())
    assert config is None and set(errors) == {"cores", "memory", "disk", "vmid", "vlan"}


def test_a_vm_that_does_not_fit_the_host_memory_is_refused():
    errors, config = validate_arm64_new_mac(state(), presets(), host_memory_limit_mb=8192)
    assert config is None and "only 8 GB free" in errors["memory"]


async def open_arm64(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.press("n")
    await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen) and app.screen.is_attached)
    await pilot.pause(0.2)
    screen = app.screen
    for _ in range(4):                       # Sonoma, Ventura, Monterey, then the arm64 beta
        await pilot.press("right")
    await pilot.pause(0.1)
    return screen


@F.tui
async def test_choosing_arm64_hides_the_rows_that_only_an_opencore_mac_has(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_arm64(pilot, app)
        assert screen.state.is_arm64 and "27 arm64 (beta)" in screen.query_one("#r-macos").render().plain
        assert F.text_of(screen.query_one("#r-name .field-suffix")).startswith("· VM")
        for rid in ("#r-install", "#r-apple", "#r-more"):
            assert not screen.query_one(rid).display
        after = F.text_of(screen.query_one("#after"))
        assert "Create the runner VM" in after and "not in this beta build yet" in after
        assert "Check, download, rehearse" not in after
        screen.query_one("#r-macos", ChoiceRow).focus()
        await pilot.press("left")            # back to Monterey: the rows come back
        await pilot.pause(0.1)
        assert screen.query_one("#r-install").display and "Create VM" in F.text_of(screen.query_one("#after"))
        assert screen.query_one("#r-more", ExpandRow).display


@F.tui
async def test_a_bad_value_stops_the_create_and_says_why(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_arm64(pilot, app)
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
async def test_enter_creates_the_runner_through_its_own_build_screen(monkeypatch):
    F.patch_host(monkeypatch)
    seen = []

    def fake_apply(steps, execute=False, adapter=None, on_step=None):
        seen.append((steps, execute))
        for idx, step in enumerate(steps, start=1):
            on_step(idx, len(steps), step, None)
        return ApplyResult(True, [], log_path=None)

    monkeypatch.setattr("osx_proxmox_next.screens.arm64_build.apply_plan", fake_apply)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_arm64(pilot, app)
        await pilot.press("enter")
        await F.settle(pilot, lambda: isinstance(app.screen, Arm64BuildScreen) and app.screen.finished,
                       what="the runner build to finish")
        build = app.screen
        assert not any(isinstance(s, NewMacScreen) for s in app.screen_stack)
        steps, execute = seen[0]
        assert execute is True and steps[3].argv[:5] == ["qm", "create", "105", "--name", "macos-arm64"]
        assert build.ok and build.crumb() == "macos-arm64"
        await F.settle_text(pilot, lambda: build_log(build), "not in this beta build yet")   # `finished` is set a line early
        log = build_log(build)
        assert "[4/10] Create the runner VM" in log and "starts" not in log
        assert "is starting" in log and "not in this beta build yet" in log
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


def build_screen_after(monkeypatch, apply):
    from osx_proxmox_next.arm64 import Arm64Config
    monkeypatch.setattr("osx_proxmox_next.screens.arm64_build.apply_plan", apply)
    return Arm64BuildScreen(Arm64Config(vmid=478, name="macos27-runner", storage="sn850x", source_ref="v0.9.0", source_sha256="a" * 64))


def build_log(screen) -> str:
    return "\n".join(line.text for line in screen.query_one("#arm64-log").lines)


@F.tui
async def test_a_failed_step_is_shown_with_its_output_and_the_way_out(monkeypatch):
    def failing(steps, execute=False, adapter=None, on_step=None):
        on_step(5, 9, steps[4], None)
        on_step(5, 9, steps[4], StepResult(steps[4].title, steps[4].command, False, 1, "storage is full"))
        return ApplyResult(False, [], log_path=None)

    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = build_screen_after(monkeypatch, failing)
        await app.push_screen(screen)
        await F.settle_text(pilot, lambda: build_log(screen), "uninstall --vmid 478 --execute")   # `finished` is set a line early
        log = build_log(screen)
        assert not screen.ok and "FAILED: Import the disk\nstorage is full" in log
        assert "osx-next-cli arm64 uninstall --vmid 478 --execute" in log


@F.tui
async def test_a_crash_in_the_worker_is_shown_not_swallowed(monkeypatch):
    def crashing(steps, execute=False, adapter=None, on_step=None):
        raise RuntimeError("no qm here")

    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = build_screen_after(monkeypatch, crashing)
        await app.push_screen(screen)
        await F.settle_text(pilot, lambda: build_log(screen), "Stopped.")                   # `finished` is set a line early
        log = build_log(screen)
        assert not screen.ok and "Unexpected error: no qm here" in log and "Stopped." in log



def test_blank_and_non_ascii_digits_are_errors_not_crashes():
    errors, config = validate_arm64_new_mac(state(vlan="  ", vmid="\u00b2"), presets())
    assert config is None and "vmid" in errors
    errors, config = validate_arm64_new_mac(state(vlan="  "), presets())
    assert errors == {} and config.vlan == 0


def test_a_build_without_a_pinned_release_marks_the_arm64_choice(monkeypatch):
    monkeypatch.setattr("osx_proxmox_next.arm64.DEFAULT_SOURCE_REF", "")
    monkeypatch.setattr("osx_proxmox_next.arm64.DEFAULT_SOURCE_SHA256", "")
    errors, config = validate_arm64_new_mac(state(), presets())
    assert config is None and list(errors) == ["macos"] and "no pinned release" in errors["macos"]
