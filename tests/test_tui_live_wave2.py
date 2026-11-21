"""The second live run: scrolling in See every change, a retry that shows itself, the undo list and its progress,
the real error on the failed screen, and the first-boot help at 100 columns."""
from __future__ import annotations

import threading

import tui_fakes as F
from gpu_tui_fakes import (  # noqa: F401 - the underscore names are autouse fixtures
    SIZE, _no_network, _vm_list, amd_env, applied, at_choose, box, dialog_text, mode, timeline)
from osx_proxmox_next import gpu, gpu_setup
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.executor import ApplyResult, StepResult
from osx_proxmox_next.gpu_setup import TROUBLESHOOTING
from osx_proxmox_next.screens.gpu import ChangesDialog, undo_progress_text
from osx_proxmox_next.screens.gpu_attach_step import failure_lines
from osx_proxmox_next.screens.gpu_boot import HELP_WIDTH, reflow_help
from osx_proxmox_next.screens.gpu_run import Guidance


@F.tui
async def test_every_change_scrolls_one_line_or_one_page_per_key(tmp_path):
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        app.push_screen(ChangesDialog("\n".join(f"line {i}" for i in range(200))))
        await F.settle(pilot, lambda: isinstance(app.screen, ChangesDialog))
        scroll = app.screen.query_one("#changes-scroll")
        await pilot.pause(0.2)
        for _ in range(5):
            await pilot.press("down")
        assert scroll.scroll_y == 5                                      # one line per press, not one per many
        await pilot.press("up")
        assert scroll.scroll_y == 4
        await pilot.press("pagedown")
        page = scroll.scroll_y - 4
        assert 10 < page <= scroll.size.height                         # a page, not a line
        await pilot.press("end")
        assert scroll.scroll_y == scroll.max_scroll_y > 100
        await pilot.press("pageup")
        assert scroll.scroll_y == scroll.max_scroll_y - page
        await pilot.press("home")
        assert scroll.scroll_y == 0
        assert not app.screen.query_one("#changes-text").size.width > scroll.size.width     # nothing past the edge


@F.tui
async def test_a_retry_shows_the_running_step_at_once_not_the_old_failure(tmp_path, applied):
    env = amd_env(tmp_path)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        started = threading.Event()
        screen._guidance(Guidance("Attaching failed", ["Log: /x"], "Try again", started.set,
                                  fail=("restart", "Attaching failed")))
        await F.settle(pilot, lambda: box(screen).startswith("Attaching failed"))
        assert "✗ Attaching failed" in timeline(screen)
        await pilot.press("enter")
        await F.settle(pilot, started.is_set)
        await F.settle(pilot, lambda: box(screen).startswith("Working on it"))
        text = timeline(screen)
        assert "◐ " in text and "✗" not in text and "Attaching failed" not in text and not screen.query("#g-primary-run")


def result(output: str, ok: bool = False) -> ApplyResult:
    return ApplyResult(ok, [StepResult("Prepare OpenCore", "cmd", ok, 1, output)], "generated/logs/x.log")


def test_the_failed_screen_shows_the_real_error_line_and_the_log():
    out = "mounting\nERROR: could not activate the disk /dev/pve/vm-105-disk-2\nlvchange: failed\n"
    lines = failure_lines(result(out))
    assert lines == ["Prepare OpenCore failed:", "ERROR: could not activate the disk /dev/pve/vm-105-disk-2",
                     "Log: generated/logs/x.log"]
    assert failure_lines(result("a\nb\nc\n")) == ["Prepare OpenCore failed:", "b", "c", "Log: generated/logs/x.log"]
    assert failure_lines(ApplyResult(False, [], "l.log")) == ["Log: l.log"]


def test_every_change_undo_makes_has_a_line_from_the_record_undo_reads(tmp_path):
    from test_gpu_boot_holders import NamedQm
    from test_gpu_setup import SEQUOIA, Env
    env = Env(tmp_path)
    env.qm = NamedQm({105: ("stopped", SEQUOIA)})
    from gpu_fakes import add_card
    add_card(env.root, device=0x73BF)
    s = env.session()
    s.macos_vms = lambda: ["VMID NAME STATUS", "105 macos-sequoia stopped"]
    gpu.record_path(105).parent.mkdir(parents=True, exist_ok=True)
    gpu.record_path(105).write_text('{"vmid": 105, "bdf": "0000:06:00", "vendor": "amd", "oc": "gpu"}')
    s.state["vm_records"] = [105]
    assert s.undo_plan() == ["Take agdpmod=pikera out of macos-sequoia's OpenCore settings",
                             "Take the card off macos-sequoia (VM 105)"]
    gpu.record_path(105).write_text('{"vmid": 105, "bdf": "0000:06:00", "vendor": "intel", "oc": "igpu"}')
    assert s.undo_plan()[0] == "Restore macos-sequoia's OpenCore settings"


def test_undo_tells_its_progress_before_each_mac_and_the_files(tmp_path):
    from test_gpu_boot_holders import NamedQm
    from test_gpu_setup import SEQUOIA, Env
    env = Env(tmp_path)
    env.qm = NamedQm({105: ("stopped", SEQUOIA)})
    s = env.session()
    gpu.record_path(105).parent.mkdir(parents=True, exist_ok=True)
    gpu.record_path(105).write_text('{"vmid": 105, "bdf": "0000:06:00", "vendor": "amd", "oc": "gpu"}')
    s.state["vm_records"] = [105]
    seen: list[int] = []
    s.undo(progress=seen.append)
    assert seen[0] == 0 and seen[1] == 2 and seen == sorted(seen)          # the Mac's two lines, then the files


def test_the_undo_progress_text_marks_done_current_and_waiting():
    plan = ["Take the card off X (VM 1)", "Remove /a", "Restore /b", "The host needs one restart afterwards."]
    assert undo_progress_text(plan, 1) == "Undoing...\n\n✓ Take the card off X (VM 1)\n◐ Remove /a\n  Restore /b"
    assert undo_progress_text([], 0) == "Undoing..."


@F.tui
async def test_the_undo_dialog_moves_line_by_line_while_it_runs(tmp_path, applied, monkeypatch):
    env = amd_env(tmp_path)
    gate, reached = threading.Event(), threading.Event()

    def slow_undo(self, allow_stop=False, progress=None):
        progress(1)
        reached.set()
        gate.wait(10)
        return True, ["Removed /x"]

    monkeypatch.setattr(gpu_setup.Session, "undo", slow_undo)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await at_choose(pilot, app, env)
        await pilot.press("enter")
        await mode(pilot, screen, "run")
        await pilot.press("u")
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, reached.is_set)
        await F.settle(pilot, lambda: "✓ Remove /etc/" in box(screen))
        text = box(screen)
        assert text.startswith("Undoing") or "Undoing..." in text
        assert "◐ Remove /etc/" in text
        gate.set()
        await F.settle(pilot, lambda: not screen.working)


def test_the_no_signal_help_wraps_under_each_number_at_the_box_width():
    text = reflow_help(TROUBLESHOOTING["no_signal"])
    lines = text.split("\n")
    assert all(len(ln) <= HELP_WIDTH for ln in lines)
    starts = [ln for ln in lines if ln.lstrip()[:2] in ("1.", "2.", "3.", "4.")]
    assert len(starts) == 4 and all(ln.startswith("  ") for ln in starts)
    assert all(ln.startswith("     ") for ln in lines if ln not in starts and ln != lines[0])   # hanging indent
