"""The footer never promises a key that does something else: every dialog's enter label against what enter does."""
from __future__ import annotations

import pytest
from textual.widgets import Button, Input

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.gpu_flow import Before
from osx_proxmox_next.screens.base import Choice, ChoiceDialog, ConfirmDialog, HelpScreen
from osx_proxmox_next.screens.dialogs import EditDialog, RemoveDialog, StopDialog
from osx_proxmox_next.screens.gpu import ChangesDialog
from osx_proxmox_next.screens.gpu_before import BeforeDialog
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.widgets.chrome import KeyBar

SIZE = (110, 34)
NO = ("no", "cancel", "close")          # the buttons that do nothing


def enter_label(app) -> str | None:
    items = app.screen.query_one(KeyBar).items
    return next((label for key, label, _a in items if key == "enter"), None)


def kinds() -> list:
    """(name, dialog factory, is it a destructive question). One entry per dialog class of the TUI."""
    rec = F.record(101, "macos-sequoia", "running")
    return [
        ("confirm", lambda: ConfirmDialog("Sure?", ["a line"], confirm="Do it"), False),
        ("confirm-danger", lambda: ConfirmDialog("Sure?", ["a line"], confirm="Remove it", danger=True), True),
        ("stop", lambda: StopDialog(rec), False),
        ("stop-after-a-hang", lambda: StopDialog(rec, force_first=True), False),
        ("choice", lambda: ChoiceDialog("Pick", ["x"], [Choice("a", "Alpha", "primary"), Choice("b", "Beta")]), False),
        ("remove", lambda: RemoveDialog(rec), True),
        ("before", lambda: BeforeDialog(Before(warnings=["Warning: be careful"])), False),
        ("changes", lambda: ChangesDialog("text"), False),
        ("help", lambda: HelpScreen("Home", [("a", "does a")]), False),
    ]


@pytest.mark.parametrize("name,make,destructive", kinds(), ids=[k[0] for k in kinds()])
@F.tui
async def test_the_enter_label_of_a_dialog_is_what_enter_does(name, make, destructive):
    app = NextApp()
    results: list = []
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        dialog = make()
        app.push_screen(dialog, results.append)
        await F.settle(pilot, lambda: app.screen is dialog and app.focused is not None)
        label, focused = enter_label(app), app.focused
        if destructive:                                              # Remove, Put everything back, a danger confirm
            assert getattr(focused, "name", "") in NO or getattr(focused, "id", "") in NO, name
            assert label == "Choose", name                           # never "Confirm" or the destructive action
        if label is None:
            return                                                   # the footer promises nothing
        pressed: list[str] = []
        if isinstance(focused, Button):
            pressed.append(str(focused.label))
        await pilot.press("enter")
        await pilot.pause(0.2)
        if label == "Choose":                                        # enter takes the focused button, whichever it is
            assert isinstance(focused, Button), name
            if isinstance(dialog, ChoiceDialog):
                assert results == [focused.name], name
        elif isinstance(focused, Button):
            assert label.lower() in pressed[0].lower() or pressed[0].lower() in label.lower(), (name, label, pressed)
        assert app.screen is not dialog, name                        # the dialog did what the footer said: it is gone


@F.tui
async def test_the_edit_dialog_footer_says_apply_and_enter_applies():
    app = NextApp()
    rec = F.record(101, "macos-sequoia", "stopped")
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        dialog = EditDialog(rec)
        app.push_screen(dialog)
        await F.settle(pilot, lambda: app.screen is dialog and isinstance(app.focused, Input))
        assert enter_label(app) == "Apply"
        applied = []
        dialog.action_apply = lambda: applied.append(1)              # what enter in a field runs
        await pilot.press("enter")
        assert applied == [1]


@F.tui
async def test_before_you_continue_names_enter_only_while_enter_sets_it_up():
    from osx_proxmox_next.gpu_setup import CONFIRM_PHRASE
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        dialog = BeforeDialog(Before(warnings=["Warning: x"], phrase=True))
        app.push_screen(dialog)
        await F.settle(pilot, lambda: app.screen is dialog and isinstance(app.focused, Input))
        assert enter_label(app) is None                              # a phrase is still to type: enter does nothing
        await pilot.press("enter")
        assert app.screen is dialog
        dialog.query_one("#g-confirm", Input).value = CONFIRM_PHRASE
        await pilot.pause(0.2)
        assert enter_label(app) == "Set it up"
        await pilot.press("enter")
        await F.settle(pilot, lambda: app.screen is not dialog)
