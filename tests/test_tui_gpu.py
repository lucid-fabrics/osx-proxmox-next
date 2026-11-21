"""The graphics card flow, moment 1: choose the card, see what it takes, approve once (and the BIOS screen)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from textual.widgets import Button, Input

import tui_fakes as F
from gpu_fakes import write
from gpu_tui_fakes import (  # noqa: F401 - _vm_list is an autouse fixture
    SIZE, _no_network, _vm_list, amd_env, applied, at_choose, at_choose_unusable, box, dialog_text, flat, host_card, keys, mode, nvidia_card,
    panel, reboot_host, row, start, timeline)
from osx_proxmox_next import gpu_setup, host_setup, nullmoth
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.gpu_setup import CONFIRM_PHRASE, NVIDIA_DISPLAY_NOTE
from osx_proxmox_next.nullmoth import ACK_PHRASE
from osx_proxmox_next.screens.base import ChoiceDialog
from osx_proxmox_next.screens.gpu import ChangesDialog, GpuScreen
from osx_proxmox_next.screens.gpu_before import BeforeDialog
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.widgets.rows import ChoiceRow
from test_gpu_setup import SEQUOIA, Env, ready

# ── the choose screen ────────────────────────────────────────────────

@F.tui
async def test_choose_shows_the_card_the_mac_and_what_it_takes(tmp_path):
    env = amd_env(tmp_path, cpu="intel")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert F.text_of(screen.query_one("#g-title")).startswith("Give macos-sequoia a graphics card")
        card = row(screen, "#g-card")
        assert "Radeon RX 6600" in card and "Works in macOS without extra drivers" in card
        assert "0000:06:00" not in card                              # the address lives in the p dialog
        assert "macos-sequoia" in row(screen, "#g-mac") and "VM 100" in row(screen, "#g-mac")
        display = row(screen, "#g-display")
        assert "Monitor on the card" in display and "Best performance. Plug a screen into the card." in display
        assert "Other choice: keep the Proxmox console" in display
        assert screen.query_one("#g-display").display and not screen.query_one("#g-driver").display
        assert not screen.query("#g-host") and not screen.query("#g-confirm") and not screen.query("#g-ack")
        need = panel(screen)
        assert "✓ BIOS settings  already right" in need
        assert "● Host setup  3 files change" in need and "● One restart  about 3 min" in need
        assert "● Attach to the Mac  automatic" in need
        part = F.text_of(screen.query_one("#g-part"))
        assert "1 Press enter" in part and "2 Restart when asked" in part and "3 Open osx-next again" in part
        assert "it finishes by itself" in part
        assert not screen.query_one("#g-primary", Button).disabled
        assert "Graphics card BETA" in screen.query_one("#topbar").render().plain
        for key in ("Move", "Change", "Set it up", "See every change", "Cancel", "Help"):
            assert key in keys(app)
        assert F.text_of(screen.query_one("#g-note")) == "Nothing changes until you press enter."
        assert F.text_of(screen.query_one("#g-safe")) == "✓ Every file is backed up first. u puts everything back."
        assert not screen.query_one("#g-optional").display


@F.tui
async def test_the_detected_line_of_an_intel_card_is_not_shown_for_a_radeon(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert "Intel graphics" not in row(screen, "#g-card") and "detected" not in row(screen, "#g-card")


@F.tui
async def test_a_card_still_on_its_host_driver_can_be_approved_with_a_mac_selected(tmp_path, applied):
    """B1: before the host setup every real card is on amdgpu: that is work to do, not a refusal."""
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        prev = screen.preview
        assert prev.refusals == [] and prev.host_pending and "amdgpu" in prev.host_pending[0]
        assert prev.vm_plan.steps and not screen.query_one("#g-primary", Button).disabled
        assert F.text_of(screen.query_one("#g-reason")) == ""
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert screen.s.state["gpu"]["bdf"] == "0000:06:00" and screen.s.approval["vm_fp"]


@F.tui
async def test_an_nvidia_card_on_its_host_driver_goes_from_enter_to_the_attach(tmp_path, monkeypatch, applied):
    env = Env(tmp_path)
    nvidia_card(env.root)
    seen = {}

    def finish(self, choice, on_event):
        seen["choice"] = choice
        return SimpleNamespace(verified=True, version="2.9", kexts=7, ip="", display="yes", notes=[], detail="",
                               result="installed")

    monkeypatch.setattr(gpu_setup.Session, "finish_nvidia", finish)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert screen.preview.refusals == [] and screen.preview.host_pending
        await pilot.press("enter")                                  # the notice dialog
        assert isinstance(app.screen, BeforeDialog)
        app.screen.query_one("#g-ack", Input).value = ACK_PHRASE
        await pilot.pause(0.2)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert 4 in screen.s.state["done"] and box(screen).startswith("Time for the one restart")
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        reboot_host(env)
        screen2 = GpuScreen(session=env.session())
        app.push_screen(screen2)
        await mode(pilot, screen2, "first_boot")
        assert seen["choice"] == "v2.9"
        assert "NullMoth 2.9 is installed and loaded" in F.text_of(screen2.query_one("#g-box-text"))


@F.tui
async def test_the_amd_attach_after_the_restart_matches_what_was_approved_on_its_host_driver(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        approved_fp = None
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        approved_fp = screen.s.approval["vm_fp"]
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))
        reboot_host(env)
        dialogs = []
        original = app.push_screen
        app.push_screen = lambda s_, *a, **k: (dialogs.append(type(s_).__name__), original(s_, *a, **k))[1]
        screen2 = GpuScreen(session=env.session())
        app.push_screen(screen2)
        await mode(pilot, screen2, "first_boot")
        app.push_screen = original
        assert dialogs == ["GpuScreen"] and screen2.s.approval["vm_fp"] == approved_fp
        assert len(applied) == 1


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
async def test_p_opens_every_change_with_the_card_and_the_host_facts(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("p")
        dialog = app.screen
        assert isinstance(dialog, ChangesDialog)
        text = F.text_of(dialog.query_one("#changes-text"))
        assert "Address: 0000:06:00" in text and "Card: " in text and "This host: CPU:" in text
        assert "each file is backed up first" in text and host_setup.BACKUP_ROOT in text
        assert ("--- /etc/modprobe.d/osx-proxmox-next-vfio.conf  (Bind the card to vfio-pci before its driver loads)"
                in text)
        assert "VM 100 settings (a config snapshot is saved first):" in text
        assert "01. " in text and "qm set 100" not in text and "bash -c" not in text   # plain lines first
        assert "Hands the card (0000:06:00,pcie=1) to VM 100 as hostpci0" in text
        await pilot.press("escape")
        assert app.screen is screen
        await pilot.press("p")
        await pilot.press("p")
        assert app.screen is screen


@F.tui
async def test_the_optional_early_binding_row_shows_and_changes_the_plan(tmp_path):
    """S5: when the binding is optional the row is offered, and choosing it writes the binding."""
    env = Env(tmp_path)
    nvidia_card(env.root, driver="vfio-pci")                         # bound now, nothing claims it at boot
    write(env.root, "/lib/modules/6.8.0/modules.dep", "kernel/drivers/vfio/vfio.ko:\n")
    ready(env)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert screen.preview.plan["optional"] is True
        assert screen.query_one("#g-optional").display and "Early bind" in row(screen, "#g-optional")
        files_before = len(screen.preview.plan["changes"])
        screen.query_one("#g-optional", ChoiceRow).set_value("on", notify=True)
        await F.settle(pilot, lambda: screen.preview is not None and screen.preview.optional)
        assert screen.preview.plan["optional"] is False and len(screen.preview.plan["changes"]) > files_before
        assert screen.query_one("#g-optional").display
        assert screen.preview.plan["changes"] and screen.preview.optional


@F.tui
async def test_the_optional_row_stays_hidden_when_the_binding_is_not_optional(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert screen.preview.plan["optional"] is False and not screen.query_one("#g-optional").display


# ── before you continue ──────────────────────────────────────────────

@F.tui
async def test_nvidia_asks_its_notice_once_in_the_dialog_and_never_carries_it_over(tmp_path):
    env = Env(tmp_path)
    nvidia_card(env.root)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert not screen.query_one("#g-display").display and screen.query_one("#g-driver").display
        hint = flat(row(screen, "#g-card"))
        assert "Works with the NullMoth driver (beta)" in hint
        assert "! Third-party driver. You confirm before anything changes." in hint
        assert nullmoth.NOTICE_BODY not in hint                       # the choose screen stays clean
        assert not screen.query_one("#g-primary", Button).disabled
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, BeforeDialog)
        assert "Before you continue" in F.text_of(dialog.query(".dialog-title").first())
        assert nullmoth.NOTICE_TITLE in F.text_of(dialog.query_one("#before-notice-title"))
        assert nullmoth.NOTICE_BODY in F.text_of(dialog.query_one("#before-notice-text"))
        assert F.text_of(dialog.query_one("#before-notice-text")).startswith(NVIDIA_DISPLAY_NOTE)
        assert nullmoth.ACK_LABEL in F.text_of(dialog.query_one("#before-ack-label"))
        assert dialog.query_one("#before-go", Button).disabled
        field = dialog.query_one("#g-ack", Input)
        field.value = "i accept"                                      # the exact phrase only
        await pilot.pause(0.2)
        assert dialog.query_one("#before-go", Button).disabled
        field.value = ACK_PHRASE
        await F.settle(pilot, lambda: not dialog.query_one("#before-go", Button).disabled)
        await pilot.press("enter")                                    # enter in the field approves too
        await mode(pilot, screen, "run")
        assert screen.s.state["ack_nullmoth"] is True and screen.s.state["display"] == "console"
        assert screen.s.approval["release"] == "v2.9"                 # the newest stable release is preselected


@F.tui
async def test_the_notice_scrolls_and_says_how_many_lines_are_left(tmp_path):
    env = Env(tmp_path)
    nvidia_card(env.root)
    app = NextApp()
    async with app.run_test(size=(100, 30)) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        dialog = app.screen
        await pilot.pause(0.3)
        more = F.text_of(dialog.query_one("#before-more"))
        assert more.startswith("↓ ") and more.endswith(" more lines")
        await pilot.press("pagedown", "pagedown", "pagedown")
        await pilot.pause(0.3)
        assert F.text_of(dialog.query_one("#before-more")) == ""      # read to the end: nothing left
        await pilot.press("escape")
        assert app.screen is screen and screen.s.approval is None


@F.tui
@pytest.mark.parametrize("size", [(100, 30), (80, 24), (110, 34)])
async def test_the_dialog_buttons_and_the_phrase_field_stay_on_screen_at_every_size(tmp_path, size):
    env = Env(tmp_path)
    nvidia_card(env.root)
    app = NextApp()
    async with app.run_test(size=size) as pilot:
        await at_choose(pilot, app, env)
        await pilot.press("enter")
        await pilot.pause(0.5)
        dialog = app.screen
        box = dialog.query_one("#before-box")
        content_bottom = box.region.bottom - 2                        # the padding row and the border row
        for widget in (dialog.query_one("#g-ack"), dialog.query_one("#before-go"), dialog.query_one("#before-cancel")):
            assert widget.region.bottom <= content_bottom and widget.region.y >= box.region.y
        assert dialog.query_one("#before-notice").size.height >= 3


@F.tui
async def test_escape_in_the_dialog_changes_nothing(tmp_path):
    env = Env(tmp_path)
    nvidia_card(env.root)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        assert isinstance(app.screen, BeforeDialog)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.screen is screen and screen.s.state["gpu"] is None and screen.mode == "choose"
        assert not (env.root / host_setup.STATE_FILE.lstrip("/")).exists()


@F.tui
async def test_a_refusal_is_one_line_and_disables_the_button(tmp_path):
    env = amd_env(tmp_path)
    env.qm.vms[120] = ("running", "hostpci0: 0000:06:00,pcie=1\n")   # another VM runs with the card
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose_unusable(pilot, app, env)
        assert "No card here can run macOS graphics" in panel(screen)
        assert screen.query_one("#g-primary", Button).disabled
        assert "Not offered: Radeon RX 6600 (in use by VM 120)" in row(screen, "#g-card")


@F.tui
async def test_a_vm_that_cannot_take_the_card_is_a_refusal_with_its_reason(tmp_path):
    env = Env(tmp_path, vms={100: ("stopped", SEQUOIA.replace("sequoia", "sonoma"))})
    nvidia_card(env.root)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        reason = F.text_of(screen.query_one("#g-reason"))
        assert reason.startswith("Refused: ") and "NullMoth needs macOS 15.5 Sequoia or newer" in reason
        assert screen.query_one("#g-primary", Button).disabled
        await pilot.press("enter")                                    # a disabled primary does nothing
        assert app.screen is screen and screen.s.state["gpu"] is None


@F.tui
async def test_the_choose_labels_are_10_wide_so_the_display_hint_fits_one_row_at_100_columns(tmp_path):
    env = amd_env(tmp_path, cpu="intel")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert row(screen, "#g-display").startswith(" Display   \u2039 ")        # the label column is 10 wide
        lines = screen.query_one("#g-display")._lines(screen.query_one("#g-display").size.width)
        assert "Best performance. Plug a screen into the card." in [t.plain.strip() for t in lines]


@F.tui
async def test_under_90_columns_every_need_value_sits_on_its_own_row_under_its_label(tmp_path):
    env = amd_env(tmp_path, cpu="intel")
    app = NextApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = await at_choose(pilot, app, env)
        rows = [t for t in screen.query_one("#g-need")._layout(30)]
        text = [t.plain for t in rows]
        assert text == ["✓ BIOS settings", "  already right", "● Host setup", "  3 files change",
                        "● One restart", "  about 3 min", "● Attach to the Mac", "  automatic"]
    wide = NextApp()
    async with wide.run_test(size=SIZE) as pilot:                       # wide: the values sit beside the labels
        screen = await at_choose(pilot, wide, env)
        assert not screen.query_one("#g-need").stack


@F.tui
async def test_only_display_adapter_is_careful_and_needs_the_typed_phrase(tmp_path, applied, monkeypatch):
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    env = Env(tmp_path)
    host_card(env.root, boot_vga=True)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert not screen.query_one("#g-primary", Button).disabled    # the phrase is asked in the dialog
        assert "! Read the warnings first. You confirm before anything changes." in flat(row(screen, "#g-card"))
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, BeforeDialog)
        warnings = F.text_of(dialog.query_one("#before-warnings"))
        assert "Careful: This card is the only display adapter" in warnings.replace("\n  ", " ")
        assert "You are not on an SSH session. Make sure you can log in over the network before you reboot." in \
            warnings.replace("\n  ", " ")                        # SF1: the warning of the plan that will run
        assert "Refused:" not in warnings and dialog.query_one("#before-go", Button).disabled
        assert "Type I understand to continue" in F.text_of(dialog.query_one(".before-label"))
        field = dialog.query_one("#g-confirm", Input)
        field.value = "i understand"
        await pilot.pause(0.2)
        assert dialog.query_one("#before-go", Button).disabled
        field.value = CONFIRM_PHRASE
        await F.settle(pilot, lambda: not dialog.query_one("#before-go", Button).disabled)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        approval = screen.s.approval
        assert approval["typed"] and approval["allow_no_console"] and 4 in screen.s.state["done"]


@F.tui
async def test_the_dialog_opens_again_when_the_plan_changed_after_the_phrase(tmp_path, applied):
    env = Env(tmp_path)
    host_card(env.root, boot_vga=True)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, BeforeDialog)
        env.qm.vms[100] = ("stopped", SEQUOIA.replace("args: ", "args: -smbios type=1 "))   # the Mac was edited meanwhile
        dialog.query_one("#g-confirm", Input).value = CONFIRM_PHRASE
        await F.settle(pilot, lambda: not dialog.query_one("#before-go", Button).disabled)
        await pilot.press("enter")
        await F.settle(pilot, lambda: app.screen is not dialog and isinstance(app.screen, BeforeDialog))
        again = app.screen
        assert again is not dialog and again.query_one("#before-go", Button).disabled   # the phrase is asked again
        assert "Careful: This card is the only display adapter" in \
            F.text_of(again.query_one("#before-warnings")).replace("\n  ", " ")   # N1: and says why
        assert screen.s.approval is None and "The plan changed while you were reading it" in screen.result_text


@F.tui
async def test_a_plan_with_a_warning_but_no_phrase_asks_in_the_dialog_without_a_field(tmp_path, applied):
    env = Env(tmp_path)
    host_card(env.root)
    env.qm.vms[120] = ("stopped", "name: other\n")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        screen.preview.plan["warnings"].append("A made-up warning")  # a plan with a plain warning
        screen.preview.vm_plan.warnings.clear()
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, BeforeDialog) and not dialog.query("Input")
        assert "! A made-up warning" in F.text_of(dialog.query_one("#before-warnings"))
        assert not dialog.query_one("#before-go", Button).disabled
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert not screen.s.approval["typed"]


@F.tui
async def test_no_usable_card_says_where_to_look_and_nothing_can_be_set_up(tmp_path):
    env = Env(tmp_path)
    host_card(env.root, device=0x73C0)                                # a chip macOS has no driver for
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await start(pilot, app, env)
        await F.settle(pilot, lambda: screen.mode == "choose" and bool(screen.query("#g-card")))
        await pilot.pause(0.3)
        assert "No card here can run macOS graphics. See the docs for cards that work." in panel(screen)
        assert screen.query_one("#g-primary", Button).disabled
        assert "no card here can run macOS graphics" in F.text_of(screen.query_one("#g-title"))
        await pilot.press("enter")
        assert screen.mode == "choose"


@F.tui
async def test_a_second_card_that_macos_cannot_use_is_listed_dimmed_with_its_reason(tmp_path):
    env = amd_env(tmp_path)
    host_card(env.root, bdf="0000:41:00", device=0x73C0, audio=0xAB29, group=9)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        card_row = screen.query_one("#g-card", ChoiceRow)
        assert [o.enabled for o in card_row.options] == [True, False]
        assert flat(row(screen, "#g-card")).endswith("Not offered: AMD GPU (no Apple driver)")
        await pilot.press("right")                                    # the unusable card is skipped
        assert card_row.value == "0000:06:00"


@F.tui
async def test_two_usable_cards_are_a_choice_and_the_one_card_case_is_preselected(tmp_path):
    env = amd_env(tmp_path)
    host_card(env.root, bdf="0000:41:00", device=0x67FF, audio=0xAB29, group=9)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert "choose the card to hand over" in F.text_of(screen.query_one("#g-title"))
        first = screen.query_one("#g-card", ChoiceRow).value
        await pilot.press("right")
        await pilot.pause(0.3)
        second = screen.query_one("#g-card", ChoiceRow).value
        assert second != first
        await F.settle(pilot, lambda: screen.preview is not None and screen.preview.card.bdf == second)


@F.tui
async def test_the_palette_entry_has_a_mac_choice_with_decide_later(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env, vmid=None)
        mac = screen.query_one("#g-mac", ChoiceRow)
        assert [o.value for o in mac.options] == ["100", "later"] and mac.value == "100"
        screen.query_one("#g-mac").focus()
        await pilot.press("right")
        await pilot.pause(0.3)
        assert mac.value == "later" and "pick a Mac later" in row(screen, "#g-mac")
        assert "Attach to the Mac" not in panel(screen)
        assert "Pick a Mac afterwards" not in F.text_of(screen.query_one("#g-part"))   # a restart comes first
        await F.settle(pilot, lambda: screen.preview is not None and screen.preview.vmid is None)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        assert screen.s.state["vm"] is None and "Attach it to" not in timeline(screen)
        assert "· Check the card is free" in timeline(screen) and "Start the Mac" not in timeline(screen)


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
async def test_a_stopped_holder_is_confirmed_once_in_before_you_continue(tmp_path, applied):
    """Fix 3: the warning of a card other VMs list is shown once, in "Before you continue", and its "Set it up"
    is the only approval: no question opens before it or after it."""
    env = amd_env(tmp_path)
    env.qm.vms[120] = ("stopped", "hostpci0: 0000:06:00,pcie=1\n")
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        assert "stopped VM 120" in row(screen, "#g-card")
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, BeforeDialog) and "stopped VM 120" in flat(F.text_of(dialog.query_one("#before-warnings")))
        assert screen.s.state["gpu"] is None                         # nothing is stored before the approval
        await pilot.press("escape")
        assert app.screen is screen and screen.s.state["gpu"] is None and screen.mode == "choose"
        await pilot.press("enter")
        await pilot.press("enter")                                    # "Set it up": the one approval
        await mode(pilot, screen, "run")
        assert not isinstance(app.screen, ChoiceDialog)
        assert screen.s.state["gpu"]["bdf"] == "0000:06:00"


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


@F.tui
async def test_the_footer_and_help_name_every_key_of_the_flow(tmp_path):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await at_choose(pilot, app, env)
        await pilot.press("f1")
        shown = F.text_of(app.screen.query_one("#help-keys"))
        for word in ("See every change", "Put everything back", "Check again", "Yes, all good", "No, help me", "Leave"):
            assert word in shown


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


# ── the BIOS screen ──────────────────────────────────────────────────

def bios_env(tmp_path) -> Env:
    env = Env(tmp_path, virt=False, tables=(), efi=False)
    host_card(env.root)
    env.qm.vms[101] = ("running", SEQUOIA)
    return env


def firmware_restart(env) -> None:
    from osx_proxmox_next import bios_guide
    fw = env.root / bios_guide.OS_INDICATIONS.lstrip("/")
    fw.parent.mkdir(parents=True, exist_ok=True)
    fw.write_bytes(b"\x06\x00\x00\x00\x01" + b"\x00" * 7)


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


@F.tui
async def test_the_bios_screen_lists_the_settings_and_restarts_into_the_bios_after_a_confirmation(tmp_path):
    env = bios_env(tmp_path)
    firmware_restart(env)
    app = NextApp()
    async with app.run_test(size=(110, 40)) as pilot:
        screen = await at_choose(pilot, app, env)
        assert "● BIOS settings" in panel(screen) and "to change" in panel(screen)
        assert "Change the BIOS settings" in F.text_of(screen.query_one("#g-part"))
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        title = F.text_of(screen.query_one("#g-run-title"))
        assert title == "One thing only you can do  a BIOS setting on this host blocks the card"
        rows = F.text_of(screen.query_one("#g-bios-rows"))
        assert rows.startswith("Change in the BIOS · ASUS PRIME X670-P")
        assert "✗ Intel virtualization (VT-x)  set to Enabled" in rows
        assert "Without it no VM can start." in rows and "✓ " in rows and "already right" in rows
        assert "How" in rows and "1 Restart into the BIOS (enter does it for you)" in rows
        assert "3 Open osx-next again" in rows and "it checks and carries on by itself" in rows
        for old in ("+====", "BIOS STEPS FOR THIS SERVER", "[!!]", "[ok]", "photo"):
            assert old not in rows
        assert screen.query_one("#g-bios-qr")                          # the QR code replaces the photo
        assert screen.query_one("#g-primary-bios", Button).label.plain == "Restart into BIOS"
        assert screen.query_one("#g-recheck", Button).label.plain == "Check again"
        order = [b.id for b in screen.query("#g-bios-foot Button")]
        assert order == ["g-recheck", "g-primary-bios"]               # the primary button is the right one
        code, cols = screen.query_one("#g-bios-code"), screen.query_one("#g-bios-cols")
        assert code.region.height == len(screen.bios_view["qr"]) and code.region.bottom <= cols.region.bottom  # whole
        note = screen.query_one("#g-note")
        assert F.text_of(note) == "Cannot find it? The BIOS search finds settings by name."
        assert note.region.width >= len(F.text_of(note)) and note.region.bottom <= screen.query_one("#g-bios-foot").region.y
        wrapped = screen.query_one("#g-bios-rows")._layout(30)           # a narrow column: steps keep a hanging indent
        steps = [t.plain for t in wrapped[next(i for i, t in enumerate(wrapped) if t.plain.startswith("1 ")):]]
        assert steps[0].startswith("1 ") and all(t.startswith("  ") for t in steps[1:] if t and not t[0].isdigit())
        for key in ("Restart into BIOS", "Check again"):
            assert key in keys(app)
        await pilot.press("enter")
        dialog = app.screen
        assert isinstance(dialog, ChoiceDialog) and "101 (vm101)" in dialog_text(dialog)
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
        await pilot.press("r")                                      # check again: still failing, the screen again
        await mode(pilot, screen, "bios")
        assert screen.bios_view is not None and screen.bios_view["rows"]


@F.tui
async def test_the_bios_screen_says_settings_when_more_than_one_must_change(tmp_path):
    env = Env(tmp_path, virt=False, tables=(), efi=False, groups=0)
    host_card(env.root)
    app = NextApp()
    async with app.run_test(size=(110, 40)) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        assert F.text_of(screen.query_one("#g-run-title")).endswith("BIOS settings on this host block the card")
        rows = F.text_of(screen.query_one("#g-bios-rows"))
        assert "Change the settings, then save and exit (often F10)" in rows
        assert "\u2717 " in rows and rows.count("\u2717") == 2


@F.tui
async def test_the_qr_code_is_whole_at_100_x_30_and_the_link_replaces_it_when_the_height_cannot_hold_it(tmp_path):
    env = bios_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        code, cols = screen.query_one("#g-bios-code"), screen.query_one("#g-bios-cols")
        assert code.region.height == len(screen.bios_view["qr"]) and code.region.bottom <= cols.region.bottom
    (tmp_path / "short").mkdir()
    short_env, short = bios_env(tmp_path / "short"), NextApp()
    async with short.run_test(size=(100, 24)) as pilot:                  # a cut-off code cannot be scanned
        screen = await at_choose(pilot, short, short_env)
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        assert not screen.query("#g-bios-qr")
        assert "Same steps with pictures on your phone:" in " ".join(F.text_of(w) for w in screen.query("#g-bios-left Static"))


@F.tui
async def test_the_bios_screen_without_room_puts_the_link_in_the_text_instead_of_the_qr_code(tmp_path):
    env = bios_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=(80, 30)) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        assert not screen.query("#g-bios-qr")
        assert "Same steps with pictures on your phone:" in " ".join(F.text_of(w) for w in screen.query("#g-bios-left Static"))


@F.tui
async def test_without_a_firmware_restart_the_bios_screen_only_offers_check_again(tmp_path):
    env = bios_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "bios")
        assert not screen.query("#g-primary-bios") and screen.query_one("#g-recheck", Button).has_class("primary")
        assert "Restart the host and press" in F.text_of(screen.query_one("#g-bios-rows"))
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
