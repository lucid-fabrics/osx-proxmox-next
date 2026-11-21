"""A help or info dialog taller than the terminal scrolls: down and up one line, page down and page up one page,
the "N more lines" hint while text is below, and the last line of the body can always be reached."""
from __future__ import annotations

import pytest
from textual.widgets import Button

import tui_fakes as F
from gpu_tui_fakes import (  # noqa: F401 - the underscore names are autouse fixtures
    _no_network, _vm_list, amd_env, mode, start)
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.gpu_flow import Before
from osx_proxmox_next.screens.base import Choice, ChoiceDialog, HelpScreen
from osx_proxmox_next.screens.gpu import ChangesDialog
from osx_proxmox_next.screens.gpu_before import BeforeDialog
from osx_proxmox_next.screens.home import HomeScreen

SIZES = [(80, 24), (100, 30)]
LONG = [f"Step {n}: do the thing number {n} and then check it worked" for n in range(1, 61)]


def dialogs():
    """(name, factory, id of its scrolling body, id of its hint)."""
    return [
        ("help", lambda: HelpScreen("Home", [(f"key{n}", f"does the thing number {n}, which takes a while to say")
                                             for n in range(40)]), "help-scroll", "help-more"),
        ("choice", lambda: ChoiceDialog("Everything is back", LONG, [Choice("ok", "Done", "primary")]),
         "choice-scroll", "choice-more"),
        ("changes", lambda: ChangesDialog("\n".join(LONG)), "changes-scroll", "changes-more"),
        ("before", lambda: BeforeDialog(Before(notice_title="Read this", notice_body="\n".join(LONG))),
         "before-notice", "before-more"),
    ]


async def read_to_the_end(pilot, app, scroll, hint) -> None:
    """Down and up move one line, page down and up one page, and the end of the body ends up on the screen."""
    height = app.size.height
    assert scroll.max_scroll_y > 0
    assert "more line" in F.text_of(hint)
    await pilot.press("down")
    assert scroll.scroll_y == 1
    await pilot.press("up")
    assert scroll.scroll_y == 0
    page = max(1, scroll.size.height - 1)
    await pilot.press("pagedown")
    assert scroll.scroll_y == page
    await pilot.press("pageup")
    assert scroll.scroll_y == 0
    for _ in range(80):
        if scroll.scroll_y >= scroll.max_scroll_y:
            break
        await pilot.press("pagedown")
    assert scroll.scroll_y == scroll.max_scroll_y
    assert scroll.scroll_y + scroll.size.height >= scroll.virtual_size.height       # the last line is in view
    assert scroll.region.bottom <= height and scroll.region.height >= 3
    assert "more line" not in F.text_of(hint)
    for button in app.screen.query(Button):
        assert button.region.bottom <= height


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("name,make,scroll_id,hint_id", dialogs(), ids=[d[0] for d in dialogs()])
@F.tui
async def test_a_dialog_taller_than_the_terminal_scrolls_to_its_last_line(name, make, scroll_id, hint_id, size):
    app = NextApp()
    async with app.run_test(size=size) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        dialog = make()
        app.push_screen(dialog)
        await F.settle(pilot, lambda: app.screen is dialog)
        await pilot.pause(0.2)
        await read_to_the_end(pilot, app, dialog.query_one(f"#{scroll_id}"), dialog.query_one(f"#{hint_id}"))


async def first_boot(pilot, app, env):
    s = env.session()
    s.choose("06:00", 100, "gpu", ack=True)
    for step in (3, 4, 5, 6):
        s.complete(step, skipped=step in (4, 5))
    s.complete(7)
    screen = await start(pilot, app, env, session=env.session())
    await mode(pilot, screen, "first_boot")
    return screen


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@F.tui
async def test_the_no_signal_help_scrolls_to_its_last_step(tmp_path, size):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=size) as pilot:
        screen = await first_boot(pilot, app, env)
        await pilot.press("n")
        await mode(pilot, screen, "first_boot")
        scroll, hint = screen.query_one("#g-box-scroll"), screen.query_one("#g-more")
        if size == (80, 24):
            await read_to_the_end(pilot, app, scroll, hint)
        else:                                  # taller: it may fit, and if it does not it scrolls
            for _ in range(20):
                await pilot.press("pagedown")
        assert scroll.scroll_y + scroll.size.height >= scroll.virtual_size.height
        last = F.text_of(screen.query_one("#g-box-text")).rstrip().splitlines()[-1].strip()
        assert last.endswith(("setup.", "again.", ".", "gpu-setup"))             # the real last line of the help
        assert screen.query_one("#g-yes", Button).region.bottom <= app.size.height


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@F.tui
async def test_a_long_run_box_scrolls_to_its_last_line(tmp_path, size):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=size) as pilot:
        screen = await first_boot(pilot, app, env)
        await screen.show_run("Long list", LONG, [("g-ok", "OK", "primary")])
        await pilot.pause(0.3)
        await read_to_the_end(pilot, app, screen.query_one("#g-box-scroll"), screen.query_one("#g-more"))
