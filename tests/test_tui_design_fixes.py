"""The design review fixes: shared bars and rows, Home layout, New Mac More, help, and the audit nits."""
from __future__ import annotations

import threading

from textual.widgets import Static

import tui_fakes as F
from osx_proxmox_next import smbios
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens.base import ChoiceDialog, HelpScreen, help_groups
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.screens.host_check import HostCheckScreen
from osx_proxmox_next.screens.new_mac import NewMacScreen
from osx_proxmox_next.widgets.checkbox import Check
from osx_proxmox_next.widgets.chrome import KeyBar
from osx_proxmox_next.widgets.lines import Line, Lines
from osx_proxmox_next.widgets.live_screen import LiveScreen
from osx_proxmox_next.widgets.rows import ActionRow, ChoiceRow, Option, wrap_text
from test_tui_widgets import Host, lines

SIZE = (100, 30)


async def home(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await pilot.pause(0.3)
    return app.screen


async def new_mac(pilot, app):
    await home(pilot, app)
    await pilot.press("n")
    await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen) and app.screen.is_attached)
    await pilot.pause(0.3)
    return app.screen


# ── item 1: the footer ───────────────────────────────────────────────

@F.tui
async def test_the_footer_key_is_bold_accent_and_its_label_plain_with_nothing_underlined(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        bar = app.screen.query_one(KeyBar)
        text = bar.render()
        accent = app.get_css_variables()["accent"]
        assert text.plain.startswith(" ←→ Select")
        keyed = [(text.plain[s.start:s.end], s.style) for s in text.spans]
        assert keyed and all(style.bold and style.color.name.lower() == accent.lower() for _k, style in keyed)
        assert all(not style.underline and not style.meta for _k, style in keyed)
        assert {k for k, _s in keyed} >= {"←→", "enter", "s", "n", "q"}
        # the label has no style at all, so the theme's text colour shows and the link underline cannot
        assert all(not (s.style.color is None and s.style.bold) for s in text.spans)
        assert "underline" not in str(bar.rich_style)


# ── item 4: the checkbox ─────────────────────────────────────────────

@F.tui
async def test_a_checkbox_shows_an_accent_check_when_on_and_an_empty_box_when_off(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("l", "l", "d")                              # the Remove dialog: "Also delete its disk images"
        await F.settle(pilot, lambda: bool(app.screen.query("#purge")))
        box = app.screen.query_one("#purge", Check)
        accent = app.get_css_variables()["accent"].lower()
        raised = app.get_css_variables()["raised"].lower()
        assert box.BUTTON_INNER == "\u2713" and "\u2713" in box.render().plain and box.value
        assert box.get_component_rich_style("toggle--button").color.name.lower() == accent
        box.value = False
        await pilot.pause(0.1)
        off = box.get_component_rich_style("toggle--button")
        assert box.BUTTON_INNER == " " and "\u2713" not in box.render().plain       # empty, also as plain text
        assert off.bgcolor.name.lower() == raised


# ── item 7: nothing meaningful is cut ────────────────────────────────

@F.tui
async def test_a_choice_row_wraps_its_hint_and_never_ends_a_line_with_an_ellipsis():
    row = ChoiceRow("Display", [Option("a", "Monitor on the card"), Option("b", "Keep the console")], "a")
    row.set_text("", "Best performance. Plug a screen into the card and leave the console alone.")
    async with Host(row).run_test(size=(40, 8)) as pilot:
        await pilot.pause(0.2)
        text = row.render().plain
        assert "…" not in text and len(text.splitlines()) >= 3 and row.size.height == len(text.splitlines())
        joined = " ".join(text.split())
        assert "Best performance. Plug a screen into the card and leave the console alone." in joined


@F.tui
async def test_a_detail_with_no_room_goes_under_the_value_whole_and_is_left_out_when_collapsed():
    row = ChoiceRow("Mac", [Option("a", "macos-sequoia")], "a")
    row.set_text("VM 100 · macOS 15 · stopped and waiting for its card", "")
    async with Host(row).run_test(size=(36, 6)) as pilot:
        await pilot.pause(0.2)
        assert "VM 100 · macOS 15 · stopped" in " ".join(row.render().plain.split())
        row.set_collapsed(True)
        await pilot.pause(0.2)
        assert row.size.height == 1 and "stopped and waiting" not in row.render().plain


@F.tui
async def test_a_dropping_row_leaves_out_the_hint_lines_that_do_not_fit_instead_of_cutting_them():
    row = ChoiceRow("macOS", [Option("a", "Sequoia 15")], "a", drop_long=True)
    row.set_text("", "Tahoe 26 · Sonoma 14 · Ventura 13 · Monterey 12")
    async with Host(row).run_test(size=(64, 6)) as pilot:
        await pilot.pause(0.2)
        assert "Monterey 12" in row.render().plain and row.size.height == 2
    narrow = ChoiceRow("macOS", [Option("a", "Sequoia 15")], "a", drop_long=True)
    narrow.set_text("", "Tahoe 26 · Sonoma 14 · Ventura 13 · Monterey 12")
    async with Host(narrow).run_test(size=(36, 6)) as pilot:
        await pilot.pause(0.2)
        assert narrow.size.height == 1 and "Tahoe" not in narrow.render().plain and "…" not in narrow.render().plain


def test_wrapped_text_keeps_its_styles_and_never_loses_a_word():
    from rich.console import Console
    from rich.text import Text
    out = wrap_text(Text("one two three four five six", style="bold"), 10, Console(width=80))
    assert [t.plain.strip() for t in out] == ["one two", "three four", "five six"]
    assert [t.plain for t in wrap_text(Text("a\n\nb"), 10, Console())] == ["a", "", "b"]


@F.tui
async def test_lines_put_the_value_flush_right_and_wrap_a_long_left_text():
    from rich.text import Text
    panel = Lines()
    async with Host(panel).run_test(size=(30, 8)) as pilot:
        panel.set_lines([Line(Text("● Host setup"), Text("3 files change")),
                         Line(Text("a long sentence that has to wrap onto a second row"), wrap=True, indent=2),
                         Line(Text("a very long label that leaves no room"), Text("value"), wrap=False)])
        await pilot.pause(0.2)
        rows = panel.render().plain.splitlines()
        assert rows[0] == "● Host setup   3 files change".replace("   ", " " * (30 - len("● Host setup") - len("3 files change")))
        assert len(rows[0]) == 30 and rows[0].endswith("3 files change")
        assert rows[1].startswith("  a long") and len(rows) >= 4
        assert rows[-1].strip() == "value"                              # no room beside it: its own row, never cut
        assert panel.render_text().splitlines()[0] == "● Host setup  3 files change"


@F.tui
async def test_an_action_row_runs_on_enter_and_on_a_click():
    row = ActionRow("Identity", id="ident")
    row.set_text("MacPro7,1 · C02ABC", "enter makes a new one")
    seen = []

    class Rows(Host):
        def on_action_row_activated(self, event):
            seen.append(event.row.id)

    async with Rows(row).run_test(size=(60, 3)) as pilot:
        row.focus()
        await pilot.press("enter")
        await pilot.click(ActionRow)
        assert seen == ["ident", "ident"]
        assert row.render().plain == " " + "Identity".ljust(13) + "MacPro7,1 \u00b7 C02ABC  enter makes a new one"


# ── items 5 and 6: the live screen frame ─────────────────────────────

@F.tui
async def test_the_waiting_placeholder_is_centred_both_ways():
    live = LiveScreen(105, "waiting", interval=60)
    async with Host(live).run_test(size=(40, 11)) as pilot:
        await pilot.pause(0.2)
        assert live.placeholder == "waiting for the VM" and live.grabs == 0
        rows = lines(live)
        assert rows[5].strip() == "waiting for the VM" and rows[5].index("w") == (40 - len("waiting for the VM")) // 2
        assert all(not r.strip() for i, r in enumerate(rows) if i != 5)


@F.tui
async def test_a_fitting_frame_takes_the_shape_of_the_picture(monkeypatch):
    monkeypatch.setattr(LiveScreen, "grabber", staticmethod(lambda vmid, probe: F.desktop_ppm(320, 240)))
    monkeypatch.setattr(LiveScreen, "ratio", 0.5625)
    live = LiveScreen(105, "running", interval=60, fit=True)
    async with Host(live).run_test(size=(40, 30)) as pilot:
        await F.settle(pilot, lambda: live.grabs >= 1)
        await pilot.pause(0.3)
        assert LiveScreen.ratio == 0.75 and live.size.height == 15      # 40 columns, 4:3: 30 pixel rows, 15 cell rows
        first, last = live._cells[0], live._cells[-1]
        assert len(live._cells) == 15 and first.cell_length == 40 == last.cell_length
        for row in (first, last):                                        # the picture reaches the top and the bottom row
            assert all(seg.style.color.get_truecolor() != (0, 0, 0) and seg.style.bgcolor.get_truecolor() != (0, 0, 0)
                       for seg in row)


# ── item 18: the help overlay ────────────────────────────────────────

def test_help_keys_are_grouped_move_this_screen_everywhere():
    entries = [("←  →  h  l", "Change the choice on this row"), ("enter", "Open"), ("h", "Host check"),
               ("?  f1", "This help"), ("q", "Quit"), ("tab  shift+tab", "Next or previous field")]
    groups = dict(help_groups(entries))
    assert [k for k, _d in groups["Move"]] == ["\u2190  \u2192  h  l"]
    assert ("h", "Host check") in groups["This screen"] and ("enter", "Open") in groups["This screen"]
    assert [k for k, _d in groups["Everywhere"]] == ["?  f1", "q", "tab  shift+tab"]
    assert list(groups) == ["Move", "This screen", "Everywhere"]


@F.tui
async def test_the_help_overlay_has_a_22_column_key_column_and_wraps_inside_its_own_column(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await new_mac(pilot, app)
        await pilot.press("f1")
        assert isinstance(app.screen, HelpScreen)
        rows = F.text_of(app.screen.query_one("#help-keys")).splitlines()
        assert [r for r in rows if r in ("Move", "This screen", "Everywhere")] == ["Move", "This screen", "Everywhere"]
        typed = next(i for i, r in enumerate(rows) if r.startswith("type".ljust(22)))
        assert rows[typed][22:].startswith("Edit a text field")
        assert rows[typed + 1].startswith(" " * 22) and rows[typed + 1].strip()       # the rest stays in its column
        assert "letters are text there" in " ".join((rows[typed] + " " + rows[typed + 1]).split())
        assert all(len(r) - len(r.lstrip()) in (0, 22) for r in rows if r.strip())     # keys at 0, descriptions at 22


# ── items 8 to 11 and N6: Home ───────────────────────────────────────

@F.tui
async def test_home_shows_whole_rows_of_cards_never_half_a_card(monkeypatch):
    F.patch_host(monkeypatch, vms=[F.record(100 + i, f"mac-{i}", "stopped") for i in range(9)])
    for size in ((100, 30), (80, 24)):
        app = NextApp()
        async with app.run_test(size=size) as pilot:
            screen = await home(pilot, app)
            grid = screen.query_one("#cards")
            cards = screen.cards()
            height = cards[0].outer_size.height
            assert grid.size.height % height == 0 and grid.size.height >= height     # a whole number of rows
            assert grid.max_scroll_y > 0                                              # the rest is reached by scrolling
            assert all(c.region.y >= grid.region.y and c.region.bottom <= grid.region.bottom
                       for c in cards[:screen.columns()])
            assert all(c.outer_size.height == height for c in cards)
            for _ in range(2):                                          # two rows down
                await pilot.press("down")
            await pilot.pause(0.3)
            focused = app.focused
            assert focused.region.bottom <= grid.region.bottom and focused.region.y >= grid.region.y


@F.tui
async def test_the_empty_state_footer_lists_only_keys_that_do_something(monkeypatch):
    F.patch_host(monkeypatch, vms=[])
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        shown = app.screen.query_one(KeyBar).render().plain
        assert "Select" not in shown and "Open" not in shown
        assert [w for w in ("New Mac", "Host", "Help", "Quit") if w in shown] == ["New Mac", "Host", "Help", "Quit"]


# ── items 12 and 13: New Mac ─────────────────────────────────────────

@F.tui
async def test_with_more_open_the_rows_above_are_one_line_each_and_the_fields_have_room(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        before = screen.query_one("#r-macos", ChoiceRow).size.height
        assert before >= 2
        from osx_proxmox_next.widgets.rows import ExpandRow
        screen.query_one("#r-more", ExpandRow).set_open(True)
        await pilot.pause(0.3)
        assert screen.has_class("more-open")
        for rid in ("r-macos", "r-size", "r-storage", "r-install", "r-apple"):
            assert screen.query_one(f"#{rid}", ChoiceRow).size.height == 1
        installer = screen.query_one("#r-installer")
        label = installer.query_one(".field-label")
        assert F.text_of(label).strip() == "Installer" and label.size.width == 13      # room for a 12 column label and a gap
        for _ in range(14):
            await pilot.press("down")
        await pilot.pause(0.2)
        focused = app.focused
        scroll = screen.query_one("#new-form")
        assert focused.region.y >= scroll.region.y and focused.region.bottom <= scroll.region.bottom
        screen.query_one("#r-more", ExpandRow).set_open(False)
        await pilot.pause(0.3)
        assert screen.query_one("#r-macos", ChoiceRow).size.height == before


@F.tui
async def test_the_identity_row_shows_the_model_and_serial_and_enter_makes_a_new_one(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await new_mac(pilot, app)
        from osx_proxmox_next.widgets.rows import ExpandRow
        screen.query_one("#r-more", ExpandRow).set_open(True)
        await pilot.pause(0.2)
        assert not screen.query_one("#r-identity").display and screen.identity is None     # Apple ID is off
        screen.query_one("#r-apple", ChoiceRow).set_value("on", notify=True)
        await pilot.pause(0.3)
        row = screen.query_one("#r-identity", ActionRow)
        first = screen.identity
        assert row.display and first.model in row.render().plain and first.serial in row.render().plain
        assert "enter makes a new one" in row.render().plain and row.label == " Identity"
        row.focus()
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert screen.identity.serial != first.serial and screen.identity.serial in row.render().plain
        assert screen._smbios_for_create().serial == screen.identity.serial        # what is shown is what the Mac gets
        screen.state.existing_uuid = "11111111-2222-3333-4444-555555555555"
        assert screen._smbios_for_create().uuid == "11111111-2222-3333-4444-555555555555"
        monkeypatch.setitem(smbios.SMBIOS_MODELS, "sonoma", "iMacPro1,1")
        monkeypatch.setitem(smbios.APPLE_PLATFORM_DATA, "iMacPro1,1", smbios.APPLE_PLATFORM_DATA["MacPro7,1"])
        screen.query_one("#r-macos", ChoiceRow).set_value("sonoma", notify=True)
        await pilot.pause(0.2)
        assert screen.identity.model == "iMacPro1,1" and "iMacPro1,1" in row.render().plain   # follows the macOS version
        screen.query_one("#r-apple", ChoiceRow).set_value("off", notify=True)
        await pilot.pause(0.2)
        assert screen.identity is None and not screen.query_one("#r-identity").display


@F.tui
async def test_a_short_narrow_new_mac_keeps_its_blank_row_and_leaves_out_what_does_not_fit(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = await new_mac(pilot, app)
        head = screen.query_one("#new-head")
        assert head.styles.margin.bottom == 1                           # the blank row under the title, as on the wide screen
        assert not screen.query_one("#new-note").display                # no room for the watch-it hint under 90 columns
        for row in screen.query("#new-form > ChoiceRow"):
            assert "…" not in row.render().plain and row.size.height == 1
        assert "Tahoe 26" not in screen.query_one("#r-macos").render().plain
    async with NextApp().run_test(size=SIZE) as pilot2:
        wide = await new_mac(pilot2, pilot2.app)
        assert wide.query_one("#new-note").display
        assert "Tahoe 26" in wide.query_one("#r-macos").render().plain


# ── N2, N3-adjacent and N7, N8 ───────────────────────────────────────

@F.tui
async def test_ctrl_q_asks_like_q_while_a_job_runs(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    gate = threading.Event()

    def slow_fix(commands, on_line):
        gate.wait(10)
        return True

    monkeypatch.setattr("osx_proxmox_next.screens.host_check._is_root", lambda: True)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_fix_commands", slow_fix)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_preflight", F.good_checks)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await F.settle(pilot, lambda: app.host_checks is not None)
        await pilot.press("h")
        await F.settle(pilot, lambda: isinstance(app.screen, HostCheckScreen))
        screen = app.screen
        await F.settle(pilot, lambda: bool(screen.query(".hc-item")))
        screen.action_fix()
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: screen.running)
        await pilot.press("ctrl+q")
        assert app.is_running and isinstance(app.screen, ChoiceDialog)        # it asked, and did not quit
        await pilot.press("escape")
        gate.set()
        await F.settle(pilot, lambda: not screen.running)
        await pilot.press("ctrl+q")
    assert not app.is_running


@F.tui
async def test_a_fix_that_raises_frees_the_screen_again(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())

    def boom(commands, on_line):
        raise RuntimeError("apt exploded")

    monkeypatch.setattr("osx_proxmox_next.screens.host_check._is_root", lambda: True)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_fix_commands", boom)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_preflight", F.good_checks)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await F.settle(pilot, lambda: app.host_checks is not None)
        await pilot.press("h")
        await F.settle(pilot, lambda: isinstance(app.screen, HostCheckScreen))
        screen = app.screen
        await F.settle(pilot, lambda: bool(screen.query(".hc-item")))
        screen.action_fix()
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: not screen.running and screen.fix_ok is False)
        assert not screen.busy()
        await pilot.press("escape")                                        # esc works again
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_check_again_runs_one_preflight_not_two(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    runs = []
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_preflight", lambda: runs.append(1) or F.good_checks())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        await F.settle(pilot, lambda: app.host_checks is not None)
        app_runs = []
        monkeypatch.setattr("osx_proxmox_next.app.run_preflight", lambda: app_runs.append(1) or F.good_checks())
        await pilot.press("h")
        await F.settle(pilot, lambda: isinstance(app.screen, HostCheckScreen))
        await F.settle(pilot, lambda: bool(app.screen.query(".hc-item")))
        await pilot.press("r")
        await F.settle(pilot, lambda: bool(runs))
        await pilot.pause(0.3)
        assert len(runs) == 1 and app_runs == []                            # the screen's own check only
        assert app.host_checks and not app.failing_checks


@F.tui
async def test_the_vm_view_names_the_undo_key_put_everything_back(monkeypatch, tmp_path):
    rec = F.record(101, "macos-sequoia", "stopped", extra="hostpci0: 0000:06:00,pcie=1\n")
    F.patch_host(monkeypatch, vms=[rec])
    monkeypatch.setattr("osx_proxmox_next.screens.vm_view.gpu.load_record", lambda vmid: object())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await home(pilot, app)
        await pilot.press("enter")
        await F.settle(pilot, lambda: type(app.screen).__name__ == "VmViewScreen")
        shown = app.screen.query_one(KeyBar).render().plain
        assert "u Put everything back" in shown and "Put the card back" not in shown
        assert any(isinstance(w, Static) for w in app.screen.query(Static))
