"""What the first run on real hardware found: another VM takes the card at boot, one approval, one restart box,
true step ticks, the run screen at once, honest file words, the undo list, the readable plan and the notes."""
from __future__ import annotations

import threading

import tui_fakes as F
from gpu_tui_fakes import (  # noqa: F401 - the underscore names are autouse fixtures
    SIZE, _no_network, _vm_list, amd_env, applied, approved, at_choose, box, dialog_text, flat, host_card, keys,
    mode, reboot_host, row, start, timeline)
from osx_proxmox_next import gpu_setup, host_setup
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.infrastructure import CommandResult
from osx_proxmox_next.screens.base import ChoiceDialog
from osx_proxmox_next.screens.gpu import ChangesDialog, GpuScreen
from osx_proxmox_next.screens.gpu_before import BeforeDialog
from osx_proxmox_next.screens.home import HomeScreen
from test_gpu_boot_holders import CARD, ONE, SEVERAL, NamedQm
from test_gpu_setup import SEQUOIA, ready

STARTS = "! chimeraos (VM 103) starts by itself afterwards and takes the card. Shut it down, then open osx-next."


def holders_env(tmp_path, **vms):
    """A host where VMs 103 and 106 hold the card and start with the host, as on the hardware that found this."""
    env = amd_env(tmp_path)
    env.qm = NamedQm({100: ("stopped", SEQUOIA), 103: ("stopped", CARD + "onboot: 1\n"),
                      106: ("stopped", CARD + "onboot: 1\n"), **vms})
    return env


async def to_run(pilot, app, env, **kw):
    """Choose the card and set it up: the one approval ("Set it up" in Before you continue)."""
    screen = await at_choose(pilot, app, env, **kw)
    await pilot.press("enter")
    assert isinstance(app.screen, BeforeDialog)
    await pilot.press("enter")
    await mode(pilot, screen, "run")
    return screen


# ── 1: another VM that starts with the host ──────────────────────────

@F.tui
async def test_the_card_block_warns_about_vms_that_start_with_the_host_and_notes_other_cards_last(tmp_path):
    env = holders_env(tmp_path)
    host_card(env.root, bdf="0000:41:00", device=0x73C0, audio=0xAB29, group=9)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        card = flat(row(screen, "#g-card"))
        assert gpu_setup.gpu.shared_note([103, 106]) in card and SEVERAL in card
        assert card.endswith("Not offered: AMD GPU (no Apple driver)")      # the other device: one muted line, last
        assert card.index("Works in macOS") < card.index(SEVERAL) < card.index("Not offered")
        await pilot.press("enter")
        await F.settle_query(pilot, app, "#before-warnings", "the before dialog")
        warnings = flat(F.text_of(app.screen.query_one("#before-warnings")))
        assert SEVERAL in warnings and gpu_setup.gpu.shared_note([103, 106]) in warnings


@F.tui
async def test_one_vm_that_starts_with_the_host_gets_the_singular_wording(tmp_path):
    env = holders_env(tmp_path)
    del env.qm.vms[106]
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert ONE in flat(row(screen, "#g-card"))


@F.tui
async def test_the_restart_box_lists_every_running_vm_and_each_vm_that_takes_the_card(tmp_path, applied):
    env = holders_env(tmp_path)
    env.qm.vms[107] = ("running", "cores: 2\n")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await to_run(pilot, app, env)
        text = box(screen)
        assert "● vm107 (VM 107) is shut down cleanly first" in text
        assert STARTS in text and "! bazzite (VM 106) starts by itself afterwards and takes the card." in text
        assert text.count("starts by itself afterwards") == 2               # one line per such VM
        assert app.focused.id == "g-restart"


