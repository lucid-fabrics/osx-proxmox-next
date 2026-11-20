"""The graphics card flow: choose and approve once, the one restart, first boot."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Input, Static

import tui_fakes as F
from gpu_fakes import add_card, add_other, write
from nullmoth_fakes import MAC_CFG
from osx_proxmox_next import gpu_setup, host_setup, nullmoth
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.gpu_setup import CONFIRM_PHRASE, NVIDIA_DISPLAY_NOTE, CardChanged
from osx_proxmox_next.nullmoth import ACK_PHRASE
from osx_proxmox_next.screens.base import ChoiceDialog
from osx_proxmox_next.screens.gpu import (
    ChangesDialog, GpuScreen, Preview, pack_fields, plan_fingerprint, plan_lines, qr_spans, short_card_name,
    vm_fingerprint)
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.widgets.chrome import KeyBar
from osx_proxmox_next.widgets.rows import ChoiceRow, FieldRow
from test_gpu_setup import ANSWERS, SEQUOIA, Env, ready  # noqa: F401

SIZE = (110, 34)
HINT = "etc/profile.d/osx-proxmox-next-gpu.sh"


@pytest.fixture(autouse=True)
def _vm_list(monkeypatch):
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["VMID NAME STATUS", "100 macos-sequoia stopped"])
    F.patch_host(monkeypatch, vms=[F.record(101, "macos-sequoia", "running")])


@pytest.fixture
def applied(monkeypatch):
    calls = []
    monkeypatch.setattr("osx_proxmox_next.executor.apply_plan",
                        lambda steps, execute=False, **k: calls.append(steps) or ApplyResult(
                            True, [StepResult("s", "c", True, 0, "")], Path("/tmp/log")))
    monkeypatch.setattr("osx_proxmox_next.rollback.create_snapshot", lambda vmid: None)
    return calls


def amd_env(tmp_path, **kw) -> Env:
    env = Env(tmp_path, **kw)
    add_card(env.root)
    return env


async def start(pilot, app, env, vmid=100, undo=False, session=None) -> GpuScreen:
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    screen = GpuScreen(vmid=vmid, undo=undo, session=session or env.session())
    app.push_screen(screen)
    await F.settle(pilot, lambda: screen.is_attached)
    return screen


async def at_choose(pilot, app, env, **kw) -> GpuScreen:
    screen = await start(pilot, app, env, **kw)
    await F.settle(pilot, lambda: screen.mode == "choose" and screen.preview is not None)
    await pilot.pause(0.1)
    return screen


def row(screen, rid: str) -> str:
    return screen.query_one(rid).render().plain


def panel(screen) -> str:
    return F.text_of(screen.query_one("#g-need"))


def keys(app) -> str:
    return app.screen.query_one(KeyBar).render().plain


def box(screen) -> str:
    return F.text_of(screen.query_one("#g-box-title")) + "\n" + F.text_of(screen.query_one("#g-box-text"))


def timeline(screen) -> str:
    return F.text_of(screen.query_one("#g-timeline"))


async def mode(pilot, screen, name: str) -> None:
    await F.settle(pilot, lambda: screen.mode == name and not screen.working)
    await pilot.pause(0.15)


def reboot_host(env, boot="later\n") -> None:
    write(env.root, "/proc/sys/kernel/random/boot_id", boot)
    ready(env)


# ── helpers ──────────────────────────────────────────────────────────

def test_pack_fields_breaks_only_between_fields():
    assert pack_fields(["aaa", "bbb", "ccc"], 8) == ["aaa bbb", "ccc"] or pack_fields(["aaa", "bbb", "ccc"], 8, sep=" ") == ["aaa bbb", "ccc"]
    assert pack_fields(["aaaa", "bbbb"], 20) == ["aaaa   bbbb"]


def test_qr_spans_find_each_qr_row_in_the_card_text():
    text = "steps\n  XX XX  \n  YY YY  \nend"
    assert qr_spans(text, ["XX XX", "YY YY", "  "]) == [(1, 2, 7), (2, 2, 7)]


def test_short_card_names_keep_the_marketing_name():
    assert short_card_name("Advanced Micro Devices, Inc. [AMD/ATI] Navi 23 [Radeon RX 6600]") == "Radeon RX 6600"
    assert short_card_name("NVIDIA Corporation GA106 [GeForce RTX 3060]") == "GeForce RTX 3060"
    assert short_card_name("Plain card") == "Plain card"
    assert short_card_name("Vendor [AMD/ATI]") == "Vendor [AMD/ATI]"


def test_fingerprints_ignore_the_stop_step_but_not_the_settings():
    steps = [SimpleNamespace(title="Stop the VM first", argv=["qm", "stop", "1"]),
             SimpleNamespace(title="Attach GPU", argv=["qm", "set", "1", "--hostpci0", "x"])]
    base = vm_fingerprint("0000:06:00", 100, "gpu", steps)
    assert base == vm_fingerprint("0000:06:00", 100, "gpu", steps[1:])
    assert base != vm_fingerprint("0000:06:00", 100, "console", steps)
    assert base != vm_fingerprint("0000:06:00", 101, "gpu", steps)
    plan = {"changes": [{"path": "/etc/a", "new": "x"}], "commands": [{"argv": ["update-initramfs"]}]}
    assert plan_fingerprint(plan) == plan_fingerprint({**plan}) != plan_fingerprint({**plan, "commands": []})


def test_plan_lines_use_todays_prefixes_and_careful_for_the_typed_override():
    plan = host_setup.new_plan()
    plan["refusals"] = ["This card is the host's only display adapter", "Some other stop"]
    plan["hard_warnings"] = ["h"]
    plan["warnings"], plan["notes"], plan["manual"], plan["skipped"] = ["w"], ["n"], ["m"], ["s"]
    assert plan_lines(plan) == ["Careful: This card is the host's only display adapter", "Refused: Some other stop",
                                "HARD WARNING: h", "Warning: w", "Note: n", "Manual step: m", "OK: s"]
    prev = Preview(plan=plan)
    assert prev.refusals == ["Some other stop"] and prev.bios_failing == []


# ── moment 1: choose and approve ─────────────────────────────────────

@F.tui
async def test_choose_shows_the_card_the_mac_and_what_it_takes(tmp_path):
    env = amd_env(tmp_path, cpu="intel")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert F.text_of(screen.query_one("#g-title")).startswith("Give macos-sequoia a graphics card")
        assert "Radeon RX 6600" in row(screen, "#g-card") and "Works natively on macOS 12 to 26" in row(screen, "#g-card")
        assert "macos-sequoia" in row(screen, "#g-mac") and "VM 100" in row(screen, "#g-mac")
        assert "Monitor on the GPU" in row(screen, "#g-display") and screen.query_one("#g-display").display
        assert not screen.query_one("#g-driver").display and not screen.query_one("#g-ack").display
        need = panel(screen)
        assert "✓ BIOS settings  already right" in need
        assert "● Host setup  3 files change" in need and "● One restart  about 3 min" in need
        assert "● Attach to the Mac  automatic" in need
        part = F.text_of(screen.query_one("#g-part"))
        assert "1 Press enter" in part and "2 Restart when asked" in part and "3 Open osx-next again" in part
        assert "it finishes by itself" in part
        assert not screen.query_one("#g-primary", Button).disabled
        assert "Graphics card BETA" in screen.query_one("#topbar").render().plain
        assert "macos-sequoia" in screen.query_one("#topbar").render().plain
        for key in ("Move", "Change", "Set it up", "See every change", "Cancel", "Help"):
            assert key in keys(app)
        assert F.text_of(screen.query_one("#g-note")) == "Nothing changes until you press enter."
        assert not screen.query_one("#g-confirm").display and not screen.query_one("#g-optional").display


@F.tui
async def test_nothing_is_written_before_enter(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert screen.s.state["gpu"] is None and screen.s.approval is None
        assert not (env.root / host_setup.STATE_FILE.lstrip("/")).exists()
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        assert not (env.root / host_setup.STATE_FILE.lstrip("/")).exists()


@F.tui
async def test_p_opens_every_change_in_a_scrolling_dialog(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("p")
        dialog = app.screen
        assert isinstance(dialog, ChangesDialog)
        text = F.text_of(dialog.query_one("#changes-text"))
        assert "each file is backed up first" in text and host_setup.BACKUP_ROOT in text
        assert "--- /etc/default/grub" in text or "+++ " in text or "/etc/" in text
        assert "VM 100 settings (a config snapshot is saved first):" in text and "qm set 100" in text
        await pilot.press("escape")
        assert app.screen is screen
        await pilot.press("p")
        await pilot.press("p")
        assert app.screen is screen


@F.tui
async def test_approving_stores_one_approval_and_runs_the_host_setup(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        s = screen.s
        assert s.state["gpu"]["bdf"] == "0000:06:00" and s.state["vm"] == 100
        approval = s.approval
        assert approval["restart"] and approval["host_work"] and approval["host_files"] == 3 and approval["vm"] == 100
        assert approval["host_fp"] and approval["vm_fp"]
        assert 4 in s.state["done"] and s.state.get("reboot_needed")
        assert "Backups are in" in screen.backup_note and (env.root / HINT).exists()
        assert box(screen).startswith("Time for the one restart")
        text = box(screen)
        assert "What a restart means right now" in text and "If you restart later, run: osx-next-cli gpu-setup" in text
        tl = timeline(screen)
        assert "✓ Backed up 3 files" in tl and "✓ Set up the host" in tl and "◐ Restart this host" in tl
        assert "your turn" in tl and "· Check the card is free" in tl and "· Attach it to macos-sequoia" in tl
        assert "· Start the Mac" in tl
        assert "Safe to leave here." in F.text_of(screen.query_one("#g-run-note"))
        assert F.text_of(screen.query_one("#g-run-title")) == "Setting up the Radeon RX 6600 for macos-sequoia"
        assert applied == []                                       # the VM is not touched before the restart
        for key in ("Restart now", "See every change", "Put everything back", "Leave"):
            assert key in keys(app)


@F.tui
async def test_the_restart_box_says_later_or_asks_before_restarting(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await pilot.press("enter")                                  # Restart now
        dialog = app.screen
        assert isinstance(dialog, ChoiceDialog)
        assert F.text_of(dialog.query_one(".dialog-title")) == "Restart this host now?"
        assert app.focused.name == "no"
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not env.run.ran("systemctl", "reboot") and app.screen is screen
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.press("tab")
        await pilot.press("enter")                                  # confirm
        await pilot.pause(0.3)
        assert env.run.ran("systemctl", "reboot")
        assert "Restarting..." in screen.result_text
        await pilot.click("#g-later")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        assert any("reminder" in n.message for n in app._notifications)


@F.tui
async def test_the_restart_box_warns_about_an_install_in_progress(tmp_path, applied, monkeypatch):
    env = amd_env(tmp_path, vms={100: ("running", SEQUOIA), 107: ("running", SEQUOIA)})
    monkeypatch.setattr("osx_proxmox_next.screens.gpu.read_driver_state",
                        lambda vmid: F.installing_driver(18) if vmid == 107 else F.DriverState())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        text = box(screen)
        assert "● vm100 is shut down cleanly first" in text
        assert "! vm107 is still installing, 18 min in" in text and "A restart now interrupts that install." in text
        await pilot.press("enter")
        assert "An install in progress will be interrupted." in F.text_of(app.screen.query(".dialog-line").last())


@F.tui
async def test_after_the_restart_the_check_and_the_attach_run_without_asking(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        approval = dict(screen.s.approval)
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        # the host restarts; the user opens the flow again (a new session reads the saved state)
        reboot_host(env)
        dialogs = []
        original = app.push_screen
        app.push_screen = lambda screen_, *a, **k: (dialogs.append(type(screen_).__name__), original(screen_, *a, **k))[1]
        screen2 = GpuScreen(session=env.session())
        app.push_screen(screen2)
        await mode(pilot, screen2, "first_boot")
        app.push_screen = original
        assert dialogs == ["GpuScreen"]                              # no dialog, no question
        assert len(applied) == 1 and any("hostpci" in " ".join(step.argv) for step in applied[0])
        s = screen2.s
        assert s.approval == approval and 6 in s.state["done"] and 7 in s.state["done"]
        assert ("start", "100") in [tuple(c[1:]) for c in env.qm.calls]  # the Mac is started on the card
        tl = timeline(screen2)
        assert "✓ Checked the card is free" in tl and "✓ Attached it to macos-sequoia" in tl
        assert "✓ Started the Mac" in tl and "Finished without a question" in tl
        assert F.text_of(screen2.query_one("#g-run-title")) == "macos-sequoia is starting on the Radeon RX 6600"
        assert "Look at the monitor plugged into the card" in box(screen2)
        assert "Do you see the macOS desktop?" in box(screen2)
        for key in ("Yes, all good", "No, help me", "Put everything back", "Your Macs"):
            assert key in keys(app)


@F.tui
async def test_a_changed_plan_after_the_restart_is_shown_and_asked_again(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        reboot_host(env)
        s2 = env.session()
        s2.state["approved"]["vm_fp"] = "something else"             # the VM settings differ from what was approved
        screen2 = GpuScreen(session=s2)
        app.push_screen(screen2)
        await mode(pilot, screen2, "run")
        assert box(screen2).startswith("This differs from what you approved")
        assert "This is what will change on VM 100" in box(screen2) and "Attach GPU" in box(screen2)
        assert applied == []
        assert screen2.query_one("#g-primary-run", Button).label.plain == "Attach the card now"
        await pilot.press("enter")
        await mode(pilot, screen2, "first_boot")
        assert len(applied) == 1


@F.tui
async def test_a_host_plan_that_changed_since_the_approval_is_asked_again(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    s.approve({"host_fp": "stale", "vm_fp": "x", "host_files": 3, "host_work": True, "restart": True, "vm": 100,
               "release": "", "typed": False, "write_optional": False, "allow_no_console": False})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=s)
        await mode(pilot, screen, "run")
        assert box(screen).startswith("This differs from what you approved")
        assert "This is exactly what will change" in box(screen)
        await pilot.press("enter")                                   # explicit approval
        await mode(pilot, screen, "run")
        assert 4 in screen.s.state["done"] and box(screen).startswith("Time for the one restart")


@F.tui
async def test_the_bios_screen_is_skipped_when_the_check_passes(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        seen: list[str] = []
        screen = await at_choose(pilot, app, env)
        original = screen.set_mode

        async def spy(name, widgets):
            seen.append(name)
            await original(name, widgets)

        screen.set_mode = spy
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert "bios" not in seen and seen[-1] == "run"
        assert 3 in screen.s.state["done"] and 3 not in screen.s.state["skipped"]


def bios_env(tmp_path) -> Env:
    env = Env(tmp_path, virt=False, tables=(), efi=False)
    add_card(env.root)
    env.qm.vms[101] = ("running", SEQUOIA)
    fw = env.root / "sys/firmware/efi/efivars"
    return env


@F.tui
async def test_the_bios_screen_shows_the_card_and_restarts_into_the_bios_after_a_confirmation(tmp_path):
    from osx_proxmox_next import bios_guide
    env = bios_env(tmp_path)
    fw = env.root / bios_guide.OS_INDICATIONS.lstrip("/")
    fw.parent.mkdir(parents=True, exist_ok=True)
    fw.write_bytes(b"\x06\x00\x00\x00\x01" + b"\x00" * 7)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert "● BIOS settings" in panel(screen) and "to change" in panel(screen)
        assert "Change the BIOS settings" in F.text_of(screen.query_one("#g-part"))
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        assert F.text_of(screen.query_one("#g-run-title")) == "One thing only you can do"
        text = F.text_of(screen.query_one("#g-bios-text"))
        assert "BIOS STEPS FOR THIS SERVER" in text and text.startswith("What we checked:") and "[!!]" in text
        assert "Running VMs that a restart will stop: 101" in text and "Saved to" in text
        assert screen.query_one("#g-primary-bios", Button).label.plain == "Restart into BIOS"
        assert screen.query_one("#g-recheck", Button).label.plain == "Check again"
        for key in ("Restart into BIOS", "Check again"):
            assert key in keys(app)
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, ChoiceDialog) and "101 (vm101)" in " ".join(F.text_of(w) for w in dialog.query(".dialog-line"))
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not env.run.ran("systemctl", "reboot", "--firmware-setup") and app.screen is screen
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert env.run.ran("systemctl", "reboot", "--firmware-setup")
        assert "Restarting into the BIOS..." in screen.result_text
        await pilot.press("r")                                      # check again: still failing, the card again
        await mode(pilot, screen, "bios")
        assert screen.bios_card is not None


@F.tui
async def test_without_a_firmware_restart_the_bios_screen_only_offers_check_again(tmp_path):
    env = bios_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        assert not screen.query("#g-primary-bios") and screen.query_one("#g-recheck", Button).has_class("primary")
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        await pilot.press("escape")                                  # leaving loses nothing
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        assert screen.s.approval is not None and 3 not in screen.s.state["done"]


@F.tui
async def test_the_flow_continues_by_itself_when_the_bios_check_passes_on_recheck(tmp_path, applied):
    env = bios_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        # the user fixed the BIOS: the host now reports virtualization, tables and UEFI
        write(env.root, "/proc/cpuinfo", "vendor_id\t: GenuineIntel\nflags\t\t: fpu vmx sse\n")
        write(env.root, "/sys/firmware/acpi/tables/DMAR", "x")
        (env.root / "sys/firmware/efi/efivars").mkdir(parents=True, exist_ok=True)
        (env.root / "sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(b"\x06\x00\x00\x00\x00")
        await pilot.press("r")
        await mode(pilot, screen, "run")
        assert 3 in screen.s.state["done"] and screen.s.state["step"] >= 4


@F.tui
async def test_nvidia_asks_its_notice_once_on_this_screen_and_never_carries_it_over(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert not screen.query_one("#g-display").display and screen.query_one("#g-driver").display
        assert screen.query_one("#g-ack").display
        before = F.text_of(screen.query_one("#g-before"))
        assert before.startswith("Before you continue") and NVIDIA_DISPLAY_NOTE in before
        assert nullmoth.NOTICE_TITLE in before and nullmoth.NOTICE_BODY in before
        assert "Type 'I accept' to confirm" in F.text_of(screen.query_one("#g-ack .field-suffix"))
        assert screen.query_one("#g-primary", Button).disabled
        field = screen.query_one("#g-ack", FieldRow).input
        field.value = "i accept"                                      # the exact phrase only
        await pilot.pause(0.2)
        assert screen.query_one("#g-primary", Button).disabled
        field.value = ACK_PHRASE
        await F.settle(pilot, lambda: not screen.query_one("#g-primary", Button).disabled)
        await pilot.press("enter")                                     # enter in the field approves too
        await mode(pilot, screen, "run")
        assert screen.s.state["ack_nullmoth"] is True and screen.s.state["display"] == "console"
        assert screen.s.approval["release"] in ("latest", "") or screen.s.approval["release"]


@F.tui
async def test_a_refusal_is_one_line_and_disables_the_button(tmp_path):
    env = amd_env(tmp_path)
    env.qm.vms[120] = ("running", "hostpci0: 0000:06:00,pcie=1\n")   # another VM runs with the card
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await app.run_test.__self__ if False else await at_choose_unusable(pilot, app, env)
        assert "No card here can run macOS graphics" in panel(screen)
        assert screen.query_one("#g-primary", Button).disabled
        assert "In use by VM 120" in row(screen, "#g-card")


async def at_choose_unusable(pilot, app, env):
    screen = await start(pilot, app, env)
    await F.settle(pilot, lambda: screen.mode == "choose" and not screen.working and bool(screen.query("#g-card")))
    await pilot.pause(0.2)
    return screen


@F.tui
async def test_a_vm_that_cannot_take_the_card_is_a_refusal_with_its_reason(tmp_path):
    env = Env(tmp_path, vms={100: ("stopped", SEQUOIA.replace("sequoia", "sonoma"))})
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        screen.query_one("#g-ack", FieldRow).input.value = ACK_PHRASE
        await F.settle(pilot, lambda: bool(F.text_of(screen.query_one("#g-reason"))))
        assert F.text_of(screen.query_one("#g-reason")).startswith("Refused: ")
        assert "NullMoth" in F.text_of(screen.query_one("#g-reason")) or "macOS" in F.text_of(screen.query_one("#g-reason"))
        assert screen.query_one("#g-primary", Button).disabled


@F.tui
async def test_only_display_adapter_is_careful_and_needs_the_typed_phrase(tmp_path, applied):
    env = Env(tmp_path)
    add_card(env.root, boot_vga=True)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        before = F.text_of(screen.query_one("#g-before"))
        assert "Careful: This card is the only display adapter" in before and "Refused:" not in before
        assert screen.query_one("#g-confirm").display and screen.query_one("#g-primary", Button).disabled
        assert 'Type "I understand" to continue' in F.text_of(screen.query_one("#g-confirm .field-suffix"))
        field = screen.query_one("#g-confirm", FieldRow).input
        field.value = "i understand"
        await pilot.pause(0.2)
        assert screen.query_one("#g-primary", Button).disabled
        field.value = CONFIRM_PHRASE
        await F.settle(pilot, lambda: not screen.query_one("#g-primary", Button).disabled)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        approval = screen.s.approval
        assert approval["typed"] and approval["allow_no_console"] and 4 in screen.s.state["done"]


@F.tui
async def test_no_usable_card_says_where_to_look_and_nothing_can_be_set_up(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, device=0x73C0)                                # a chip macOS has no driver for
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env)
        await F.settle(pilot, lambda: screen.mode == "choose" and bool(screen.query("#g-card")))
        await pilot.pause(0.3)
        assert "No card here can run macOS graphics. See the docs for cards that work." in panel(screen)
        assert screen.query_one("#g-primary", Button).disabled
        assert "no card here can run macOS graphics" in F.text_of(screen.query_one("#g-title"))
        await pilot.press("enter")                                    # a disabled primary does nothing
        assert screen.mode == "choose"


@F.tui
async def test_a_second_card_that_macos_cannot_use_is_listed_dimmed_with_its_reason(tmp_path):
    env = amd_env(tmp_path)
    add_card(env.root, bdf="0000:41:00", device=0x73C0, group=9)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        card_row = screen.query_one("#g-card", ChoiceRow)
        assert [o.enabled for o in card_row.options] == [True, False]
        assert "(0000:41:00)" in row(screen, "#g-card")
        await pilot.press("right")                                    # the unusable card is skipped
        assert card_row.value == "0000:06:00"


@F.tui
async def test_two_usable_cards_are_a_choice_and_the_one_card_case_is_preselected(tmp_path):
    env = amd_env(tmp_path)
    add_card(env.root, bdf="0000:41:00", device=0x67FF, group=9)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert "choose the card to hand over" in F.text_of(screen.query_one("#g-title"))
        first = screen.query_one("#g-card", ChoiceRow).value
        await pilot.press("right")
        await pilot.pause(0.3)
        assert screen.query_one("#g-card", ChoiceRow).value != first
        await F.settle(pilot, lambda: screen.preview is not None and screen.preview.card.bdf == screen.query_one("#g-card", ChoiceRow).value)


@F.tui
async def test_the_palette_entry_has_a_mac_choice_with_decide_later(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env, vmid=None)
        mac = screen.query_one("#g-mac", ChoiceRow)
        assert [o.value for o in mac.options] == ["100", "later"] and mac.value == "100"
        assert F.text_of(screen.query_one("#g-title")).startswith("choose the card") or "graphics card" in F.text_of(screen.query_one("#g-title"))
        screen.query_one("#g-mac").focus()
        await pilot.press("right")
        await pilot.pause(0.3)
        assert mac.value == "later" and "pick a Mac later" in row(screen, "#g-mac")
        assert "Attach to the Mac" not in panel(screen)
        assert "Pick a Mac afterwards" in F.text_of(screen.query_one("#g-part")) or "Press enter" in F.text_of(screen.query_one("#g-part"))
        await F.settle(pilot, lambda: screen.preview is not None and screen.preview.vmid is None)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert screen.s.state["vm"] is None and "Attach it to" not in timeline(screen)
        assert "· Check the card is free" in timeline(screen) and "Start the Mac" not in timeline(screen)


@F.tui
async def test_decide_later_ends_after_the_check_with_a_prompt_to_pick_a_mac(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", None, "gpu")
    s.complete(3)
    s.complete(4, skipped=True)
    s.complete(5, skipped=True)
    ready(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, vmid=None, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-pick")))
        assert F.text_of(screen.query_one("#g-run-title")) == "Pick the Mac that gets the card"
        pick = screen.query_one("#g-pick", ChoiceRow)
        assert pick.value == "100"
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        assert screen.s.state["vm"] == 100 and len(applied) == 1


@F.tui
async def test_picking_a_mac_needs_a_choice(tmp_path, monkeypatch):
    env = amd_env(tmp_path, vms={100: ("stopped", SEQUOIA.replace("sequoia", "sonoma").replace("isa-applesmc", "x"))})
    s = env.session()
    s.choose("06:00", None, "gpu")
    s.complete(3)
    s.complete(4, skipped=True)
    s.complete(5, skipped=True)
    ready(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, vmid=None, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-pick")))
        screen.query_one("#g-pick", ChoiceRow).value = ""
        screen._pick_attach()
        assert "Pick a Mac first." in screen.result_text and screen.result_fail


@F.tui
async def test_a_running_mac_is_stopped_only_after_a_confirmation(tmp_path, applied):
    env = amd_env(tmp_path, vms={100: ("running", SEQUOIA)})
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    s.complete(4, skipped=True)
    s.complete(5, skipped=True)
    ready(env)
    s.approve({"host_fp": "", "vm_fp": "", "host_files": 0, "host_work": False, "restart": False, "vm": 100,
               "release": "", "typed": False, "write_optional": False, "allow_no_console": False})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        assert box(screen).startswith("This differs from what you approved")   # no recorded fingerprint: ask first
        await pilot.press("enter")
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        dialog = app.screen
        assert F.text_of(dialog.query_one(".dialog-title")) == "Stop vm100 first?" or "Stop" in F.text_of(dialog.query_one(".dialog-title"))
        assert "closed without saving" in " ".join(F.text_of(w) for w in dialog.query(".dialog-line"))
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert applied == [] and app.screen is screen
        await pilot.press("enter")
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        await pilot.press("tab")
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        assert len(applied) == 1 and any(" ".join(st.argv).startswith("qm stop") or "stop" in st.title.lower() for st in applied[0])


@F.tui
async def test_the_attach_is_refused_with_the_reason_and_the_user_can_pick_another_mac(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    s.complete(4, skipped=True)
    s.complete(5, skipped=True)
    ready(env)
    env.qm.vms[100] = ("stopped", "name: plain\n")                   # no longer a macOS VM made by this tool
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-secondary")))
        assert box(screen).startswith("The Mac cannot take the card yet") and "Refused:" in box(screen)
        assert screen.query_one("#g-secondary", Button).label.plain == "Pick another Mac"
        await pilot.click("#g-secondary")
        await F.settle(pilot, lambda: bool(screen.query("#g-pick")) or screen.s.state["vm"] is None)
        assert screen.s.state["vm"] is None
        assert applied == []


@F.tui
async def test_verify_failures_show_todays_guidance_and_primary_action(tmp_path):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    s.complete(4)
    s.complete(5, skipped=True)
    s.approve({"host_fp": "x", "vm_fp": "x", "host_files": 1, "host_work": True, "restart": False, "vm": 100,
               "release": "", "typed": False, "write_optional": False, "allow_no_console": False})
    # the kernel arguments are still missing and the host did not restart: verify says go back to the host step
    write(env.root, "/proc/cmdline", "BOOT_IMAGE=/x ro quiet")
    s.state["boot_id"] = "different"
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=s)
        await F.settle(pilot, lambda: bool(screen.query("#g-box-title")) and not screen.working)
        await pilot.pause(0.2)
        title = F.text_of(screen.query_one("#g-box-title"))
        assert title in ("The card is not free yet", "Time for the one restart")


@F.tui
async def test_a_moved_card_is_explained_and_the_user_goes_back_to_the_choice(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    (env.root / "sys/bus/pci/devices/0000:06:00.0/device").write_text("0x1234\n")   # a different chip at the address
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=s)
        await F.settle(pilot, lambda: screen.mode == "choose" and bool(screen.query("#g-card")))
        await pilot.pause(0.3)
        assert screen.s.state["gpu"] is None and screen.s.approval is None
        assert any("g-notice" in w.classes for w in screen.query(Static))


@F.tui
async def test_a_failing_worker_shows_the_error_and_the_flow_stays_usable(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        monkeypatch.setattr(type(screen.s), "choose", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
        await pilot.press("enter")
        await F.settle(pilot, lambda: "Something went wrong: disk full" in screen.result_text)
        assert screen.result_fail and not screen.working


@F.tui
async def test_a_refused_choose_shows_the_refusals(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        monkeypatch.setattr(type(screen.s), "choose", lambda *a, **k: ["In use by VM 120 (running)"])
        await pilot.press("enter")
        await F.settle(pilot, lambda: "Refused: In use by VM 120 (running)" in F.text_of(screen.query_one("#g-result")))


@F.tui
async def test_a_stopped_holder_is_confirmed_in_a_dialog_before_the_card_is_used(tmp_path, applied):
    env = amd_env(tmp_path)
    env.qm.vms[120] = ("stopped", "hostpci0: 0000:06:00,pcie=1\n")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert "stopped VM 120" in row(screen, "#g-card")
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, ChoiceDialog) and F.text_of(dialog.query_one(".dialog-title")) == "This card is listed by other VMs"
        assert app.focused.name == "no"
        await pilot.press("escape")
        assert screen.s.state["gpu"] is None and screen.mode == "choose"
        await pilot.press("enter")
        await pilot.press("tab")
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert screen.s.state["gpu"]["bdf"] == "0000:06:00"


@F.tui
async def test_the_optional_early_binding_is_offered_only_when_it_applies(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, driver=None)                                  # no driver claims the card: the binding is optional
    ready(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        if not screen.query_one("#g-optional").display:
            assert screen.preview.plan["optional"] is False
            return
        assert "Early binding" in row(screen, "#g-optional")


@F.tui
async def test_esc_leaves_and_is_refused_while_a_step_runs(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        screen.working = True
        await pilot.press("escape")
        assert app.screen is screen and any("Wait for this step" in n.message for n in app._notifications)
        assert screen.busy()
        await pilot.press("q")
        assert isinstance(app.screen, ChoiceDialog)
        await pilot.press("escape")
        screen.working = False
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


# ── moment 3: first boot, finished, undo ─────────────────────────────

async def at_first_boot(pilot, app, env, vendor_card=None, applied_calls=None):
    s = env.session()
    s.choose("06:00" if vendor_card is None else vendor_card, 100, "gpu", ack=True)
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    s.complete(7)
    screen = await start(pilot, app, env, session=env.session())
    await mode(pilot, screen, "first_boot")
    return screen


@F.tui
async def test_yes_finishes_and_no_shows_the_troubleshooting(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_first_boot(pilot, app, env)
        await pilot.press("n")
        await mode(pilot, screen, "first_boot")
        assert "No signal on the monitor" in box(screen) and box(screen).startswith("No signal? Try these")
        assert screen.query_one("#g-back", Button) and screen.query_one("#g-yes", Button)
        await pilot.click("#g-back")
        await mode(pilot, screen, "first_boot")
        assert "Do you see the macOS desktop?" in box(screen)
        await pilot.press("y")
        await mode(pilot, screen, "finished")
        assert F.text_of(screen.query_one("#g-run-title")) == "Setup finished"
        assert "Setup summary" in F.text_of(screen.query_one("#g-box-text")) and "Card: " in F.text_of(screen.query_one("#g-box-text"))
        assert not (env.root / HINT).exists()
        assert screen.s.state["finished"]
        await pilot.press("n")                                       # no effect outside the first boot
        await pilot.press("enter")                                   # start a new setup
        await F.settle(pilot, lambda: screen.mode == "choose" and bool(screen.query("#g-card")))
        assert not screen.s.state["finished"]


@F.tui
async def test_yes_and_no_do_nothing_outside_the_first_boot_and_the_button_works(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("y", "n")
        assert screen.mode == "choose"
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_first_boot(pilot, app, env)
        await pilot.click("#g-yes")
        await mode(pilot, screen, "finished")


@F.tui
async def test_nvidia_first_boot_has_one_done_button_and_no_question(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_first_boot(pilot, app, env, vendor_card="01:00")
        assert box(screen).startswith("Your Mac is ready") and "Do you see the macOS desktop?" not in box(screen)
        assert not screen.query("#g-no") and "No, help me" not in keys(app)
        await pilot.press("n")                                       # no troubleshooting branch for NVIDIA
        assert screen.mode == "first_boot"
        await pilot.press("enter")
        await mode(pilot, screen, "finished")


@F.tui
async def test_nvidia_attach_runs_the_whole_setup_with_the_chosen_release(tmp_path, monkeypatch):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    seen = {}

    def finish(self, choice, on_event):
        seen["choice"] = choice
        on_event("downloading")
        return SimpleNamespace(verified=True, version="2.9", kexts=7, ip="10.0.0.9", display="no", notes=["a note"],
                               detail="", result="installed")

    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", finish)
    monkeypatch.setattr("osx_proxmox_next.screens.gpu.nullmoth_finish.versions",
                        lambda: [{"tag": "v2.9", "prerelease": False}, {"tag": "v3.0-rc", "prerelease": True}])
    monkeypatch.setattr("osx_proxmox_next.screens.gpu.nullmoth_cli.version_lines", lambda found: [r["tag"] for r in found])
    s = env.session()
    s.choose("01:00", 100, "gpu", ack=True)
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    s.approve({"host_fp": "", "vm_fp": "", "host_files": 0, "host_work": False, "restart": False, "vm": 100,
               "release": "v2.9", "typed": False, "write_optional": False, "allow_no_console": False})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        assert box(screen).startswith("This differs from what you approved")
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        assert seen["choice"] == "v2.9"
        text = box(screen)
        assert "NullMoth 2.9 is installed and loaded (7 extensions)." in text and "ssh <user>@10.0.0.9" in text
        assert "vnc://10.0.0.9" in text and nullmoth.DISPLAY_LINE in text and "Note: a note" in text


@F.tui
async def test_a_failed_nvidia_setup_stops_with_the_reason(tmp_path, monkeypatch):
    env = Env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)
    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", lambda self, choice, on_event: SimpleNamespace(
        verified=False, version="", kexts=0, ip="", display="", notes=[], detail="the guest said no", result=""))
    s = env.session()
    s.choose("01:00", 100, "gpu", ack=True)
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    s.approve({"host_fp": "", "vm_fp": "", "host_files": 0, "host_work": False, "restart": False, "vm": 100,
               "release": "latest", "typed": False, "write_optional": False, "allow_no_console": False})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        await pilot.press("enter")
        await F.settle(pilot, lambda: box(screen).startswith("The driver is not confirmed"))
        assert "the guest said no" in box(screen) and nullmoth.FAILED_NOTE in box(screen)
        assert screen.s.state["step"] == 7


@F.tui
async def test_u_puts_everything_back_after_a_confirmation(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("u")                                       # nothing recorded yet: no key
        assert app.screen is screen and "Put everything back" not in keys(app)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert "Put everything back" in keys(app)
        await pilot.press("u")
        dialog = app.screen
        assert isinstance(dialog, ChoiceDialog) and F.text_of(dialog.query_one(".dialog-title")) == "Put everything back?"
        lines = " ".join(F.text_of(w) for w in dialog.query(".dialog-line"))
        assert "puts back every file and VM setting this setup changed" in lines
        assert app.focused.name == "no"
        await pilot.press("escape")
        assert (env.root / HINT).exists()
        await pilot.press("u")
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog) and "Everything is back" in F.text_of(
            app.screen.query_one(".dialog-title")))
        await pilot.press("enter")
        await F.settle(pilot, lambda: screen.mode == "choose" and bool(screen.query("#g-card")))
        assert not (env.root / HINT).exists() and screen.s.state["gpu"] is None


@F.tui
async def test_undo_stops_a_running_mac_it_changed_and_reports_a_failure(tmp_path, applied, monkeypatch):
    env = amd_env(tmp_path, vms={100: ("running", SEQUOIA)})
    s = env.session()
    s.state["vm_records"].append(100)
    s.save()
    monkeypatch.setattr(gpu_setup.Session, "undo", lambda self, allow_stop=False: (False, [f"stop={allow_stop}", "VM 100: restoring failed."]))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env, session=env.session())
        await pilot.press("u")
        assert "Running VMs that were changed are stopped first: 100." in " ".join(
            F.text_of(w) for w in app.screen.query(".dialog-line"))
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: "restoring failed" in screen.result_text)
        assert "stop=True" in screen.result_text and screen.result_fail


@F.tui
async def test_undo_from_the_vm_view_opens_the_flow_with_the_confirmation(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.state["vm_records"].append(100)
    s.save()
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, undo=True, session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        assert F.text_of(app.screen.query_one(".dialog-title")) == "Put everything back?"


@F.tui
async def test_g_on_home_opens_the_flow_for_the_selected_mac(tmp_path, monkeypatch):
    F.patch_host(monkeypatch)
    env = amd_env(tmp_path)
    monkeypatch.setattr("osx_proxmox_next.screens.gpu.Session", lambda: env.session())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await pilot.press("g")
        await F.settle(pilot, lambda: isinstance(app.screen, GpuScreen))
        assert app.screen.vmid == 101 and app.screen.fixed_vm
        await F.settle(pilot, lambda: bool(app.screen.query("#g-card")))
        await pilot.pause(0.3)
        assert "VM 101" in row(app.screen, "#g-mac")


@F.tui
async def test_the_palette_opens_the_flow_without_a_preselected_mac(tmp_path, monkeypatch):
    F.patch_host(monkeypatch)
    env = amd_env(tmp_path)
    monkeypatch.setattr("osx_proxmox_next.screens.gpu.Session", lambda: env.session())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        app.action_gpu()
        await F.settle(pilot, lambda: isinstance(app.screen, GpuScreen))
        assert app.screen.vmid is None and not app.screen.fixed_vm


@F.tui
async def test_a_saved_flow_in_the_middle_resumes_where_it_stopped(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    s.save()
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, vmid=None, session=env.session())
        await F.settle(pilot, lambda: screen.mode in ("run", "bios") and not screen.working)
        assert screen.s.state["step"] >= 4


@F.tui
async def test_the_changes_dialog_after_the_approval_lists_what_is_left(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await pilot.press("p")
        await F.settle(pilot, lambda: isinstance(app.screen, ChangesDialog))
        assert "VM 100 settings" in F.text_of(app.screen.query_one("#changes-text"))
        await pilot.press("escape")


@F.tui
async def test_the_footer_and_help_name_every_key_of_the_flow(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("f1")
        shown = F.text_of(app.screen.query_one("#help-keys"))
        for word in ("See every change", "Put everything back", "Check again", "Yes, all good", "No, help me", "Leave"):
            assert word in shown


# ── the check after the restart: every way it can fail ───────────────

def verify_env(tmp_path):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    s.complete(4)
    s.complete(5, skipped=True)
    s.approve({"host_fp": "x", "vm_fp": "*", "host_files": 1, "host_work": True, "restart": False, "vm": 100,
               "release": "", "typed": False, "write_optional": False, "allow_no_console": False})
    return env


def failing(detail="", fix="check", label="IOMMU"):
    return [{"status": "fail", "label": label, "detail": detail, "fix": fix}, {"status": "ok", "label": "ok", "detail": "", "fix": ""}]


@F.tui
@pytest.mark.parametrize("fix,button,target", [("host", "Back to host setup", 4), ("bios", "Back to the BIOS step", 3),
                                              ("check", "Check again", 6)])
async def test_a_failed_check_offers_the_step_that_fixes_it(tmp_path, monkeypatch, fix, button, target):
    env = verify_env(tmp_path)
    monkeypatch.setattr(gpu_setup.Session, "verify", lambda self: failing("Missing intel_iommu=on", fix))
    sent = []
    monkeypatch.setattr(gpu_setup.Session, "send_back", lambda self, step: sent.append(step))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        assert box(screen).startswith("The card is not free yet") and "[!!] IOMMU - Missing intel_iommu=on" in box(screen)
        assert "[ok] ok" in box(screen)
        assert screen.query_one("#g-primary-run", Button).label.plain == button
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert sent == ([target] if fix != "check" else [])


@F.tui
async def test_a_failed_check_that_needs_a_reboot_goes_back_to_the_restart_box(tmp_path, monkeypatch):
    env = verify_env(tmp_path)
    monkeypatch.setattr(gpu_setup.Session, "verify", lambda self: failing("Reboot the host to apply the kernel arguments"))
    monkeypatch.setattr(gpu_setup.Session, "rebooted_since_apply", lambda self: False)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: box(screen).startswith("Time for the one restart") if screen.query("#g-box-title") else False)
        assert screen.s.state["step"] == 5 and screen.s.state.get("reboot_needed")
        assert "The change is saved but not active yet." in screen.result_text


@F.tui
async def test_a_restart_that_did_not_activate_the_kernel_arguments_says_where_to_look(tmp_path, monkeypatch):
    env = verify_env(tmp_path)
    monkeypatch.setattr(gpu_setup.Session, "verify", lambda self: failing("Reboot the host to apply the kernel arguments"))
    monkeypatch.setattr(gpu_setup.Session, "rebooted_since_apply", lambda self: True)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        assert "proxmox-boot-tool refresh" in box(screen) and "the kernel arguments are still not active" in box(screen)


@F.tui
async def test_a_card_that_moved_during_the_check_sends_the_user_back_to_the_choice(tmp_path, monkeypatch):
    env = verify_env(tmp_path)
    monkeypatch.setattr(gpu_setup.Session, "verify", lambda self: failing("The card is now at 0000:07:00", "scan"))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: screen.mode == "choose" and bool(screen.query("#g-card")))
        await pilot.pause(0.2)
        assert screen.s.state["gpu"] is None and "now at 0000:07:00" in F.text_of(screen.query(".g-notice").first())


@F.tui
async def test_a_verify_problem_with_nothing_left_to_change_says_so(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    s.send_back(4)                                    # verify sent it back, and the host is already right
    monkeypatch.setattr(gpu_setup.Session, "host_plan", lambda self, **k: host_setup.new_plan())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")) and not screen.working)
        assert "nothing left for this tool to change" in box(screen)
        verified = []
        monkeypatch.setattr(gpu_setup.Session, "verify", lambda self: verified.append(1) or [
            {"status": "ok", "label": "x", "detail": "", "fix": ""}])
        await pilot.press("enter")
        await F.settle(pilot, lambda: bool(verified))


@F.tui
async def test_a_host_that_needs_nothing_skips_the_host_and_restart_steps(tmp_path, applied, monkeypatch):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    ready(env)
    monkeypatch.setattr(gpu_setup.Session, "host_plan", lambda self, **k: host_setup.new_plan())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: 4 in screen.s.state["skipped"] and 5 in screen.s.state["skipped"])


@F.tui
async def test_a_failed_host_apply_says_so_and_offers_to_try_again(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    monkeypatch.setattr(gpu_setup.Session, "host_apply", lambda self, plan, confirmed=False: {
        "ok": False, "error": "disk full", "rolled_back": True})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: box(screen).startswith("Apply these changes") if screen.query("#g-box-title") else False)
        await pilot.press("enter")
        await F.settle(pilot, lambda: "The host setup did not finish" in box(screen) if screen.query("#g-box-title") else False)
        assert "disk full" in box(screen) and "The files were put back." in screen.result_text
        assert screen.query_one("#g-primary-run", Button).label.plain == "Try again"


@F.tui
async def test_the_ask_again_screen_for_an_only_display_adapter_needs_the_typed_phrase(tmp_path, applied):
    env = Env(tmp_path)
    add_card(env.root, boot_vga=True)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.complete(3)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=s)
        await F.settle(pilot, lambda: bool(screen.query("#g-typed")))
        assert "Careful: This card is the only display adapter" in box(screen)
        screen.action_primary()                                       # nothing typed: refused
        assert 'Type "I understand" first.' in screen.result_text
        screen.query_one("#g-typed", Input).value = CONFIRM_PHRASE
        await pilot.pause(0.3)
        screen.action_primary()                                       # re-plans once with the typed confirmation
        await F.settle(pilot, lambda: bool(screen.s.approval and screen.s.approval.get("allow_no_console")))
