"""The live VM screen widget, the bars, the capacity bar and the rows."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from textual.app import App
from textual.containers import VerticalScroll

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.widgets.capacity import CapacityBar
from osx_proxmox_next.widgets.chrome import KeyBar
from osx_proxmox_next.widgets.live_screen import LiveScreen, grab_frame
from osx_proxmox_next.widgets.rows import ChoiceRow, ExpandRow, Option

SIZE = (100, 30)


class Host(App):
    """A bare app that mounts one widget on the osx-next theme."""

    def __init__(self, widget):
        super().__init__()
        from osx_proxmox_next.theme import OSX_NEXT, THEME_NAME
        self.register_theme(OSX_NEXT)
        self.theme = THEME_NAME
        self.widget = widget

    def compose(self):
        yield self.widget


def lines(widget) -> list[str]:
    return [widget.render_line(y).text for y in range(widget.size.height)]


@F.tui
async def test_a_running_vm_is_drawn_with_half_blocks_and_refreshes(monkeypatch):
    frames = []
    monkeypatch.setattr(LiveScreen, "grabber", staticmethod(lambda vmid, probe: frames.append(probe) or F.desktop_ppm(64, 40)))
    live = LiveScreen(105, "running", interval=0.2, id="v")
    app = Host(live)
    async with app.run_test(size=(40, 12)) as pilot:
        await F.settle(pilot, lambda: live.grabs >= 2)
        assert live.placeholder == "" and live.signal
        assert all(ch == "▀" for ch in lines(live)[3])
        assert len(set(frames)) == 1 and "osx-next-view-105-" in frames[0]
        assert live.render_line(0).cell_length == 40
        probe = frames[0]
        assert oct(os.stat(os.path.dirname(probe)).st_mode & 0o777) == "0o700"    # not a guessable file in /tmp
    assert not os.path.exists(os.path.dirname(probe))


@F.tui
async def test_the_picture_is_resampled_when_the_widget_is_resized(monkeypatch):
    monkeypatch.setattr(LiveScreen, "grabber", staticmethod(lambda vmid, probe: F.desktop_ppm(64, 40)))
    live = LiveScreen(105, "running", interval=60)
    app = Host(live)
    async with app.run_test(size=(40, 12)) as pilot:
        await F.settle(pilot, lambda: live.grabs >= 1)
        before = live._cells_size
        await pilot.resize_terminal(60, 20)
        await F.settle(pilot, lambda: live._cells_size != before)
        assert live._cells_size == live.size


@F.tui
async def test_placeholders_for_a_stopped_vm_a_gpu_vm_and_a_failed_grab(monkeypatch):
    monkeypatch.setattr(LiveScreen, "grabber", staticmethod(lambda vmid, probe: b""))
    live = LiveScreen(105, "stopped", interval=60)
    app = Host(live)
    async with app.run_test(size=(40, 11)) as pilot:
        await pilot.pause(0.2)
        assert live.placeholder == "powered off" and live.grabs == 0
        mid = lines(live)[5]
        assert mid.strip() == "powered off" and mid.index("p") == (40 - len("powered off")) // 2
        assert lines(live)[0].strip() == ""
        live.set_state("running", False)
        await F.settle(pilot, lambda: live.grabs >= 1)
        assert live.placeholder == "no signal"
        live.set_state("running", True)
        assert live.placeholder == "screen is on the GPU"
        live.set_state("running", False)
        live.set_state("stopped", False)
        assert live.placeholder == "powered off"


@F.tui
async def test_a_grab_that_raises_never_takes_the_ui_down(monkeypatch):
    def boom(vmid, probe):
        raise RuntimeError("qm exploded")

    monkeypatch.setattr(LiveScreen, "grabber", staticmethod(boom))
    live = LiveScreen(105, "running", interval=60)
    app = Host(live)
    async with app.run_test(size=(40, 11)) as pilot:
        await F.settle(pilot, lambda: live.grabs >= 1)
        assert live.placeholder == "no signal" and app.is_running


@F.tui
async def test_only_a_visible_widget_on_the_active_screen_grabs(monkeypatch):
    calls = []
    monkeypatch.setattr(LiveScreen, "grabber", staticmethod(lambda vmid, probe: calls.append(vmid) or F.desktop_ppm(16, 10)))
    near = LiveScreen(1, "running", interval=60, id="near")
    far = LiveScreen(2, "running", interval=60, id="far")
    near.styles.height = 5
    far.styles.height = 5

    class Scrolling(App):
        def compose(self):
            with VerticalScroll():
                yield near
                from textual.widgets import Static
                yield Static("\n" * 30)
                yield far

    app = Scrolling()
    async with app.run_test(size=(40, 10)) as pilot:
        await F.settle(pilot, lambda: 1 in calls)
        assert near.is_visible_on_screen() and not far.is_visible_on_screen()
        assert 2 not in calls
        far.grab_now()
        await pilot.pause(0.2)
        assert 2 not in calls
        near.display = False
        assert not near.is_visible_on_screen()
        near.display = True
        app.push_screen(__import__("textual.screen", fromlist=["Screen"]).Screen())
        await pilot.pause(0.1)
        assert not near.is_visible_on_screen()                 # a screen on top: not visible
        far.scroll_visible()


def test_a_widget_that_is_not_mounted_is_not_visible():
    assert LiveScreen(1, "running").is_visible_on_screen() is False


def test_grab_frame_uses_the_qm_console_with_its_own_probe(monkeypatch, tmp_path):
    seen = {}

    class Console:
        def __init__(self, vmid, probe_path=None):
            seen.update(vmid=vmid, probe=probe_path)

        def grab(self):
            return b"frame"

    monkeypatch.setattr("osx_proxmox_next.widgets.live_screen.QmConsole", Console)
    assert grab_frame(7, str(tmp_path / "x.ppm")) == b"frame"
    assert seen == {"vmid": 7, "probe": str(tmp_path / "x.ppm")}


@F.tui
async def test_the_capacity_bar_colours_and_text():
    bar = CapacityBar("CPU", "cores")
    app = Host(bar)
    async with app.run_test(size=(40, 4)) as pilot:
        bar.set_values(8, 32, "cores")
        text = bar.render()
        assert text.plain.splitlines()[0].startswith("CPU") and text.plain.splitlines()[0].endswith("8 of 32 cores")
        assert text.plain.splitlines()[1].count("━") == 40
        assert "ok" == bar.state
        bar.set_values(28, 32, "cores")
        assert bar.state == "warn" and bar.summary() == "28 of 32 cores !"
        bar.set_values(40, 32, "cores")
        assert bar.state == "over" and bar.summary().endswith("✗ does not fit")
        bar.set_values(8, 0, "")
        assert bar.state == "unknown" and bar.summary() == "8 cores" and bar.render().plain.count("━") == 40
        bar.set_values(0.1, 1000, "GB")
        assert bar.render().plain.splitlines()[1].count("━") == 40


@F.tui
async def test_choice_rows_skip_disabled_options_and_notify_only_on_change():
    changes = []

    class Rows(Host):
        def __init__(self):
            super().__init__(None)

        def compose(self):
            yield ChoiceRow("Size", [Option("a", "A"), Option("b", "B", False), Option("c", "C")], "a", id="row")
            yield ExpandRow("More", "stuff", id="more")

        def on_choice_row_changed(self, event):
            changes.append(event.value)

    app = Rows()
    async with app.run_test(size=(40, 6)) as pilot:
        row = app.query_one("#row", ChoiceRow)
        row.focus()
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause()
        assert changes == ["c", "a"]
        row.set_options([Option("x", "X"), Option("y", "Y")])          # the old value is gone: the first one wins
        assert row.value == "x"
        row.set_options([Option("x", "X", False), Option("y", "Y", False)])
        assert row.value == "x"
        row.set_value("x", notify=True)
        assert changes == ["c", "a"]
        row.set_options([Option("only", "Only")], "only")
        await pilot.press("right")
        assert row.value == "only" and "‹" not in row.render().plain
        row.set_text("detail", "line one\nline two")
        await pilot.pause()
        assert row.size.height == 3
        assert "detail" in row.render().plain and row.render().plain.count("line") == 2
        more = app.query_one("#more", ExpandRow)
        assert more.render().plain.strip().startswith("▸ More")
        more.set_open(True)
        assert more.render().plain.strip().startswith("▾ More")


@F.tui
async def test_the_footer_keys_are_clickable(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        bar = app.screen.query_one(KeyBar)
        plain = bar.render().plain
        at = plain.index("n New Mac")
        await pilot.click(KeyBar, offset=(at, 0))
        from osx_proxmox_next.screens.new_mac import NewMacScreen
        await F.settle(pilot, lambda: isinstance(app.screen, NewMacScreen))
        at = app.screen.query_one(KeyBar).render().plain.index("? Help")
        await pilot.click(KeyBar, offset=(at, 0))
        from osx_proxmox_next.screens.base import HelpScreen
        await F.settle(pilot, lambda: isinstance(app.screen, HelpScreen))


# ── design round 2, item 6: the private frame folder goes away when a signal ends the app ──

HOOK = textwrap.dedent("""
    import sys, tempfile, time
    sys.path.insert(0, {src!r})
    from osx_proxmox_next.widgets import live_screen as ls
    path = tempfile.mkdtemp(prefix="osx-next-view-999-")
    ls._FRAME_DIRS.add(path)
    ls.install_cleanup()
    print(path, flush=True)
    {tail}
