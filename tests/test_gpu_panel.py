"""The guided GPU passthrough panel in the TUI."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.css.query import NoMatches
from textual.widgets import Button, Checkbox, Input, OptionList, RadioSet, Select

from gpu_fakes import add_card, add_other, write
from nullmoth_fakes import MAC_CFG
from osx_proxmox_next import _gpu_mixin, gpu_setup
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.gpu_setup import NVIDIA_DISPLAY_NOTE
from test_app_wizard import _advance_to_step
from test_gpu_setup import ANSWERS, SEQUOIA, Env, ready  # noqa: F401

HINT = "etc/profile.d/osx-proxmox-next-gpu.sh"


@pytest.fixture(autouse=True)
def _vm_list(monkeypatch):
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["VMID NAME STATUS", "100 macos-sequoia stopped"])


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
    await settle(pilot, lambda: "[1 Scan]" in text(app, "#gp_rail"))


def pick_vm(app, key: str) -> None:
    """Highlight one entry of the VM picker (a VM id, "later" or "create")."""
    olist = app.query_one("#gp_vm_list", OptionList)
    ids = [olist.get_option_at_index(i).id for i in range(olist.option_count)]
    olist.highlighted = ids.index(key)


def vm_entries(app) -> dict:
    olist = app.query_one("#gp_vm_list", OptionList)
    return {olist.get_option_at_index(i).id: olist.get_option_at_index(i) for i in range(olist.option_count)}


def run(coro_fn):
    asyncio.run(coro_fn())


def amd_env(tmp_path: Path, **kw) -> Env:
    env = Env(tmp_path, **kw)
    add_card(env.root)
    return env


def test_menu_has_one_gpu_tab_with_a_beta_tag_and_no_wizard_rail():
    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            assert label(app, "#mode_gpu") == "GPU Passthrough"
            with pytest.raises(NoMatches):
                app.query_one("#mode_nvidia")  # NVIDIA goes through the guided flow
            with pytest.raises(NoMatches):
                app.query_one("#nvidia_panel")
            assert text(app, "#gp_tag") == "Beta" and text(app, "#gp_name") == "GPU Passthrough"
            assert not shown(app, "#gpu_panel") and shown(app, "#step_bar")
            await pilot.click("#mode_gpu")
            assert shown(app, "#gpu_panel") and not shown(app, "#step_bar")  # only the GPU rail shows
            await pilot.click("#mode_manage")
            assert shown(app, "#step_bar")
            await pilot.click("#mode_create")
            assert shown(app, "#step_bar") and "1.Preflight" in text(app, "#step_bar")
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
            assert shown(app, "#gp_gpu_select") and shown(app, "#gp_display_radio") and not shown(app, "#gp_ack_in")
            app.query_one("#gp_gpu_select", Select).value = "0000:06:00"
            await settle(pilot, lambda: not app.query_one("#gp_primary_btn", Button).disabled)
            pick_vm(app, "100")
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
            await settle(pilot, lambda: shown(app, "#gp_vm_list") and "100" in vm_entries(app))
            assert app.query_one("#gp_vm_list", OptionList).highlighted == 0
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
            assert not shown(app, "#gp_qr") if _has_qr_widget(app) else True
            # one photo: the QR rows sit inside the frame, and the checked items are one line above it
            card = app._gp_card
            assert card["qr"] and all(any(r in ln for ln in body.splitlines()) for r in card["qr"] if r.strip())
            assert body.startswith("What we checked:") and "[ok]" in body and "[!!]" in body and body.splitlines()[0].count("[!!]") == 1
            assert len(card["text"].splitlines()) < 40
            # the primary action is the reboot; re-check is secondary; two clicks (with the running VMs named) reboot
            assert label(app, "#gp_primary_btn") == "Reboot into BIOS now" and label(app, "#gp_secondary_btn") == "Re-check"
            assert label(app, "#gp_tertiary_btn") == "I'll do it later"
            await press(pilot, app, "#gp_primary_btn")
            assert "101 (vm101)" in text(app, "#gp_result") or "101" in text(app, "#gp_result")
            assert not env.run.ran("systemctl", "reboot", "--firmware-setup")
            await press(pilot, app, "#gp_primary_btn")
            assert "Rebooting into the BIOS" in text(app, "#gp_result") and env.run.ran("systemctl", "reboot", "--firmware-setup")
            await press(pilot, app, "#gp_back_btn")
            await settle(pilot, lambda: "Step 1-2" in text(app, "#gp_title"))
    run(go)


def _has_qr_widget(app) -> bool:
    try:
        app.query_one("#gp_qr")
        return True
    except NoMatches:
        return False


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
            # no firmware reboot here: the card names the key to press, and the button is absent
            assert label(app, "#gp_primary_btn") == "Re-check" and label(app, "#gp_secondary_btn") == "I'll do it later"
            assert not shown(app, "#gp_tertiary_btn")
            await press(pilot, app, "#gp_secondary_btn")
            assert "osx-next-cli gpu-setup" in text(app, "#gp_result")
            await press(pilot, app, "#gp_primary_btn")  # re-check: still failing, card again
            await settle(pilot, lambda: "BIOS STEPS" in text(app, "#gp_body"))
    run(go)


def test_nvidia_needs_a_typed_acknowledgement_that_never_carries_over(tmp_path):
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
            assert shown(app, "#gp_notice") and shown(app, "#gp_ack_in")
            # NVIDIA has no display choice: no radio set, one note instead
            assert not shown(app, "#gp_display_radio") and not shown(app, "#gp_display_label")
            assert shown(app, "#gp_display_note") and text(app, "#gp_display_note") == NVIDIA_DISPLAY_NOTE
            assert "Proxmox console goes black" in NVIDIA_DISPLAY_NOTE and "dummy plug" in NVIDIA_DISPLAY_NOTE
            assert not any(isinstance(w, Checkbox) and w.id in ("gp_ack_cb", "gp_confirm_cb") for w in app.query(Checkbox))
            assert app.query_one("#gp_primary_btn", Button).disabled
            pick_vm(app, "100")
            app.query_one("#gp_ack_in", Input).value = "i accept"  # exact phrase only
            await pilot.pause()
            assert app.query_one("#gp_primary_btn", Button).disabled
            app.query_one("#gp_ack_in", Input).value = "I accept"
            await settle(pilot, lambda: not app.query_one("#gp_primary_btn", Button).disabled)
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 4" in text(app, "#gp_title"))
            assert app._gp.state["ack_nullmoth"] is True and app._gp.state["display"] == "console"
            assert app.query_one("#gp_ack_in", Input).value == ""  # reset once it was used
            # opening the screen again starts with an empty box and a disabled button
            app._gp.goto(1)
            app.query_one("#gp_ack_in", Input).value = "I accept"
            app._gp_open()
            await settle(pilot, lambda: "scan and choose" in text(app, "#gp_title"))
            assert app.query_one("#gp_ack_in", Input).value == ""
            app.query_one("#gp_gpu_select", Select).value = "0000:01:00"
            await pilot.pause()
            assert app.query_one("#gp_primary_btn", Button).disabled
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
            pick_vm(app, "100")
            await pilot.pause()
            env.qm.vms[120] = ("running", "hostpci0: 0000:06:00,pcie=1\n")  # another VM took the card meanwhile
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "In use by VM 120" in text(app, "#gp_result"))
            assert "Refused:" in text(app, "#gp_result")
    run(go)


def test_the_vm_picker_lists_this_hosts_vms_and_disables_the_ones_that_cannot_take_the_card(tmp_path, monkeypatch):
    env = Env(tmp_path, vms={106: ("stopped", SEQUOIA.replace("sequoia", "sequoia")), 107: ("stopped", SEQUOIA.replace("sequoia", "sonoma")),
                             108: ("running", SEQUOIA)})
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    names = {106: "sequoia-gpu", 107: "sonoma-old", 108: "busy"}
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["VMID NAME STATUS"] + [
        f"{vmid} {names[vmid]} {env.qm.vms[vmid][0]}" for vmid in names])
    stopped = []
    env.qm.run_orig = env.qm.run

    def run_qm(argv):
        if argv[:2] == ["qm", "stop"]:
            stopped.append(argv[2])
            env.qm.vms[int(argv[2])] = ("stopped", env.qm.vms[int(argv[2])][1])
        return env.qm.run_orig(argv)
    env.qm.run = run_qm

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: shown(app, "#gp_vm_list"))
            app.query_one("#gp_gpu_select", Select).value = "0000:01:00"
            await settle(pilot, lambda: "106" in vm_entries(app))
            entries = vm_entries(app)
            assert list(entries) == ["106", "107", "108", "later", "create"]
            assert str(entries["106"].prompt).split()[:2] == ["106", "sequoia-gpu"] and "macOS 15" in str(entries["106"].prompt)
            assert "stopped" in str(entries["106"].prompt) and not entries["106"].disabled
            assert entries["107"].disabled and "macOS 14: NullMoth needs 15.5+" in str(entries["107"].prompt)
            assert not entries["108"].disabled and "running: stop it first" in str(entries["108"].prompt)
            assert str(entries["later"].prompt) == "Set up the host now, pick a VM later"
            assert str(entries["create"].prompt) == "Create a new macOS VM first"
            app.query_one("#gp_ack_in", Input).value = "I accept"
            pick_vm(app, "108")  # a running VM: the Stop button appears and Choose stays off until it is stopped
            await pilot.pause()
            assert shown(app, "#gp_stop_btn") and label(app, "#gp_stop_btn") == "Stop VM 108"
            assert app.query_one("#gp_primary_btn", Button).disabled
            await press(pilot, app, "#gp_stop_btn")
            assert label(app, "#gp_stop_btn").startswith("Yes, stop VM 108") and not stopped  # asks first
            await press(pilot, app, "#gp_stop_btn")
            await settle(pilot, lambda: stopped == ["108"] and "is stopped" in text(app, "#gp_result"))
            await settle(pilot, lambda: "stopped" in str(vm_entries(app)["108"].prompt))
            assert not shown(app, "#gp_stop_btn") and not app.query_one("#gp_primary_btn", Button).disabled
            pick_vm(app, "later")
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: app._gp.state["step"] >= 3)
            assert app._gp.state["vm"] is None
    run(go)


def test_the_display_choice_is_two_full_radio_options(tmp_path):
    env = amd_env(tmp_path)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: shown(app, "#gp_display_radio"))
            assert [str(app.query_one(f"#gp_display_{i}").label) for i in (0, 1)] == [
                "Monitor on the GPU", "Keep the Proxmox console"]
            assert [text(app, f"#gp_display_desc_{i}").strip() for i in (0, 1)] == [
                "Best performance. Plug a screen into the card; the Proxmox console goes blank.",
                "The card speeds up apps and you keep using the web console."]
            assert not app.query_one("#gp_display_0").disabled and not shown(app, "#gp_display_note")
            with pytest.raises(NoMatches):
                app.query_one("#gp_display_select")  # the truncated dropdown is gone
            app.query_one("#gp_display_1").value = True
            await pilot.pause()
            app.query_one("#gp_gpu_select", Select).value = "0000:06:00"
            await settle(pilot, lambda: not app.query_one("#gp_primary_btn", Button).disabled)
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: app._gp.state["step"] >= 3)
            assert app._gp.state["display"] == "console"
    run(go)


def test_scan_cards_have_one_width_usable_first_and_a_packed_summary(tmp_path):
    env = amd_env(tmp_path)
    add_card(env.root, bdf="0000:41:00", device=0x73DF, group=9)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "Works natively" in text(app, "#gp_body"))
            body = text(app, "#gp_body")
            borders = [ln for ln in body.splitlines() if ln.strip().startswith(("+=", "+-", ">> +"))]
            assert len({len(ln) for ln in borders}) == 1 and len(borders[0]) > 76  # full width of the panel
            assert body.index("Navi 23") < body.index("Navi 22")
            assert body.count("Can't use:") == 1
            assert "PVE version unknown" not in body and "unknown" not in body.splitlines()[0]
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
            assert "only display adapter" in text(app, "#gp_body") and shown(app, "#gp_confirm_in")
            # a typed phrase, never a box that stays ticked: the button stays off until it is typed exactly
            assert app.query_one("#gp_primary_btn", Button).disabled
            app.query_one("#gp_confirm_in", Input).value = "i understand"
            await pilot.pause()
            assert app.query_one("#gp_primary_btn", Button).disabled
            app.query_one("#gp_confirm_in", Input).value = "I understand"
            await pilot.pause()
            assert not app.query_one("#gp_primary_btn", Button).disabled
            await press(pilot, app, "#gp_primary_btn")  # re-plans with the confirmation
            await settle(pilot, lambda: "Rescue" in text(app, "#gp_body"))
            assert app.query_one("#gp_confirm_in", Input).value == "" and not shown(app, "#gp_confirm_in")
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
    for ch in s.host_plan(write_optional=True)["changes"]:
        write(env.root, ch["path"], ch["new"])
    env.run.answers["update-initramfs"] = (0, "")

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            plan = s.host_plan()
            plan["commands"] = []
            s.host_plan = lambda allow_no_console=False, write_optional=False: plan
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
            env.qm.vms[130] = ("running", SEQUOIA)
            await press(pilot, app, "#gp_primary_btn")  # the first click only asks, and names what will stop
            assert "130 (vm130)" in text(app, "#gp_result") and label(app, "#gp_primary_btn") == "Yes, reboot now"
            assert not env.run.ran("systemctl", "reboot")
            await press(pilot, app, "#gp_primary_btn")
            assert "Rebooting..." in text(app, "#gp_result") and env.run.ran("systemctl", "reboot")
            env.run.answers["systemctl reboot"] = (1, "nope")
            await press(pilot, app, "#gp_primary_btn")
            await press(pilot, app, "#gp_primary_btn")
            assert "Could not start the reboot: nope" in text(app, "#gp_result")
            # any redraw of the screen disarms the button
            await press(pilot, app, "#gp_primary_btn")
            before = sum(1 for c in env.run.calls if c == ["systemctl", "reboot"])
            app._gp_set("Step 5: reboot", "again", "Reboot now")
            await press(pilot, app, "#gp_primary_btn")
            assert sum(1 for c in env.run.calls if c == ["systemctl", "reboot"]) == before
            assert label(app, "#gp_primary_btn") == "Yes, reboot now"
    run(go)


def test_vm_step_messages_and_undo(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    env.qm.vms[100] = ("running", SEQUOIA)
    monkeypatch.setattr(gpu_setup, "list_macos_vms",
                        lambda: ["VMID NAME STATUS", f"100 macos-sequoia {env.qm.vms[100][0]}"])
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
            await settle(pilot, lambda: "100" in vm_entries(app))
            assert "running: stop it first" in str(vm_entries(app)["100"].prompt)
            pick_vm(app, "100")
            await settle(pilot, lambda: shown(app, "#gp_stop_btn"))
            app.query_one("#gp_primary_btn", Button).disabled = False
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
            await press(pilot, app, "#gp_undo_btn")  # undo asks first, like every action with side effects
            assert label(app, "#gp_undo_btn") == "Yes, undo everything" and "Press the button again" in text(app, "#gp_result")
            await press(pilot, app, "#gp_undo_btn")
            await settle(pilot, lambda: "Step" in text(app, "#gp_title"))
    run(go)


def test_nvidia_vm_step_picks_a_release_and_runs_the_whole_setup(tmp_path, monkeypatch):
    from osx_proxmox_next import nullmoth_finish
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    releases = [{"tag": "v3.0-rc1", "prerelease": True, "published": "2026-11-05T00:00:00Z"},
                {"tag": "v2.9", "prerelease": False, "published": "2026-11-01T00:00:00Z"},
                {"tag": "v2.8", "prerelease": False, "published": "2026-10-01T00:00:00Z"}]
    monkeypatch.setattr(nullmoth_finish, "versions", lambda: releases)
    chosen = []

    def finish(self, choice, on_event):
        chosen.append(choice)
        on_event("Building the payload disk")
        return nullmoth_finish.FinishResult(version="v2.8", verified=True, kexts=5, display="no", ip="10.0.0.2",
                                            notes=["a note"])
    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", finish)

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
            await settle(pilot, lambda: shown(app, "#gp_nm_select") and app.query_one("#gp_nm_select").value == "v2.9")
            app.query_one("#gp_nm_select").value = "v2.8"  # an older release instead of the preselected latest
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: label(app, "#gp_primary_btn") == "Attach the card now")
            assert "Remote Login" not in text(app, "#gp_body") and "downloads the NullMoth release" in text(app, "#gp_body")
            await press(pilot, app, "#gp_primary_btn")  # one click: there is no guest checklist to confirm any more
            await settle(pilot, lambda: "Step 8" in text(app, "#gp_title"))
            assert chosen == ["v2.8"] and "installed and loaded" in text(app, "#gp_body") and app._gp.first_boot_qr()
            result = text(app, "#gp_result")
            assert "NullMoth v2.8 is installed and loaded (5 extensions)" in result and "vnc://10.0.0.2" in result
            assert "a note" in result and "dummy plug" in result  # no display was seen on the card
            assert label(app, "#gp_primary_btn") == "Done"
            await press(pilot, app, "#gp_primary_btn")
            assert "Setup finished" in text(app, "#gp_title")
    run(go)


def test_nvidia_vm_step_offline_and_failed_setup(tmp_path, monkeypatch):
    from osx_proxmox_next import nullmoth_finish
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)

    def offline():
        raise nullmoth_finish.FinishError("offline")
    monkeypatch.setattr(nullmoth_finish, "versions", offline)
    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", lambda self, choice, on_event: nullmoth_finish.FinishResult(
        verified=False, detail="the driver installer stopped: STOP"))

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
            await settle(pilot, lambda: shown(app, "#gp_nm_select"))
            assert app._gp_release_choice() == "latest"  # offline: the install resolves "latest" itself
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: label(app, "#gp_primary_btn") == "Attach the card now")
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "not confirmed" in text(app, "#gp_result"))
            assert "STOP" in text(app, "#gp_result") and "Step 7" in text(app, "#gp_title")
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


def test_a_moved_card_is_explained_and_the_user_goes_back_to_the_scan(tmp_path):
    from test_gpu_hardening import MOVED, swap_in_nvme
    env = amd_env(tmp_path)

    async def go():
        app = NextApp()
        async with app.run_test(size=(140, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.choose("06:00", 100, "gpu")
            s.complete(3, skipped=True)
            swap_in_nvme(env.root)
            app._gp = s
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: MOVED in text(app, "#gp_result"))
            await settle(pilot, lambda: "Step 1-2" in text(app, "#gp_title"))
            assert s.state["gpu"] is None and s.state["step"] == 1
            assert not (env.root / "etc/modprobe.d/osx-proxmox-next-vfio.conf").exists()
    run(go)


def test_the_host_confirmation_is_empty_every_time_the_step_opens(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, boot_vga=True)

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
            await settle(pilot, lambda: "only display adapter" in text(app, "#gp_body"))
            app.query_one("#gp_confirm_in", Input).value = "I understand"
            await pilot.pause()
            await press(pilot, app, "#gp_back_btn")
            await settle(pilot, lambda: "Step 1-2" in text(app, "#gp_title"))
            s.complete(1)
            s.complete(2)
            s.complete(3, skipped=True)
            app._gp_open()
            await settle(pilot, lambda: "only display adapter" in text(app, "#gp_body"))
            assert app.query_one("#gp_confirm_in", Input).value == "" and app.query_one("#gp_primary_btn", Button).disabled
            assert not s.state.get("allow_no_console")  # the earlier confirmation was not kept anywhere
    run(go)