@F.tui
async def test_the_shared_card_approval_is_recorded_when_before_you_continue_is_confirmed(tmp_path, applied, monkeypatch):
    env = holders_env(tmp_path)
    chosen = []
    original = gpu_setup.Session.choose
    monkeypatch.setattr(gpu_setup.Session, "choose", lambda self, *a, **k: chosen.append((a, k)) or original(self, *a, **k))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        assert isinstance(app.screen, BeforeDialog) and not chosen          # nothing is recorded before the approval
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        (args, kwargs), = chosen
        assert args[4] is True and sorted(kwargs["confirmed"]) == [103, 106]  # share_ok, and the VMs the dialog listed
        assert screen.s.state["gpu"]["bdf"] == "0000:06:00"


async def freed_after_restart(pilot, app, env):
    """The flow after the restart: the host is ready, VM 103 runs and has the card."""
    s = env.session()
    approved(s)
    for step in (3, 4, 5):
        s.complete(step, skipped=step in (4, 5))
    ready(env)
    reboot_host(env)
    env.qm.vms[103] = ("running", CARD + "onboot: 1\n")
    screen = GpuScreen(session=env.session())
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    app.push_screen(screen)
    await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")) and app.focused is not None)
    return screen


@F.tui
async def test_after_the_restart_a_running_vm_with_the_card_is_named_and_the_step_shows_a_cross(tmp_path, applied):
    env = holders_env(tmp_path)
    for vmid in (103, 106):                                                   # the approval was given on a quiet host
        del env.qm.vms[vmid]
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await freed_after_restart(pilot, app, env)
        text = box(screen)
        assert text.startswith("VM 103 is using the card")
        assert "chimeraos (VM 103) is running with this card. Shut it down, then press enter." in text
        assert "It starts with this host. Turn off its Start at boot option to stop it taking the card after every " \
               "restart." in flat(text)
        tl = timeline(screen)
        assert "✗ Card is in use by VM 103" in tl and "Checked the card is free" not in tl and "✓ Checked" not in tl
        assert "· Attach it to macos-sequoia" in tl and "· Start the Mac" in tl
        assert [str(b.label) for b in screen.query("#g-box-buttons Button")] == ["Leave", "Check again"]
        assert app.focused.id == "g-primary-run"                              # no "Pick another Mac": it is the card
        assert "Pick another Mac" not in text and "enter Check again" in keys(app) and "esc Leave" in keys(app)
        assert not env.qm.calls or not any(c[:2] == ["qm", "stop"] for c in env.qm.calls)   # nobody else is touched
        env.qm.vms[103] = ("stopped", CARD + "onboot: 1\n")                  # the user shuts it down, then enter
        await pilot.press("enter")
        await mode(pilot, screen, "first_boot")
        assert "✓ Checked the card is free" in timeline(screen) and "✗" not in timeline(screen)