""")
SRC = str(Path(__file__).resolve().parent.parent / "src")


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_a_signal_removes_the_frame_folder_and_the_process_still_ends_by_that_signal(signum):
    proc = subprocess.Popen([sys.executable, "-c", HOOK.format(src=SRC, tail="time.sleep(60)")],
                            stdout=subprocess.PIPE, text=True)
    path = proc.stdout.readline().strip()
    assert os.path.isdir(path)
    proc.send_signal(signum)
    assert proc.wait(timeout=10) == -signum            # it ended the way the signal would have ended it
    assert not os.path.exists(path)


def test_a_hangup_ignored_by_nohup_stays_ignored_and_the_folder_is_kept():
    ignoring = "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
    proc = subprocess.Popen([sys.executable, "-c", HOOK.replace("import sys,", "import signal, sys,")
                             .replace("ls.install_cleanup()", ignoring + "ls.install_cleanup()")
                             .format(src=SRC, tail="time.sleep(1.5)")], stdout=subprocess.PIPE, text=True)
    path = proc.stdout.readline().strip()
    proc.send_signal(signal.SIGHUP)
    assert proc.wait(timeout=10) == 0                  # the hangup was ignored, the process ran to its end
    assert not os.path.exists(path)                     # and the exit hook still cleaned up


def test_the_frame_folder_is_removed_at_interpreter_exit():
    proc = subprocess.run([sys.executable, "-c", HOOK.format(src=SRC, tail="")], capture_output=True, text=True,
                          timeout=20)
    assert proc.returncode == 0 and not os.path.exists(proc.stdout.strip())


def test_remove_frame_dirs_empties_the_registry(tmp_path):
    from osx_proxmox_next.widgets import live_screen as ls
    folder = tmp_path / "osx-next-view-1-x"
    folder.mkdir()
    ls._FRAME_DIRS.add(str(folder))
    ls.remove_frame_dirs()
    assert not folder.exists() and not ls._FRAME_DIRS
