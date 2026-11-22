"""scripts/demo/run_demo.py: the fake-host launcher behind the README demo.

It runs the real NextApp, so the walk below is the one tui.tape records. The point of the test is the promise the
launcher makes: it never starts a process (no qm, no pvesh) and never writes outside its temp directory."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.screens.build import BuildScreen
from osx_proxmox_next.screens.gpu import GpuScreen
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.screens.new_mac import NewMacScreen

DEMO_DIR = Path(__file__).resolve().parents[1] / "scripts" / "demo"
sys.path.insert(0, str(DEMO_DIR))
import png_ppm  # noqa: E402
import run_demo  # noqa: E402


@pytest.fixture
def demo(monkeypatch, tmp_path):
    host = run_demo.install(monkeypatch, tmp_path, phase_seconds=0.02)
    attempts: list = []

    def refuse(*args, **kwargs):
        attempts.append(args[0] if args else kwargs)
        raise AssertionError("the demo started a process")

    # the launcher refuses processes itself: this second guard is the test's own, so a regression in that one shows
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr("os.system", refuse)
    host.attempts = attempts
    return host


def text(app, selector: str) -> str:
    return F.text_of(app.screen.query_one(selector))


@F.tui
async def test_the_tape_walk_runs_on_the_fake_host_without_starting_a_process(demo, tmp_path):
    app = NextApp()
    async with app.run_test(size=run_demo.SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and len(app.screen.records) == 2)
        assert [r.name for r in app.screen.records] == ["macos-sequoia", "tahoe-studio"]
        assert app.host_label == "pve"

        await pilot.press("n")                                    # New Mac, a lighter size, create
        await F.settle_screen(pilot, app, NewMacScreen)
        await pilot.press("right", "down", "down", "left", "enter")
        await F.settle_screen(pilot, app, BuildScreen)
        await F.settle(pilot, lambda: app.screen.pipe.phase("install").status == "active", what="the build to reach Install")
        assert "Installing macOS" in text(app, "#timeline")
        assert [r.name for r in demo.vms] == ["macos-sequoia", "tahoe-studio", "macos-sonoma"]

        await pilot.press("escape")                               # the build goes on, on the card
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and len(app.screen.records) == 3)

        await pilot.press("g")                                    # graphics card: choose, every change, approve, run
        await F.settle_screen(pilot, app, GpuScreen)
        await F.settle(pilot, lambda: app.screen.mode == "choose" and app.screen.preview is not None)
        assert "Radeon RX 6800" in text(app, "#g-card")
        await pilot.press("p")
        await F.settle_query(pilot, app, "#changes-text")
        await pilot.press("escape", "enter")
        await F.settle_query(pilot, app, "#before-box")
        await pilot.press("enter")
        await F.settle(pilot, lambda: app.screen.mode == "run" and not app.screen.working)
        await F.settle_text(pilot, lambda: text(app, "#g-box-title"), "Time for the one restart")

    assert demo.spawned == [] and demo.attempts == []
    assert str(demo.env.root).startswith(str(tmp_path))          # the fake host tree lives in the temp dir


def test_png_ppm_hands_the_apps_frame_reader_the_picture(tmp_path):
    import struct
    import zlib

    from osx_proxmox_next.widgets.ppm import parse_ppm

    def png(color_type: int, rows: list[bytes]) -> bytes:
        raw = b"".join(b"\x00" + row for row in rows)
        chunk = lambda kind, body: struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        head = struct.pack(">IIBBBBB", len(rows[0]) // (3 if color_type == 2 else 4), len(rows), 8, color_type, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    rgba = tmp_path / "a.png"
    rgba.write_bytes(png(6, [bytes((10, 20, 30, 255, 40, 50, 60, 255)), bytes((70, 80, 90, 255, 1, 2, 3, 255))]))
    ppm = png_ppm.png_to_ppm(rgba)
    frame = parse_ppm(ppm)
    assert (frame.width, frame.height) == (2, 2) and ppm.endswith(bytes((10, 20, 30, 40, 50, 60, 70, 80, 90, 1, 2, 3)))

    thin = png_ppm.png_to_ppm(rgba, max_width=1)                 # thinned to one pixel across
    assert parse_ppm(thin).width == 1

    other = tmp_path / "b.png"
    other.write_bytes(b"not a png")
    with pytest.raises(ValueError):
        png_ppm.png_to_ppm(other)