@F.tui
async def test_a_vm_without_start_at_boot_gets_no_quiet_line_and_several_are_named(tmp_path, applied):
    env = holders_env(tmp_path)
    for vmid in (103, 106):
        del env.qm.vms[vmid]
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        s = env.session()
        approved(s)
        for step in (3, 4, 5):
            s.complete(step, skipped=step in (4, 5))
        ready(env)
        reboot_host(env)
        env.qm.vms[103] = ("running", CARD)
        env.qm.vms[106] = ("running", CARD + "onboot: 1\n")
        screen = GpuScreen(session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        app.push_screen(screen)
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        text = flat(box(screen))
        assert text.startswith("VMs 103, 106 are using the card")
        assert "Shut them down, then press enter." in text and "VM 106 starts with this host." in text
        assert "VM 103 starts" not in text
        assert "✗ Card is in use by VMs 103, 106" in timeline(screen)


@F.tui
async def test_a_vm_that_takes_the_card_between_the_check_and_the_attach_goes_back_to_the_check(tmp_path, applied,
                                                                                               monkeypatch):
    env = holders_env(tmp_path)
    for vmid in (103, 106):
        del env.qm.vms[vmid]
    s = env.session()
    approved(s)
    for step in (3, 4, 5):
        s.complete(step, skipped=step in (4, 5))
    ready(env)
    reboot_host(env)
    busy, asked = gpu_setup.gpu.Holder(103, "chimeraos", True, False), []
    # free while the check runs, taken a moment later: the attach looks again right before it writes
    monkeypatch.setattr(gpu_setup.Session, "card_holders", lambda self: asked.append(1) or ([] if len(asked) == 1 else [busy]))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = GpuScreen(session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        app.push_screen(screen)
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        assert box(screen).startswith("VM 103 is using the card")
        assert "✗ Card is in use by VM 103" in timeline(screen) and screen.s.state["step"] == 6
        assert 6 not in screen.s.state["done"] and applied == []


# ── 2: step ticks are true ───────────────────────────────────────────

@F.tui
async def test_a_failed_host_apply_shows_a_cross_and_the_steps_after_it_stay_pending(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    monkeypatch.setattr(gpu_setup.Session, "host_apply", lambda self, plan, confirmed=False: {
        "ok": False, "error": "update-grub failed", "rolled_back": True})
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        tl = timeline(screen)
        assert "✗ Host setup did not finish" in tl and "✓ Set up the host" not in tl
        assert "· Restart this host" in tl and "· Check the card is free" in tl and "· Start the Mac" in tl
        assert box(screen).startswith("The host setup did not finish")


@F.tui
async def test_a_mac_that_cannot_take_the_card_shows_a_cross_on_the_attach_step(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    approved(s)
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    ready(env)
    reboot_host(env)
    env.qm.vms[100] = ("stopped", SEQUOIA + "hostpci0: 0000:09:00,pcie=1\n")   # its slot is taken by another device
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = GpuScreen(session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        app.push_screen(screen)
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        assert "✗ Mac cannot take the card" in timeline(screen) and "✓ Attached" not in timeline(screen)
        assert [str(b.label) for b in screen.query("#g-box-buttons Button")] == ["Pick another Mac", "Check again"]


@F.tui
async def test_a_mac_that_does_not_start_is_not_shown_as_started(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    approved(s)
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    ready(env)
    reboot_host(env)
    starts = []
    run = env.qm.run
    env.qm.run = lambda argv: (starts.append(argv), CommandResult(False, 1, "start failed: no power"))[1] \
        if argv[:2] == ["qm", "start"] and len(starts) == 0 else run(argv)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = GpuScreen(session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        app.push_screen(screen)
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        tl = timeline(screen)
        assert "✗ Mac did not start" in tl and "✓ Attached it to macos-sequoia" in tl and "✓ Started" not in tl
        assert box(screen).startswith("The Mac did not start") and "start failed: no power" in box(screen)
        assert screen.s.state["start_failed"]
        await pilot.press("enter")                                           # Start again
        await mode(pilot, screen, "first_boot")
        assert not screen.s.state["start_failed"] and "✓ Started the Mac" in timeline(screen)


@F.tui
async def test_a_step_that_crashes_shows_a_cross_and_a_way_on(tmp_path, applied, monkeypatch):
    env = amd_env(tmp_path)
    s = env.session()
    approved(s)
    for step in (3, 4, 5):
        s.complete(step, skipped=step in (4, 5))
    ready(env)
    reboot_host(env)
    monkeypatch.setattr(gpu_setup.Session, "verify", lambda self: (_ for _ in ()).throw(RuntimeError("lspci vanished")))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = GpuScreen(session=env.session())
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        app.push_screen(screen)
        await F.settle(pilot, lambda: bool(screen.query("#g-primary-run")))
        assert box(screen).startswith("Something went wrong") and "lspci vanished" in box(screen)
        await F.settle(pilot, lambda: "enter Try again" in keys(app))   # the key bar follows the box by a redraw
        assert "✗ Step did not finish" in timeline(screen)


# ── 5: the run screen the moment the user approves ───────────────────

@F.tui
async def test_the_run_screen_is_up_while_the_host_works_and_keys_keep_working(tmp_path, monkeypatch):
    env = amd_env(tmp_path)
    release, entered = threading.Event(), threading.Event()
    original = gpu_setup.Session.host_apply

    def slow(self, plan, confirmed=False):
        entered.set()
        release.wait(20)
        return original(self, plan, confirmed)

    monkeypatch.setattr(gpu_setup.Session, "host_apply", slow)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await F.settle(pilot, lambda: entered.is_set() and screen.mode == "run")
        tl = timeline(screen)
        assert "◐ Back up files" in tl and "· Set up the host" in tl and "✓" not in tl.split("Restart")[0]
        assert screen.working and F.text_of(screen.query_one("#g-run-title")).startswith("Setting up the")
        first = tl.splitlines()[0][-4:]
        await F.settle(pilot, lambda: timeline(screen).splitlines()[0][-4:] != first, tries=120)   # it moves every second
        await pilot.press("escape")                                           # keys answer: leaving waits, politely
        assert app.screen is screen and any("Wait for this step" in n.message for n in app._notifications)
        await pilot.press("p")
        await F.settle(pilot, lambda: isinstance(app.screen, ChangesDialog))
        await pilot.press("escape")
        await pilot.press("question_mark")
        await F.settle(pilot, lambda: app.screen is not screen and not isinstance(app.screen, ChangesDialog))
        await pilot.press("escape")
        release.set()
        await F.settle(pilot, lambda: box(screen).startswith("Time for the one restart"))
        assert "✓ Set up the host" in timeline(screen)


@F.tui
async def test_backup_words_count_only_files_that_existed(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)

        def entries(*existed):
            return [{"path": f"/etc/{i}", "existed": e, "stamp": "s", "reverted": False} for i, e in enumerate(existed)]

        for existed, words in (((False, False), "Nothing to back up (2 new files)"), ((False,), "Nothing to back up (1 new file)"),
                               ((True, True), "Backed up 2 files"), ((True,), "Backed up 1 file"),
                               ((True, False, False), "Backed up 1 file, 2 new"), ((), "Nothing to back up")):
            screen.s.state["host_changes"] = entries(*existed)
            assert screen._backup_words() == words
        screen.s.state["host_changes"] = [{"path": "/etc/o", "existed": True, "stamp": "a", "reverted": True},
                                          {"path": "/etc/n", "existed": False, "stamp": "b", "reverted": False}]
        assert screen._backup_words() == "Backed up 1 file, 1 new"           # the whole record counts
        retried = [{"path": "/etc/a", "existed": True, "stamp": "a", "reverted": False},
                   {"path": "/etc/b", "existed": True, "stamp": "a", "reverted": False},
                   {"path": "/etc/a", "existed": True, "stamp": "b", "reverted": False},    # a retried host setup
                   {"path": "/etc/c", "existed": False, "stamp": "b", "reverted": False}]
        screen.s.state["host_changes"] = retried
        assert screen._backup_words() == "Backed up 2 files, 1 new"          # /etc/a once, from both runs


# ── 6 and 7: the files, in the undo question and its answer ──────────

@F.tui
async def test_the_undo_question_lists_what_it_will_do_and_the_answer_says_removed(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await pilot.press("u")
        dialog = app.screen
        lines = [F.text_of(w) for w in dialog.query(".dialog-line")]
        assert lines[0].startswith("Remove /etc/") and "Remove /etc/default/grub.d/osx-proxmox-next.cfg" in lines
        assert "The host needs one restart afterwards." in lines and lines[-1] == "Anything you edited since is kept and reported."
        assert app.focused.name == "no" and "enter Choose" in keys(app) and "Put everything back" not in keys(app)
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: isinstance(app.screen, ChoiceDialog) and "Everything is back" in F.text_of(
            app.screen.query_one(".dialog-title")))
        done = dialog_text(app.screen)
        assert "Removed /etc/modprobe.d/osx-proxmox-next-vfio.conf" in done and "Restored /etc/modprobe.d" not in done
        assert done.endswith("Restart this host when it suits you. The boot change stays active until then.")


@F.tui
async def test_the_undo_question_names_the_mac_that_has_the_card(tmp_path, applied):
    env = amd_env(tmp_path)
    s = env.session()
    approved(s)
    s.state["vm_records"] = [100]
    gpu_setup.gpu.record_path(100).parent.mkdir(parents=True, exist_ok=True)
    gpu_setup.gpu.record_path(100).write_text('{"vmid": 100, "bdf": "0000:06:00", "vendor": "amd", "oc": "gpu"}')
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env, session=s)
        await F.settle(pilot, lambda: box(screen).startswith("Time for the one restart") if screen.mode == "run" else False)
        await pilot.press("u")
        await F.settle(pilot, lambda: "Take the card off macos-sequoia (VM 100)" in dialog_text(app.screen),
                       what="the take-the-card-off dialog text")
        assert "Take the card off macos-sequoia (VM 100)" in dialog_text(app.screen)


# ── 8: see every change ──────────────────────────────────────────────

@F.tui
async def test_p_shows_plain_lines_and_x_toggles_the_exact_commands(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await at_choose(pilot, app, env)
        await pilot.press("p")
        dialog = app.screen
        assert isinstance(dialog, ChangesDialog)
        assert keys(app).split() == ["↑↓", "Scroll", "x", "Exact", "commands", "esc", "Close"]
        plain = F.text_of(dialog.query_one("#changes-text"))
        assert "This host:" in plain and "--- /etc/modprobe.d/osx-proxmox-next-vfio.conf" in plain
        assert "run: update-initramfs" in plain and "Hands the card (0000:06:00,pcie=1) to VM 100 as hostpci0" in plain
        assert "bash -c" not in plain and "qm set" not in plain
        await pilot.press("x")
        await F.settle(pilot, lambda: "qm set" in F.text_of(dialog.query_one("#changes-text")), what="the exact commands")
        exact = F.text_of(dialog.query_one("#changes-text"))
        assert "qm set 100 --hostpci0 0000:06:00,pcie=1" in exact and "This host:" in exact
        assert "x Plain summary" in keys(app)
        await pilot.press("x")
        await F.settle(pilot, lambda: F.text_of(dialog.query_one("#changes-text")) == plain, what="the plain summary")
        assert F.text_of(dialog.query_one("#changes-text")) == plain and "x Exact commands" in keys(app)
        await pilot.press("escape")
        assert app.screen is not dialog


# ── 3: nothing opens between the choose screen, Before you continue and the run screen ──

@F.tui
async def test_nothing_opens_between_choose_before_you_continue_and_the_run(tmp_path, applied):
    env = holders_env(tmp_path)
    opened = []
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        original = app.push_screen
        app.push_screen = lambda screen_, *a, **k: (opened.append(type(screen_).__name__), original(screen_, *a, **k))[1]
        await pilot.press("enter")
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await F.settle(pilot, lambda: box(screen).startswith("Time for the one restart"))
        assert opened == ["BeforeDialog"]


# ── 10: the Home banner after the restart ────────────────────────────

@F.tui
async def test_the_home_banner_says_when_the_restart_is_done(monkeypatch):
    F.patch_host(monkeypatch)
    state = {"finished": False, "gpu": {"bdf": "0000:0a:00"}, "step": 5, "vm": 101, "boot_id": "the-old-boot"}
    monkeypatch.setattr("osx_proxmox_next.screens.home.host_setup.load_state", lambda root="/": state)
    for boot, expected in (("the-old-boot", "▸ Graphics card setup for macos-sequoia is waiting for the restart.  g continues"),
                           ("a-new-boot", "▸ Restart done. Graphics card setup for macos-sequoia is ready to finish.  g continues")):
        monkeypatch.setattr(host_setup, "boot_id", lambda root="/", boot=boot: boot)
        app = NextApp()
        async with app.run_test(size=SIZE) as pilot:
            await F.settle(pilot, lambda app=app: isinstance(app.screen, HomeScreen) and app.screen.loaded)
            await pilot.pause(0.3)
            assert F.text_of(app.screen.query_one("#resume")) == expected
