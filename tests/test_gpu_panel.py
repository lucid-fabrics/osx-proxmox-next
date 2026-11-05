"""The guided GPU passthrough panel in the TUI."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Input, Select

from gpu_fakes import add_card, add_other, write
from nullmoth_fakes import MAC_CFG
from osx_proxmox_next import _gpu_mixin, gpu_setup
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.executor import ApplyResult, StepResult
from test_app_wizard import _advance_to_step
from test_gpu_setup import ANSWERS, SEQUOIA, Env, ready  # noqa: F401

HINT = "etc/profile.d/osx-proxmox-next-gpu.sh"


@pytest.fixture(autouse=True)
def _vm_list(monkeypatch):
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["100 macos-sequoia stopped"])


async def settle(pilot, pred, tries: int = 100):
    for _ in range(tries):
        await pilot.pause(0.03)
        if pred():
            return
    app = pilot.app
    raise AssertionError("the panel never reached the expected state:\n" + "\n".join(
        text(app, wid)[:600] for wid in ("#gp_title", "#gp_body", "#gp_result")))


async def press(pilot, app, wid: str) -> None:
    """Press a button without needing it to be inside the visible part of the screen."""
    app.query_one(wid, Button).press()
    await pilot.pause(0.05)


def text(app, wid: str) -> str:
    return str(app.query_one(wid).content)


def label(app, wid: str) -> str:
    return str(app.query_one(wid, Button).label)


def shown(app, wid: str) -> bool:
    return not app.query_one(wid).has_class("hidden")


async def open_panel(pilot, app, env: Env):
    app._gp = env.session()
    await _advance_to_step(pilot, app, 2)
    await pilot.click("#mode_gpu")
    await settle(pilot, lambda: "GPU passthrough" in text(app, "#gp_rail"))


def run(coro_fn):
    asyncio.run(coro_fn())


def amd_env(tmp_path: Path, **kw) -> Env:
    env = Env(tmp_path, **kw)
    add_card(env.root)
    return env


def test_menu_has_the_guided_entry():
    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            assert label(app, "#mode_gpu") == "GPU Passthrough (guided)"
            assert not shown(app, "#gpu_panel")
            await pilot.click("#mode_nvidia")
            assert not shown(app, "#gpu_panel")
    run(go)


def test_amd_walkthrough_from_scan_to_finish(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    applied = []
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan",
                        lambda steps, execute=False, **k: applied.append(steps) or ApplyResult(True, [StepResult("s", "c", True, 0, "")], Path("/tmp/log")))
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "Works natively" in text(app, "#gp_body"))
            assert shown(app, "#gp_gpu_select") and shown(app, "#gp_display_select") and not shown(app, "#gp_ack_cb")
            assert app.query_one("#gp_primary_btn", Button).disabled
            app.query_one("#gp_gpu_select", Select).value = "0000:06:00"
            await settle(pilot, lambda: not app.query_one("#gp_primary_btn", Button).disabled)
            app.query_one("#gp_vmid", Input).value = "100"
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 4" in text(app, "#gp_title"))
            assert "This is exactly what will change" in text(app, "#gp_body") and label(app, "#gp_primary_btn") == "Apply these changes"
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 5" in text(app, "#gp_title"))
            assert "Done. Backups are in" in text(app, "#gp_result")
            assert "must reboot" in text(app, "#gp_body") and (env.root / HINT).exists()
            await press(pilot, app, "#gp_secondary_btn")
            assert "run: osx-next-cli gpu-setup" in text(app, "#gp_result")
            # the host rebooted
            write(env.root, "/proc/sys/kernel/random/boot_id", "later\n")
            ready(env)
            app._gp = env.session()
            app._gp_open()
            await settle(pilot, lambda: "Step 7" in text(app, "#gp_title"))
            assert shown(app, "#gp_vmid") and app.query_one("#gp_vmid", Input).value == "100"
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: label(app, "#gp_primary_btn") == "Attach the card now")
            assert "Attach GPU" in text(app, "#gp_body")
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 8" in text(app, "#gp_title"))
            assert label(app, "#gp_secondary_btn") == "No, help me" and applied
            await press(pilot, app, "#gp_secondary_btn")
            assert "No signal on the monitor" in text(app, "#gp_body")
            await press(pilot, app, "#gp_primary_btn")
            assert "Setup finished" in text(app, "#gp_title") and "Setup summary" in text(app, "#gp_body")
            assert not (env.root / HINT).exists() and shown(app, "#gp_undo_btn")
            await press(pilot, app, "#gp_primary_btn")  # start a new setup
            await settle(pilot, lambda: "Step 1-2" in text(app, "#gp_title"))
    run(go)


def test_bios_card_and_qr_are_shown(tmp_path):
    env = Env(tmp_path, virt=False, tables=(), efi=False)
    add_card(env.root)
    env.qm.vms[101] = ("running", SEQUOIA)
    fw = env.root / _gpu_bios_path()
    fw.parent.mkdir(parents=True, exist_ok=True)
    fw.write_bytes(b"\x06\x00\x00\x00\x01" + b"\x00" * 7)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "Works natively" in text(app, "#gp_body"))
            app.query_one("#gp_gpu_select", Select).value = "0000:06:00"
            await pilot.pause()
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 3" in text(app, "#gp_title"))
            body = text(app, "#gp_body")
            assert "BIOS STEPS FOR THIS SERVER" in body and "Running VMs that a reboot will stop: 101" in body
            assert shown(app, "#gp_qr") and label(app, "#gp_secondary_btn") == "Reboot into BIOS now"
            assert "[ok]" in body and "[!!]" in body  # ticks are literal text, not Rich markup
            await press(pilot, app, "#gp_secondary_btn")
            assert "Rebooting into the BIOS" in text(app, "#gp_result") and env.run.ran("systemctl", "reboot", "--firmware-setup")
            await press(pilot, app, "#gp_back_btn")
            await settle(pilot, lambda: "Step 1-2" in text(app, "#gp_title"))
    run(go)


def _gpu_bios_path() -> str:
    from osx_proxmox_next import bios_guide
    return bios_guide.OS_INDICATIONS.lstrip("/")


def test_bios_without_firmware_reboot_offers_later(tmp_path):
    env = Env(tmp_path, virt=False)
    add_card(env.root)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.choose("06:00", 100, "gpu")
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 3" in text(app, "#gp_title"))
            assert label(app, "#gp_secondary_btn") == "I'll do it later"
            await press(pilot, app, "#gp_secondary_btn")
            assert "osx-next-cli gpu-setup" in text(app, "#gp_result")
            await press(pilot, app, "#gp_primary_btn")  # re-check: still failing, card again
            await settle(pilot, lambda: "BIOS STEPS" in text(app, "#gp_body"))
    run(go)


def test_nvidia_needs_the_acknowledgement(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "NullMoth" in text(app, "#gp_body"))
            app.query_one("#gp_gpu_select", Select).value = "0000:01:00"
            await pilot.pause()
            assert shown(app, "#gp_notice") and shown(app, "#gp_ack_cb") and not shown(app, "#gp_display_select")
            assert app.query_one("#gp_primary_btn", Button).disabled
            app.query_one("#gp_vmid", Input).value = "100"
            app.query_one("#gp_ack_cb", Checkbox).value = True
            await settle(pilot, lambda: not app.query_one("#gp_primary_btn", Button).disabled)
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 4" in text(app, "#gp_title"))
            assert app._gp.state["ack_nullmoth"] is True and app._gp.state["display"] == "console"
    run(go)


def test_choose_is_refused_with_a_reason(tmp_path):
    env = amd_env(tmp_path)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "Works natively" in text(app, "#gp_body"))
            app.query_one("#gp_gpu_select", Select).value = "0000:06:00"
            app.query_one("#gp_vmid", Input).value = "999"
            await pilot.pause()
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "VM 999 not found" in text(app, "#gp_result"))
            assert "Refused:" in text(app, "#gp_result")
    run(go)


def test_no_usable_card(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, device=0x73DF)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "No card here can run macOS graphics" in text(app, "#gp_body"))
            assert "Navi 22" in text(app, "#gp_body") and app.query_one("#gp_primary_btn", Button).disabled
    run(go)


def test_host_step_failures_and_confirmation(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, boot_vga=True)
    env.run.answers["update-grub"] = (1, "exploded")

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.choose("06:00", 100, "gpu")
            s.complete(3, skipped=True)
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 4" in text(app, "#gp_title"))
            assert "only display adapter" in text(app, "#gp_body") and shown(app, "#gp_confirm_cb")
            assert not app.query_one("#gp_primary_btn", Button).disabled
            app.query_one("#gp_confirm_cb", Checkbox).value = True
            await press(pilot, app, "#gp_primary_btn")  # re-plans with the confirmation
            await settle(pilot, lambda: "Rescue" in text(app, "#gp_body"))
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "exploded" in text(app, "#gp_result"))
            assert "The files were put back." in text(app, "#gp_result")
            await press(pilot, app, "#gp_back_btn")
            await settle(pilot, lambda: "Step 1-2" in text(app, "#gp_title"))  # BIOS was skipped: Back lands on the choice
    run(go)


def test_host_step_skips_when_nothing_is_needed(tmp_path):
    env = amd_env(tmp_path)
    ready(env)
    write(env.root, "/etc/modules", "vfio\nvfio_iommu_type1\nvfio_pci\n")
    s = env.session()
    s.choose("06:00", 100, "gpu")
    for ch in s.host_plan()["changes"]:
        write(env.root, ch["path"], ch["new"])
    env.run.answers["update-initramfs"] = (0, "")

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            plan = s.host_plan()
            plan["commands"] = []
            s.host_plan = lambda allow_no_console=False: plan
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 7" in text(app, "#gp_title"))
            assert {3, 4, 5} <= set(s.state["skipped"]) | set(s.state["done"])
    run(go)


def test_verify_screens(tmp_path):
    env = amd_env(tmp_path)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            # configured, not rebooted: the reboot screen
            write(env.root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"\n')
            s = env.session()
            s.choose("06:00", 100, "gpu")
            s.goto(6)
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 5" in text(app, "#gp_title"))
            assert "not active yet" in text(app, "#gp_result")
            # booted with them, the card is not bound: the host step again
            ready(env)
            (env.root / "sys/bus/pci/devices/0000:06:00.0/driver").unlink()
            s.goto(6)
            app._gp_open()
            await settle(pilot, lambda: "Step 6" in text(app, "#gp_title"))
            assert "Back to the host step" in label(app, "#gp_primary_btn") and "Not bound" in text(app, "#gp_body")
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 4" in text(app, "#gp_title"))
            # a storage device in the group: nothing to click through
            s.goto(6)
            (env.root / "sys/bus/pci/devices/0000:06:00.0/driver").symlink_to(env.root / "sys/bus/pci/drivers/vfio-pci")
            add_other(env.root, "0000:07:00.0", 0x144D, 0xA808, 0x010802, 7)
            app._gp_open()
            await settle(pilot, lambda: "Step 6" in text(app, "#gp_title"))
            assert label(app, "#gp_primary_btn") == "Check again" and "Move the card to another slot" in text(app, "#gp_body")
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 6" in text(app, "#gp_title"))
    run(go)


def test_reboot_now_button(tmp_path):
    env = amd_env(tmp_path)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            write(env.root, "/proc/sys/kernel/random/boot_id", "one\n")
            s = env.session()
            s.choose("06:00", 100, "gpu")
            s.host_apply(s.host_plan())
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 5" in text(app, "#gp_title"))
            await press(pilot, app, "#gp_primary_btn")
            assert "Rebooting..." in text(app, "#gp_result") and env.run.ran("systemctl", "reboot")
            env.run.answers["systemctl reboot"] = (1, "nope")
            await press(pilot, app, "#gp_primary_btn")
            assert "Could not start the reboot: nope" in text(app, "#gp_result")
    run(go)


def test_vm_step_messages_and_undo(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    env.qm.vms[100] = ("running", SEQUOIA)
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.choose("06:00", 100, "gpu")
            s.goto(7)
            s.state["vm"] = None
            s.state["host_changes"] = [{"path": "/x", "existed": False, "backup": "", "reverted": False}]
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 7" in text(app, "#gp_title"))
            await press(pilot, app, "#gp_primary_btn")  # no VM entered
            assert "Enter a VM ID first." in text(app, "#gp_result")
            app.query_one("#gp_vmid", Input).value = "100"
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "REFUSED" in text(app, "#gp_body").upper() or "Refused" in text(app, "#gp_body"))
            assert "running" in text(app, "#gp_body") and label(app, "#gp_primary_btn") == "Preview"
            env.qm.vms[100] = ("stopped", SEQUOIA)
            await press(pilot, app, "#gp_primary_btn")  # a refused plan previews again instead of applying
            await settle(pilot, lambda: label(app, "#gp_primary_btn") == "Attach the card now")
            monkeypatch.setattr("osx_proxmox_next.executor.apply_plan",
                                lambda *a, **k: ApplyResult(False, [], Path("/tmp/log")))
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Attaching failed" in text(app, "#gp_result"))
            await press(pilot, app, "#gp_undo_btn")
            await settle(pilot, lambda: "Step" in text(app, "#gp_title"))
    run(go)


def test_nvidia_vm_step_downloads_amfipass(tmp_path, monkeypatch):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    fetched = []
    monkeypatch.setattr("osx_proxmox_next.oc_builder.ensure_amfipass", lambda d: fetched.append(d))
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan",
                        lambda *a, **k: ApplyResult(True, [StepResult("s", "c", True, 0, "")], Path("/tmp/log")))

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.choose("01:00", 100, "console", ack=True)
            s.goto(7)
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 7" in text(app, "#gp_title"))
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: label(app, "#gp_primary_btn") == "Attach the card now")
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 8" in text(app, "#gp_title"))
            assert fetched and "kmutil showloaded" in text(app, "#gp_body") and shown(app, "#gp_qr")
            assert label(app, "#gp_primary_btn") == "Done"
            await press(pilot, app, "#gp_primary_btn")
            assert "Setup finished" in text(app, "#gp_title")
    run(go)


def test_a_failing_worker_shows_the_error(tmp_path):
    env = amd_env(tmp_path)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.scan = lambda exclude_vm=-1: (_ for _ in ()).throw(RuntimeError("lspci exploded"))
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "lspci exploded" in text(app, "#gp_result"))
    run(go)


def test_session_is_created_on_first_use(monkeypatch):
    made = []
    monkeypatch.setattr(_gpu_mixin, "Session", lambda: made.append(1) or object())
    mixin = _gpu_mixin.GpuSetupMixin()
    assert mixin._gp_session() is mixin._gp_session() and made == [1]
