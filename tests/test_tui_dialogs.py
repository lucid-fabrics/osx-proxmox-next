"""The Remove and Edit dialogs, and the edit rules."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from textual.widgets import Button, Checkbox, Input, Static

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.domain import EditChanges
from osx_proxmox_next.rollback import RollbackSnapshot
from osx_proxmox_next.screens.dialogs import EditDialog, RemoveDialog, changes_from_form
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.services import VmFacts, VmInfo

SIZE = (100, 30)
FACTS = VmFacts(name="macos-sequoia", cores=8, memory_mb=16384, bridge="vmbr0", nic_model="vmxnet3")


# ── the edit rules ───────────────────────────────────────────────────

def form(**over):
    base = dict(name="macos-sequoia", cores="8", memory_gb="16", bridge="vmbr0", nic_model="vmxnet3",
                add_disk_gb="", disk_slot="virtio0")
    base.update(over)
    return changes_from_form(FACTS, **base)


def test_nothing_changed_means_nothing_to_apply():
    assert form() == (None, [])


def test_only_changed_fields_are_applied():
    changes, problems = form(name="renamed", cores="4", memory_gb="8")
    assert problems == [] and changes == EditChanges(name="renamed", cores=4, memory_mb=8192)


def test_a_nic_model_change_alone_is_a_change():
    changes, _ = form(nic_model="e1000-82545em")
    assert changes.bridge == "vmbr0" and changes.nic_model == "e1000-82545em"


def test_a_bridge_change_keeps_the_nic_model():
    changes, _ = form(bridge="vmbr1")
    assert changes.bridge == "vmbr1" and changes.nic_model == "vmxnet3"


def test_a_disk_name_alone_is_not_a_change_but_with_added_space_it_is_used():
    assert form(disk_slot="sata0") == (None, [])
    changes, _ = form(add_disk_gb="32", disk_slot="sata0")
    assert changes.disk_gb_add == 32 and changes.disk_name == "sata0"
    changes, _ = form(add_disk_gb="32", disk_slot="")
    assert changes.disk_name == "virtio0"


def test_numbers_that_are_not_numbers_are_problems():
    changes, problems = form(cores="many", memory_gb="lots", add_disk_gb="big")
    assert changes is None
    assert problems == ["Cores must be a whole number.", "Memory (GB) must be a whole number.",
                        "Added disk space must be a whole number."]


# ── the dialogs on Home ──────────────────────────────────────────────

class Calls:
    def __init__(self):
        self.destroy: list[tuple] = []
        self.edit: list[tuple] = []


def patch_services(monkeypatch, *, exists=True, destroy_ok=True, edit_ok=True, raises=False):
    calls = Calls()
    info = VmInfo(101, "macos-sequoia", "running", F.three_vms()[0].config_raw) if exists else None
    monkeypatch.setattr("osx_proxmox_next.screens.dialogs.fetch_vm_info", lambda vmid, adapter=None: info)
    monkeypatch.setattr("osx_proxmox_next.screens.dialogs.get_proxmox_adapter", lambda: None)

    def destroy(vmid, purge=False, on_step=None):
        if raises:
            raise RuntimeError("boom")
        calls.destroy.append((vmid, purge))
        step = SimpleNamespace(title="Destroy VM")
        on_step(1, 1, step, None)
        on_step(1, 1, step, SimpleNamespace(ok=destroy_ok))
        return SimpleNamespace(ok=destroy_ok, log_path=Path("/tmp/d.log")), RollbackSnapshot(vmid, Path("/tmp/snap"))

    def edit(vmid, changes, start_after=False, on_step=None, current_net0=None):
        if raises:
            raise RuntimeError("boom")
        calls.edit.append((vmid, changes, start_after))
        step = SimpleNamespace(title="Set cores")
        on_step(1, 1, step, None)
        on_step(1, 1, step, SimpleNamespace(ok=edit_ok))
        return SimpleNamespace(ok=edit_ok, log_path=Path("/tmp/e.log"))

    monkeypatch.setattr("osx_proxmox_next.screens.dialogs.run_destroy_worker", destroy)
    monkeypatch.setattr("osx_proxmox_next.screens.dialogs.run_edit_worker", edit)
    monkeypatch.setattr("osx_proxmox_next.screens.dialogs.create_snapshot", lambda vmid: None)
    return calls


async def open_home(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.pause(0.1)


def log_text(app) -> str:
    return F.text_of(app.screen.query_one("#remove-log", Static))


@F.tui
async def test_remove_dialog_defaults_cancel_focus_purge_on_and_a_disabled_button(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("l", "l", "d")
        dialog = app.screen
        assert isinstance(dialog, RemoveDialog)
        assert app.focused.id == "cancel" and dialog.query_one("#remove", Button).disabled
        assert dialog.query_one("#purge", Checkbox).value is True
        title = F.text_of(dialog.query_one(".dialog-title"))
        assert title == "Remove VM 104 · build-sonoma?"
        assert "96 GB on local-lvm" in str(dialog.query_one("#purge", Checkbox).label)
        assert "It cannot be undone." in [F.text_of(w) for w in dialog.query(Static)]
        dialog.query_one("#confirm-id", Input).value = "10"
        await pilot.pause()
        assert dialog.query_one("#remove", Button).disabled
        dialog.query_one("#confirm-id", Input).value = "104"
        await pilot.pause()
        assert not dialog.query_one("#remove", Button).disabled
        dialog.query_one("#confirm-id", Input).value = "1040"
        await pilot.pause()
        assert dialog.query_one("#remove", Button).disabled
        await pilot.press("escape")
        assert isinstance(app.screen, HomeScreen)


@F.tui
async def test_enter_on_cancel_closes_the_dialog_without_removing(monkeypatch):
    F.patch_host(monkeypatch)
    calls = patch_services(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("d", "enter")
        assert isinstance(app.screen, HomeScreen) and calls.destroy == []


@F.tui
async def test_removing_with_the_typed_id_runs_the_destroy_plan(monkeypatch):
    F.patch_host(monkeypatch)
    calls = patch_services(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("d")
        dialog = app.screen
        dialog.query_one("#purge", Checkbox).value = False
        dialog.query_one("#confirm-id", Input).value = "101"
        await pilot.pause()
        dialog.query_one("#confirm-id", Input).focus()
        await pilot.press("enter")                         # enter in the field confirms
        await F.settle(pilot, lambda: "was removed" in log_text(app))
        assert calls.destroy == [(101, False)]
        assert "Log: /tmp/d.log" in log_text(app) and dialog.query_one("#cancel", Button).label.plain == "Close"
        assert dialog.query_one("#remove", Button).disabled
        await pilot.press("enter")                          # the close button has the focus
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_removing_with_the_button_and_the_disk_images_kept(monkeypatch):
    F.patch_host(monkeypatch)
    calls = patch_services(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("d")
        app.screen.query_one("#confirm-id", Input).value = "101"
        await pilot.pause()
        await pilot.click("#remove")
        await F.settle(pilot, lambda: bool(calls.destroy))
        assert calls.destroy == [(101, True)]
        await F.settle(pilot, lambda: "was removed" in log_text(app))
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_a_failed_removal_shows_the_rollback_hints(monkeypatch):
    F.patch_host(monkeypatch)
    patch_services(monkeypatch, destroy_ok=False)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("d")
        app.screen.query_one("#confirm-id", Input).value = "101"
        await pilot.pause()
        app.screen.action_remove()
        app.screen.action_remove()  # a second press while it runs does nothing
        await F.settle(pilot, lambda: "failed" in log_text(app))
        assert "Review snapshot: /tmp/snap" in log_text(app)
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_removing_a_vm_that_is_gone_or_that_crashes_the_worker(monkeypatch):
    F.patch_host(monkeypatch)
    patch_services(monkeypatch, exists=False)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("d")
        app.screen.query_one("#confirm-id", Input).value = "101"
        await pilot.pause()
        app.screen.action_remove()
        await F.settle(pilot, lambda: "was not found" in log_text(app))
    patch_services(monkeypatch, raises=True)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("d")
        app.screen.query_one("#confirm-id", Input).value = "101"
        await pilot.pause()
        app.screen.action_remove()
        await F.settle(pilot, lambda: "failed" in log_text(app))
        assert "Log:" in log_text(app)


@F.tui
async def test_the_remove_guard_ignores_a_wrong_or_missing_id(monkeypatch):
    F.patch_host(monkeypatch)
    calls = patch_services(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("d")
        dialog = app.screen
        dialog.action_remove()
        dialog.on_input_submitted(None)
        await pilot.pause(0.2)
        assert calls.destroy == [] and not dialog.running


def edit_dialog(app) -> EditDialog:
    assert isinstance(app.screen, EditDialog)
    return app.screen


def field(app, fid: str):
    return app.screen.query_one(f"#{fid}").query_one(Input)


@F.tui
async def test_edit_is_prefilled_and_apply_waits_for_a_change(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        dialog = edit_dialog(app)
        assert field(app, "e-name").value == "macos-sequoia" and field(app, "e-cores").value == "8"
        assert field(app, "e-memory").value == "16" and field(app, "e-bridge").value == "vmbr0"
        assert dialog.query_one("#e-nic").value == "vmxnet3" and field(app, "e-slot").value == "virtio0"
        assert dialog.query_one("#e-start", Checkbox).value is True       # it is running
        assert "stops it first" in F.text_of(dialog.query_one("#e-note", Static))
        assert dialog.query_one("#apply", Button).disabled
        field(app, "e-cores").value = "4"
        await pilot.pause()
        assert not dialog.query_one("#apply", Button).disabled
        field(app, "e-cores").value = "8"
        await pilot.pause()
        assert dialog.query_one("#apply", Button).disabled


@F.tui
async def test_changing_only_the_nic_model_enables_apply(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        dialog = edit_dialog(app)
        dialog.query_one("#e-nic").focus()
        await pilot.press("right")
        await pilot.pause()
        assert dialog.query_one("#e-nic").value == "e1000-82545em"
        assert not dialog.query_one("#apply", Button).disabled
        await pilot.press("left")
        await pilot.pause()
        assert dialog.query_one("#apply", Button).disabled


@F.tui
async def test_changing_only_the_added_disk_with_its_name_enables_apply(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        field(app, "e-slot").value = "sata0"
        await pilot.pause()
        assert app.screen.query_one("#apply", Button).disabled
        field(app, "e-disk").value = "32"
        await pilot.pause()
        assert not app.screen.query_one("#apply", Button).disabled


@F.tui
async def test_applying_an_edit_runs_the_edit_plan_with_only_the_changes(monkeypatch):
    F.patch_host(monkeypatch)
    calls = patch_services(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        dialog = edit_dialog(app)
        field(app, "e-memory").value = "24"
        field(app, "e-name").value = "bigger"
        await pilot.pause()
        await pilot.click("#apply")
        await F.settle(pilot, lambda: bool(calls.edit))
        vmid, changes, start_after = calls.edit[0]
        assert vmid == 101 and changes.memory_mb == 24576 and changes.name == "bigger" and changes.cores is None
        assert start_after is True
        await F.settle(pilot, lambda: dialog.finished)
        assert "Changes applied." in F.text_of(dialog.query_one("#e-note", Static))
        assert dialog.query_one("#cancel", Button).label.plain == "Close"
        assert dialog.query_one("#apply", Button).disabled                 # the same changes are never applied twice
        dialog.action_apply()                              # nothing more to apply
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_enter_in_a_field_applies_and_a_failed_edit_is_reported(monkeypatch):
    F.patch_host(monkeypatch)
    patch_services(monkeypatch, edit_ok=False)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        dialog = edit_dialog(app)
        field(app, "e-cores").value = "4"
        await pilot.pause()
        field(app, "e-cores").focus()
        await pilot.press("enter")
        await F.settle(pilot, lambda: "failed" in F.text_of(dialog.query_one("#e-note", Static)))
        assert not dialog.finished


@F.tui
async def test_edit_problems_are_shown_and_nothing_runs(monkeypatch):
    F.patch_host(monkeypatch)
    calls = patch_services(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        dialog = edit_dialog(app)
        field(app, "e-bridge").value = "br0"
        await pilot.pause()
        dialog.action_apply()
        assert "Bridge must match vmbr" in F.text_of(dialog.query_one("#e-note", Static))
        field(app, "e-bridge").value = "vmbr0"
        field(app, "e-cores").value = "many"
        await pilot.pause()
        assert dialog.query_one("#apply", Button).disabled
        dialog.action_apply()
        assert "Cores must be a whole number" in F.text_of(dialog.query_one("#e-note", Static))
        assert calls.edit == []


@F.tui
async def test_an_edit_of_a_vm_that_is_gone_or_crashes(monkeypatch):
    F.patch_host(monkeypatch)
    patch_services(monkeypatch, exists=False)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        field(app, "e-cores").value = "4"
        await pilot.pause()
        app.screen.action_apply()
        await F.settle(pilot, lambda: "was not found" in F.text_of(app.screen.query_one("#e-note", Static)))
    patch_services(monkeypatch, raises=True)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        field(app, "e-cores").value = "4"
        await pilot.pause()
        app.screen.action_apply()
        await F.settle(pilot, lambda: "failed" in F.text_of(app.screen.query_one("#e-note", Static)))


@F.tui
async def test_an_edit_dialog_offers_a_nic_model_the_app_does_not_know(monkeypatch):
    vms = [F.record(101, "odd", "stopped")]
    vms[0].config_raw = vms[0].config_raw.replace("vmxnet3", "virtio")
    F.patch_host(monkeypatch, vms=vms)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        row = app.screen.query_one("#e-nic")
        assert [o.value for o in row.options] == ["virtio", "vmxnet3", "e1000-82545em"] and row.value == "virtio"
        assert app.screen.query_one("#e-start", Checkbox).value is False
        await pilot.press("escape")
        assert isinstance(app.screen, HomeScreen)


@F.tui
async def test_edit_from_the_vm_view_and_remove_leaves_the_view(monkeypatch):
    F.patch_host(monkeypatch)
    patch_services(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("enter")
        from osx_proxmox_next.screens.vm_view import VmViewScreen
        await F.settle(pilot, lambda: isinstance(app.screen, VmViewScreen))
        await pilot.press("e")
        assert isinstance(app.screen, EditDialog)
        await pilot.press("escape")
        await pilot.press("d")
        app.screen.query_one("#confirm-id", Input).value = "101"
        await pilot.pause()
        app.screen.action_remove()
        await F.settle(pilot, lambda: app.screen.finished)
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_the_edit_dialog_defaults_to_the_main_disk_and_the_arrows_move_between_fields(monkeypatch):
    rec = F.record(101, "odd", "stopped")
    rec.config_raw = rec.config_raw.replace("ide0: local:iso/opencore.iso,media=disk,size=40M\n", "") + (
        "ide0: local-lvm:vm-101-disk-2,media=disk,size=1G\n")
    rec.config_raw = "ide0: local-lvm:vm-101-disk-0,size=64M\n" + rec.config_raw
    F.patch_host(monkeypatch, vms=[rec])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_home(pilot, app)
        await pilot.press("e")
        assert field(app, "e-slot").value == "virtio0"
        assert app.focused is field(app, "e-name")
        await pilot.press("down")
        assert app.focused is field(app, "e-cores")
        await pilot.press("up")
        assert app.focused is field(app, "e-name")
