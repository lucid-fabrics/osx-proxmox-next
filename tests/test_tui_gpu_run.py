"""The graphics card flow, moments 2 and 3: the run, the one restart, the attach, the first boot, undo."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.widgets import Button, Input, Static

import tui_fakes as F
from gpu_tui_fakes import (  # noqa: F401 - the underscore names are autouse fixtures
    HINT, SIZE, _no_network, _vm_list, amd_env, applied, approved, at_choose, box, dialog_text, flat, host_card, keys,
    mode, nvidia_card, reboot_host, row, start, timeline)
from osx_proxmox_next import gpu_setup, host_setup, nullmoth
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.gpu_setup import CONFIRM_PHRASE
from osx_proxmox_next.screens import gpu_restart
from osx_proxmox_next.screens.base import ChoiceDialog
from osx_proxmox_next.screens.gpu import ChangesDialog, GpuScreen
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.widgets.rows import ChoiceRow
from test_gpu_setup import SEQUOIA, Env, ready


async def to_restart_box(pilot, app, env):
    screen = await at_choose(pilot, app, env)
    await pilot.press("enter")
    await mode(pilot, screen, "run")
    return screen


def at_attach(env, vmid=100, display="gpu", bdf="06:00", **over):
    """A flow that is past the host and the restart: the card is bound and only the attach is left."""
    s = env.session()
    record = approved(s, bdf, vmid, display, **over)
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    ready(env)
    reboot_host(env)
    return record


# ── moment 2: the run and the one restart ────────────────────────────

@F.tui
async def test_approving_stores_one_approval_and_runs_the_host_setup(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_restart_box(pilot, app, env)
        s = screen.s
        assert s.state["gpu"]["bdf"] == "0000:06:00" and s.state["vm"] == 100
        approval = s.approval
        assert approval["restart"] and approval["host_work"] and approval["host_files"] == 3 and approval["vm"] == 100
        assert approval["host_fp"] and approval["vm_fp"]
        assert 4 in s.state["done"] and s.state.get("reboot_needed") and (env.root / HINT).exists()
        text = box(screen)
        assert text.startswith("Time for the one restart") and "What a restart means right now" in text
        assert "The host needs it to hand the card over.\nAbout 3 minutes." in text
        assert "run: osx-next-cli" not in text                        # the Home reminder resumes it, no command to type
        tl = timeline(screen)
        assert "✓ Nothing to back up (3 new files)" in tl and "✓ Set up the host" in tl and "◐ Restart this host" in tl
        assert "your turn" in tl and "· Check the card is free" in tl and "· Attach it to macos-sequoia" in tl
        assert "· Start the Mac" in tl and "The last three run by themselves\nwhen you open osx-next again." in tl
        for line in tl.splitlines()[:2]:
            assert len(line) == 40 and line[-3] == ":"                 # a duration, flush right
        note = F.text_of(screen.query_one("#g-run-note"))
        assert note == "✓ Safe to leave here. Choose Later and your Macs page shows a reminder to finish."
        assert "Backups are in" not in note and host_setup.BACKUP_ROOT not in note
        assert F.text_of(screen.query_one("#g-run-title")) == "Setting up the Radeon RX 6600 for macos-sequoia"
        assert applied == []                                         # the VM is not touched before the restart
        for key in ("Restart now", "See every change", "Put everything back", "Later"):
            assert key in keys(app)
        assert screen.query_one("#g-box").has_class("warn") is False


@F.tui
async def test_the_backup_folder_is_in_the_p_dialog_not_on_the_screen(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_restart_box(pilot, app, env)
        await pilot.press("p")
        await F.settle(pilot, lambda: isinstance(app.screen, ChangesDialog))
        text = F.text_of(app.screen.query_one("#changes-text"))
        assert "Backups of this setup: " + screen.s.state["backup_dir"] in text
        assert "VM 100 settings" in text and "Address: 0000:06:00" in text


@F.tui
async def test_the_restart_box_is_the_one_confirmation(tmp_path, applied, monkeypatch):
    """Fix 4: Restart now has the focus, enter restarts and nothing opens after it."""
    clock = [100.0]
    monkeypatch.setattr(gpu_restart, "_now", lambda: clock[0])      # the guard runs on this clock, no sleeping
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_restart_box(pilot, app, env)
        buttons = [b.id for b in screen.query("#g-box-buttons Button")]
        assert buttons == ["g-later", "g-restart"]                    # the primary button on the right
        assert app.focused.id == "g-restart"
        assert keys(app).startswith(" enter Restart now   p See every change") and "esc Later" in keys(app)
        await F.settle(pilot, lambda: screen._primary_action is not None)   # the box is drawn and armed
        await pilot.press("enter")                                    # typed ahead: the first second is ignored
        await pilot.pause()
        assert not env.run.ran("systemctl", "reboot") and app.screen is screen
        clock[0] += 2.0                                               # a second has passed
        await F.settle(pilot, lambda: not screen.query_one("#g-restart").has_class("-active"))   # button press effect
        await pilot.press("enter")
        await F.settle(pilot, lambda: env.run.ran("systemctl", "reboot"))
        assert app.screen is screen and "Restarting..." in screen.result_text   # no second dialog
        await pilot.press("enter")                                    # a second enter does not restart twice
        await pilot.pause()
        assert [c for c in env.run.calls if c[:2] == ["systemctl", "reboot"]] == [["systemctl", "reboot"]]
        await pilot.click("#g-later")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        assert any("reminder" in n.message for n in app._notifications)


@F.tui
async def test_the_restart_box_warns_about_an_install_in_progress(tmp_path, applied, monkeypatch):
    env = amd_env(tmp_path, vms={100: ("running", SEQUOIA), 107: ("running", SEQUOIA)})
    monkeypatch.setattr("osx_proxmox_next.screens.gpu_restart.read_driver_state",
                        lambda vmid: F.installing_driver(18) if vmid == 107 else F.DriverState())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_restart_box(pilot, app, env)
        text = box(screen)
        assert "● vm100 (VM 100) is shut down cleanly first" in text
        assert "! vm107 (VM 107) is still installing, 18 min in" in text and "A restart now interrupts that install." in text


@F.tui
async def test_after_the_restart_the_check_and_the_attach_run_without_asking(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_restart_box(pilot, app, env)
        approval = dict(screen.s.approval)
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        reboot_host(env)                                             # the host restarts; the user opens the flow again
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
        assert isinstance(screen2.durations["start"], float)         # written on the UI thread, from the worker's result
        tl = timeline(screen2)
        assert "✓ Checked the card is free" in tl and "✓ Attached it to macos-sequoia" in tl
        assert "✓ Started the Mac" in tl and "Finished without a question" in tl
        for line in tl.splitlines()[:6]:                            # every finished step has its duration
            assert len(line) == 40 and line[-3] == ":"
        assert F.text_of(screen2.query_one("#g-run-title")) == "macos-sequoia is starting on the Radeon RX 6600"
        for key in ("Yes, all good", "No, help me", "Put everything back", "Your Macs"):
            assert key in keys(app)


@F.tui
async def test_the_first_boot_box_says_what_the_design_says(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    approved(s)
    for step in (3, 4, 5, 6, 7):
        s.complete(step, skipped=step in (4, 5))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await mode(pilot, screen, "first_boot")
        text = box(screen)
        assert text.startswith("Look at the monitor plugged into the card\nThe first start takes up to 2 minutes.")
        assert "This Mac's screen now lives on the card, not in Proxmox." in text
        assert text.endswith("Do you see the macOS desktop?")
        assert "Start the VM. You should see the OpenCore picker" not in text
        assert [b.id for b in screen.query("#g-box-buttons Button")] == ["g-no", "g-yes"]
        assert screen.query_one("#g-box").has_class("warn") is False


@F.tui
async def test_a_changed_vm_after_the_restart_is_shown_and_asked_again(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await to_restart_box(pilot, app, env)
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        reboot_host(env)
        s2 = env.session()
        s2.state["approved"]["vm_fp"] = "something else"              # the VM settings differ from what was approved
        screen2 = GpuScreen(session=s2)
        app.push_screen(screen2)
        await mode(pilot, screen2, "run")
        assert box(screen2).startswith("This differs from what you approved")
        assert "This is what will change on VM 100" in box(screen2) and "Attach GPU" in box(screen2)
        assert applied == [] and screen2.query_one("#g-box").has_class("warn")
        assert screen2.query_one("#g-primary-run", Button).label.plain == "Attach the card now"
        await pilot.press("enter")
        await mode(pilot, screen2, "first_boot")
        assert len(applied) == 1


@F.tui
async def test_a_host_plan_that_changed_since_the_approval_is_asked_again(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    approved(s, host_fp="stale")
    s.complete(3)
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
async def test_a_stale_i_understand_is_not_carried_into_a_different_plan(tmp_path, applied):
    """S6: the phrase typed for plan A never covers plan B."""
    env = amd_env(tmp_path)
    s = env.session()
    approved(s, host_fp="stale", typed=True, allow_no_console=True)
    s.complete(3)
    (env.root / "sys/bus/pci/devices/0000:06:00.0/boot_vga").write_text("1\n")   # now the host's only display adapter
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=s)
        await F.settle(pilot, lambda: bool(screen.query("#g-typed")))
        assert "Careful: This card is the only display adapter" in box(screen)
        screen.action_primary()                                       # nothing typed for this plan: refused
        assert f'Type "{CONFIRM_PHRASE}" first.' in screen.result_text and 4 not in screen.s.state["done"]
        screen.query_one("#g-typed", Input).value = CONFIRM_PHRASE
        await pilot.pause(0.3)
        screen.action_primary()                                       # plans again with the phrase typed just now
        await F.settle(pilot, lambda: bool(screen.s.approval and screen.s.approval.get("allow_no_console")) and not screen.working)
        await pilot.pause(0.3)
        assert 4 not in screen.s.state["done"]                        # the plan it will run is shown first
        screen.action_primary()
        await mode(pilot, screen, "run")
        assert 4 in screen.s.state["done"]


@F.tui
async def test_the_ask_again_screen_for_an_only_display_adapter_needs_the_typed_phrase(tmp_path, applied):
    env = Env(tmp_path)
    host_card(env.root, boot_vga=True)
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
        screen.action_primary()                                       # plans once more with the typed confirmation
        await F.settle(pilot, lambda: bool(screen.s.approval and screen.s.approval.get("allow_no_console")))


# ── the attach ───────────────────────────────────────────────────────

@F.tui
async def test_a_running_mac_is_stopped_only_after_a_confirmation(tmp_path, applied):
    env = amd_env(tmp_path, vms={100: ("running", SEQUOIA)})
    at_attach(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        dialog = app.screen
        assert F.text_of(dialog.query_one(".dialog-title")) == "Stop macos-sequoia first?"
        assert "closed without saving" in dialog_text(dialog)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert applied == [] and app.screen is screen
        screen.drive()                                                # look again: the question comes back
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        await pilot.press("tab")
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        assert len(applied) == 1 and any(st.title.lower().startswith("stop") for st in applied[0])


@F.tui
async def test_a_running_mac_with_an_nvidia_card_is_stopped_before_the_driver_setup(tmp_path, monkeypatch):
    """S1: the user confirms "Stop it", so it is stopped before the NVIDIA setup, which refuses a running Mac."""
    env = Env(tmp_path, vms={100: ("running", SEQUOIA)})
    nvidia_card(env.root)
    order: list[str] = []
    original = env.qm.run

    def run(argv):
        if argv[:2] == ["qm", "stop"]:
            order.append("stop")
            env.qm.vms[100] = ("stopped", SEQUOIA)
        return original(argv)

    env.qm.run = run

    def finish(self, choice, on_event):
        order.append("finish")
        assert self.pve.qm("status", "100").output == "status: stopped"
        return SimpleNamespace(verified=True, version="2.9", kexts=7, ip="", display="yes", notes=[], detail="",
                               result="installed")

    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", finish)
    monkeypatch.setattr("osx_proxmox_next.gpu_flow.time.sleep", lambda _s: None)
    at_attach(env, bdf="01:00", display="console")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        await pilot.press("tab")
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        assert order == ["stop", "finish"]


@F.tui
async def test_the_vm_changed_while_the_stop_question_was_open_is_shown_again_not_written(tmp_path, applied):
    """S2: what is written is what was shown. A Mac edited meanwhile goes back to the ask screen."""
    env = amd_env(tmp_path, vms={100: ("running", SEQUOIA)})
    at_attach(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        env.qm.vms[100] = ("running", SEQUOIA.replace("-cpu host", "-cpu host -device edited"))  # edited in the web UI
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")) and not screen.working)
        assert applied == []                                          # nothing was written
        assert box(screen).startswith("This differs from what you approved")
        assert "-device edited" not in " ".join(" ".join(s_.argv) for s_ in applied)


@F.tui
async def test_the_attach_is_refused_with_the_reason_and_the_user_can_pick_another_mac(tmp_path, applied):
    env = amd_env(tmp_path)
    at_attach(env)
    env.qm.vms[100] = ("stopped", "name: plain\n")                   # no longer a macOS VM made by this tool
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-secondary")))
        assert box(screen).startswith("The Mac cannot take the card yet") and "Refused:" in box(screen)
        assert [b.id for b in screen.query("#g-box-buttons Button")] == ["g-secondary", "g-primary-run"]
        assert screen.query_one("#g-secondary", Button).label.plain == "Pick another Mac"
        await F.settle(pilot, lambda: not screen.working and screen._secondary_action is not None)   # the click needs both
        await pilot.click("#g-secondary")
        await F.settle(pilot, lambda: bool(screen.query("#g-pick")))
        assert screen.s.state["vm"] is None and applied == []


# ── decide later ─────────────────────────────────────────────────────

def decided_later(env):
    s = env.session()
    s.choose("06:00", None, "gpu")
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    reboot_host(env)


@F.tui
async def test_decide_later_shows_the_steps_of_the_highlighted_mac_and_approves_exactly_those(tmp_path, applied):
    """S3: the pick screen lists the steps and stores the real fingerprint, never a wildcard."""
    env = amd_env(tmp_path, vms={100: ("stopped", SEQUOIA), 102: ("stopped", SEQUOIA.replace("sequoia", "sonoma"))})
    decided_later(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, vmid=None, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-pick")) and screen.picked_plan is not None)
        assert F.text_of(screen.query_one("#g-run-title")) == "Pick the Mac that gets the card"
        info = F.text_of(screen.query_one("#g-pick-info"))
        assert "This is what will change on VM 100 (a config snapshot is saved first):" in info and "Attach GPU" in info
        assert not screen.query_one("#g-pick-go", Button).disabled
        screen.query_one("#g-pick", ChoiceRow).set_value("102", notify=True)
        await F.settle(pilot, lambda: screen.picked_plan is not None and screen.picked_plan[0] == 102)
        assert "VM 102" in F.text_of(screen.query_one("#g-pick-info"))
        screen.query_one("#g-pick", ChoiceRow).set_value("100", notify=True)
        await F.settle(pilot, lambda: screen.picked_plan is not None and screen.picked_plan[0] == 100)
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        fp = screen.s.approval["vm_fp"]
        assert fp != "*" and len(fp) == 40 and screen.s.state["vm"] == 100 and len(applied) == 1


@F.tui
async def test_decide_later_approval_does_not_cover_a_vm_that_changes_afterwards(tmp_path, applied):
    env = amd_env(tmp_path)
    decided_later(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, vmid=None, session=env.session())
        await F.settle(pilot, lambda: screen.picked_plan is not None)
        screen.query_one("#g-pick-go", Button).disabled = False
        screen.s.state["vm"] = None
        screen._pick_attach()
        await mode(pilot, screen, "first_boot")
        assert len(applied) == 1
        # the same record, later, on a VM that was edited since: it must ask again
        s2 = env.session()
        s2.state["finished"] = False
        s2.goto(7)
        env.qm.vms[100] = ("stopped", SEQUOIA.replace("-cpu host", "-cpu host -device edited"))
        app.pop_screen()
        screen3 = GpuScreen(session=s2)
        app.push_screen(screen3)
        await F.settle(pilot, lambda: bool(screen3.query("#g-primary-run")))
        assert box(screen3).startswith("This differs from what you approved")


@F.tui
async def test_picking_a_mac_needs_a_choice(tmp_path):
    env = amd_env(tmp_path, vms={100: ("stopped", SEQUOIA.replace("sequoia", "sonoma").replace("isa-applesmc", "x"))})
    decided_later(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, vmid=None, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-pick")) and not screen.working)
        assert screen.query_one("#g-pick-go", Button).disabled
        screen._pick_attach()
        assert "Pick a Mac first." in screen.result_text and screen.result_fail


@F.tui
async def test_decide_later_ends_after_the_check_with_a_prompt_to_pick_a_mac(tmp_path, applied):
    env = amd_env(tmp_path)
    decided_later(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, vmid=None, session=env.session())
        await F.settle(pilot, lambda: bool(screen.query("#g-pick")) and screen.picked_plan is not None)
        assert screen.query_one("#g-pick", ChoiceRow).value == "100"
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        assert screen.s.state["vm"] == 100 and len(applied) == 1


# ── NVIDIA ───────────────────────────────────────────────────────────

@F.tui
async def test_nvidia_attach_runs_the_whole_setup_with_the_chosen_release(tmp_path, monkeypatch):
    env = Env(tmp_path)
    nvidia_card(env.root)
    seen = {}

    def finish(self, choice, on_event):
        seen["choice"] = choice
        on_event("downloading")
        return SimpleNamespace(verified=True, version="2.9", kexts=7, ip="10.0.0.9", display="no", notes=["a note"],
                               detail="", result="installed")

    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", finish)
    at_attach(env, bdf="01:00", display="console", release="v2.9")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await mode(pilot, screen, "first_boot")
        assert seen["choice"] == "v2.9"
        text = box(screen)
        assert "NullMoth 2.9 is installed and loaded (7 extensions)." in text and "ssh <user>@10.0.0.9" in text
        assert "vnc://10.0.0.9" in text and nullmoth.DISPLAY_LINE in text and "Note: a note" in text
        assert text.startswith("Your Mac is ready") and "Do you see the macOS desktop?" not in text
        assert not screen.query("#g-no") and "No, help me" not in keys(app)
        await pilot.press("n")                                       # no troubleshooting branch for NVIDIA
        assert screen.mode == "first_boot"
        await pilot.press("enter")
        await mode(pilot, screen, "finished")


@F.tui
async def test_a_failed_nvidia_setup_stops_with_the_reason(tmp_path, monkeypatch):
    env = Env(tmp_path)
    nvidia_card(env.root)
    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", lambda self, choice, on_event: SimpleNamespace(
        verified=False, version="", kexts=0, ip="", display="", notes=[], detail="the guest said no", result=""))
    at_attach(env, bdf="01:00", display="console")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=env.session())
        await F.settle(pilot, lambda: box(screen).startswith("The driver is not confirmed"),
                       what="the failed-driver box")
        assert "the guest said no" in box(screen) and nullmoth.FAILED_NOTE in box(screen)
        assert screen.s.state["step"] == 7 and screen.query_one("#g-box").has_class("fail")


# ── the check after the restart: every way it can fail ───────────────

def verify_env(tmp_path):
    env = amd_env(tmp_path)
    s = env.session()
    approved(s, restart=False, host_work=True)
    s.complete(3)
    s.complete(4)
    s.complete(5, skipped=True)
    return env


def failing(detail="", fix="check", label="IOMMU"):
    return [{"status": "fail", "label": label, "detail": detail, "fix": fix},
            {"status": "ok", "label": "ok", "detail": "", "fix": ""}]


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
        assert box(screen).startswith("The card is not free yet")
        assert "✗ IOMMU - Missing intel_iommu=on" in box(screen) and "✓ ok" in box(screen)
        assert "[!!]" not in box(screen) and "[ok]" not in box(screen)
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
        await F.settle(pilot, lambda: bool(screen.query("#g-box-title")) and box(screen).startswith("Time for the one restart"))
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
        await F.settle(pilot, lambda: bool(screen.query("#g-box-title")) and box(screen).startswith("Apply these changes"))
        await pilot.press("enter")
        await F.settle(pilot, lambda: bool(screen.query("#g-box-title")) and "The host setup did not finish" in box(screen))
        assert "disk full" in box(screen) and "The files were put back." in screen.result_text
        assert screen.query_one("#g-primary-run", Button).label.plain == "Try again"


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
        await F.settle(pilot, lambda: screen.mode == "run" and not screen.working)
        assert screen.s.state["step"] >= 4


# ── moment 3 ─────────────────────────────────────────────────────────

async def at_first_boot(pilot, app, env, bdf="06:00"):
    s = env.session()
    s.choose(bdf, 100, "gpu", ack=True)
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
        assert "Use an HDMI cable" in box(screen) and box(screen).startswith("No signal? Try these")
        assert screen.query_one("#g-back", Button) and screen.query_one("#g-yes", Button)
        await pilot.click("#g-back")
        await mode(pilot, screen, "first_boot")
        assert "Do you see the macOS desktop?" in box(screen)
        await pilot.press("y")
        await mode(pilot, screen, "finished")
        assert F.text_of(screen.query_one("#g-run-title")) == "Setup finished"
        assert "Setup summary" in F.text_of(screen.query_one("#g-box-text")) and "Card: " in F.text_of(screen.query_one("#g-box-text"))
        assert not (env.root / HINT).exists() and screen.s.state["finished"]
        await pilot.press("n")                                       # no effect outside the first boot
        await pilot.press("enter")                                   # start a new setup
        await F.settle(pilot, lambda: screen.mode == "choose" and bool(screen.query("#g-card")))
        assert not screen.s.state["finished"]


@F.tui
async def test_the_no_signal_help_fits_100x30_and_names_the_current_screens(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=(100, 30)) as pilot:
        screen = await at_first_boot(pilot, app, env)
        await pilot.press("n")
        await F.settle(pilot, lambda: screen.helping and bool(screen.query("#g-back")))
        await pilot.pause(0.3)
        text = F.text_of(screen.query_one("#g-box-text"))
        assert len(text.splitlines()) <= 12
        assert F.text_of(screen.query_one("#g-more")) == ""            # no "more lines" hint: nothing to scroll
        assert screen.query_one("#g-box-scroll").max_scroll_y == 0
        for current in ("HDMI", "Your Macs", "Press u", "press g", "Keep the Proxmox console"):
            assert current in " ".join(text.split())
        assert "gpu-setup" not in text and "Monitor on the GPU" not in text
        assert screen.focused is screen.query_one("#g-back", Button)
        assert str(screen.query_one("#g-yes", Button).label) == "It works now"
        assert [(k, label) for k, label, _ in screen.footer_items()] == \
            [("↑↓", "Scroll"), ("enter", "Back"), ("esc", "Back")]


@F.tui
async def test_enter_and_esc_on_the_no_signal_help_go_back_without_recording_success(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=(100, 30)) as pilot:
        screen = await at_first_boot(pilot, app, env)
        for key in ("enter", "escape"):
            await pilot.press("n")
            await F.settle(pilot, lambda: screen.helping and bool(screen.query("#g-back")))
            await pilot.press(key)
            await F.settle(pilot, lambda: not screen.helping and bool(screen.query("#g-no")))
            assert "Do you see the macOS desktop?" in box(screen)
            assert screen.mode == "first_boot" and not screen.s.state["finished"]
            assert app.screen is screen
        await pilot.press("n")
        await F.settle(pilot, lambda: screen.helping and bool(screen.query("#g-back")))
        await pilot.click("#g-yes")                                    # "It works now" is the one that records it
        await mode(pilot, screen, "finished")
        assert screen.s.state["finished"]


def test_no_screen_or_widget_text_sends_the_user_to_the_old_flow():
    import pathlib
    import re
    import osx_proxmox_next
    root = pathlib.Path(osx_proxmox_next.__file__).parent
    bad = re.compile(r"gpu-setup again|Monitor on the GPU|\bwizard\b|\bstep 2\b|\bStep 4\b")
    hits = []
    for folder in ("screens", "widgets"):
        for path in (root / folder).glob("*.py"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("#", "from ", "import ")) or "wizard_state" in line:
                    continue
                if bad.search(line):
                    hits.append(f"{path.name}:{n}: {stripped}")
    assert not hits, hits


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


# ── u: put everything back ───────────────────────────────────────────

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
        assert "Remove /etc/modprobe.d/osx-proxmox-next-vfio.conf" in dialog_text(dialog)
        assert "Anything you edited since is kept and reported." in dialog_text(dialog)
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
    monkeypatch.setattr(gpu_setup.Session, "undo", lambda self, allow_stop=False, progress=None: (False, [f"stop={allow_stop}", "VM 100: restoring failed."]))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env, session=env.session())
        await pilot.press("u")
        await F.settle(pilot, lambda: "Running VMs that were changed are stopped first: 100." in dialog_text(app.screen),
                       what="the undo dialog text")
        assert "Running VMs that were changed are stopped first: 100." in dialog_text(app.screen)
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: "restoring failed" in screen.result_text)
        assert "stop=True" in screen.result_text and screen.result_fail


@F.tui
async def test_undo_from_the_vm_view_opens_the_question_even_while_the_name_is_read(tmp_path, applied, monkeypatch):
    """N3: a slow `qm config` for the title must not swallow the undo question."""
    env = amd_env(tmp_path)
    s = env.session()
    s.choose("06:00", 100, "gpu")
    s.state["vm_records"].append(100)
    s.save()
    import time as _time

    def slow_info(vmid, adapter=None):
        _time.sleep(0.6)
        return SimpleNamespace(name="macos-sequoia", status="stopped", config_raw=SEQUOIA)

    monkeypatch.setattr("osx_proxmox_next.screens.gpu.fetch_vm_info", slow_info)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await start(pilot, app, env, undo=True, session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog))
        assert F.text_of(app.screen.query_one(".dialog-title")) == "Put everything back?"


@F.tui
async def test_the_changes_dialog_after_the_approval_lists_what_is_left(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await to_restart_box(pilot, app, env)
        await pilot.press("p")
        await F.settle(pilot, lambda: isinstance(app.screen, ChangesDialog))
        assert "VM 100 settings" in F.text_of(app.screen.query_one("#changes-text"))
        await pilot.press("escape")


@F.tui
async def test_the_row_keys_move_between_the_rows_of_the_form(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        screen.query_one("#g-card").focus()
        await pilot.press("down")
        assert app.focused.id == "g-mac"
        await pilot.press("down")
        assert app.focused.id == "g-display"
        await pilot.press("up", "up")
        assert app.focused.id == "g-card"
        assert row(screen, "#g-card").startswith(" Card ")
        assert flat(row(screen, "#g-card")).startswith("Card Radeon RX 6600")
