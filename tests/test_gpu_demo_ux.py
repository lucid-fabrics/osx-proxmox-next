"""Walkthrough polish: window title, display choice, careful prefix, rail state, button width, BIOS card
layout and the finish screen. TUI, CLI and bash twins."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
from textual.widgets import Button, Select

sys.path.insert(0, str(Path(__file__).parent))
from gpu_fakes import add_card  # noqa: E402
from osx_proxmox_next import bios_guide, gpu_setup  # noqa: E402
from osx_proxmox_next.app import NextApp  # noqa: E402
from osx_proxmox_next.gpu_setup import NEXT_AMD, NEXT_NVIDIA, NULLMOTH_DOC  # noqa: E402
from test_gpu_panel import (  # noqa: E402,F401
    _vm_list, amd_env, open_panel, press, settle, shown, text)
from test_gpu_setup import Env, chosen  # noqa: E402
from test_gpu_parity import BASH, bash, block_file  # noqa: E402,F401

DATA = bios_guide.load_data()
FACTS = {"board_vendor": "ASUSTeK COMPUTER INC.", "board_name": "PRIME Z790-P", "cpu_vendor": "intel"}
FAILING = [{"key": "above4g", "ok": False, "detail": ""}]
QR = bios_guide.qr_lines(bios_guide.doc_url(DATA, "asus"))  # 41 columns: the frame is 120 wide with it beside


def run(coro_fn):
    asyncio.run(coro_fn())


def test_the_window_title_is_the_project_name():
    async def go():
        app = NextApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.title == "osx-proxmox-next"
    run(go)


# ── BIOS card: QR beside the steps when it fits, under them when it does not ──

def _card(max_width):
    return bios_guide.build_card(FAILING, FACTS, DATA, "asus", qr=QR, max_width=max_width)


def _qr_rows_with_steps(text):
    rows = [r for r in QR if r.strip()]
    return [ln for ln in text.splitlines() if any(r in ln for r in rows) and re.search(r"[A-Za-z]{3}", ln)]


@pytest.mark.parametrize("max_width,beside", [(None, True), (160, True), (120, True), (119, False), (100, False), (79, False)])
def test_the_qr_goes_under_the_steps_when_there_is_no_room_beside_them(max_width, beside):
    text = _card(max_width)
    lines = text.splitlines()
    assert len({len(ln) for ln in lines}) == 1  # one frame, every row the same width
    assert max_width is None or len(lines[0]) <= max_width
    rows = [r for r in QR if r.strip()]
    assert all(any(r in ln for ln in lines) for r in rows)  # the whole code is inside the frame
    assert bool(_qr_rows_with_steps(text)) is beside
    assert all(ln.startswith("| ") and ln.endswith(" |") for ln in lines[1:-1])


def test_no_qr_means_no_gap_and_a_plain_card():
    plain = bios_guide.build_card(FAILING, FACTS, DATA, "asus", max_width=100)
    assert len(plain.splitlines()[0]) <= 100 and "Same steps with pictures" in plain


def test_the_engine_command_takes_max_width():
    import subprocess
    payload = {"facts": FACTS, "items": FAILING and [{"key": "above4g", "ok": False, "detail": ""}], "qr": QR, "max_width": 90}
    out = subprocess.run([sys.executable, str(Path(bios_guide.__file__)), "card"], input=json.dumps(payload),
                         capture_output=True, text=True, check=True).stdout
    assert len(out.splitlines()[0]) <= 90 and not _qr_rows_with_steps(out)


def test_the_panel_card_follows_the_terminal_width(tmp_path):
    async def card_at(cols):
        env = Env(tmp_path / str(cols), virt=False, tables=(), efi=False)
        add_card(env.root)
        app = NextApp()
        async with app.run_test(size=(cols, 70)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "Works natively" in text(app, "#gp_body"))
            app.query_one("#gp_gpu_select", Select).value = "0000:06:00"
            await pilot.pause()
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 3" in text(app, "#gp_title"))
            return app._gp_card["text"], app._gp_width()

    narrow, width = asyncio.run(card_at(100))
    assert not _qr_rows_with_steps(narrow) and max(len(ln) for ln in narrow.splitlines()) <= width
    wide, _ = asyncio.run(card_at(170))
    assert _qr_rows_with_steps(wide)


# ── display choice ──

def test_the_display_note_is_for_nvidia_and_the_radios_for_amd(tmp_path):
    env = amd_env(tmp_path)
    add_card(env.root, bdf="0000:01:00", vendor=0x10DE, device=0x2504, audio=0x228E, group=3)

    async def go():
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "Works natively" in text(app, "#gp_body"))
            app.query_one("#gp_gpu_select", Select).value = "0000:06:00"
            await pilot.pause()
            assert shown(app, "#gp_display_radio") and not shown(app, "#gp_display_note")
            app.query_one("#gp_gpu_select", Select).value = "0000:01:00"
            await pilot.pause()
            assert not shown(app, "#gp_display_radio") and shown(app, "#gp_display_note")
            assert text(app, "#gp_display_note").startswith("NVIDIA: the Proxmox console goes black")
    run(go)


# ── "Careful", not "Refused", where typing a phrase overrides ──

def test_only_display_adapter_is_careful_not_refused(tmp_path):
    env = Env(tmp_path)
    add_card(env.root, boot_vga=True)

    async def go():
        app = NextApp()
        async with app.run_test(size=(120, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.choose("06:00", 100, "gpu")
            s.complete(3, skipped=True)
            app._gp = s
            from test_app_wizard import _advance_to_step
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "only display adapter" in text(app, "#gp_body"))
            body = text(app, "#gp_body")
            assert "Careful: This card is the only display adapter" in body and "Refused:" not in body
    run(go)


def test_bash_says_careful_for_the_typed_override_and_keeps_refused_otherwise():
    assert 'gpu_say "Careful: $refusals"; else gpu_say "REFUSED: $refusals"' in BASH


# ── rail: a passing BIOS check is done, never skipped ──

def test_a_passing_bios_check_shows_done_on_the_rail(tmp_path):
    env = amd_env(tmp_path)

    async def go():
        app = NextApp()
        async with app.run_test(size=(120, 70)) as pilot:
            await pilot.pause()
            s = env.session()
            s.choose("06:00", 100, "gpu")
            app._gp = s
            from test_app_wizard import _advance_to_step
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await settle(pilot, lambda: "Step 4" in text(app, "#gp_title"))
            assert 3 in s.state["done"] and 3 not in s.state["skipped"]
            assert "skipped" not in text(app, "#gp_rail")
    run(go)


def test_bash_marks_a_passing_bios_check_done():
    assert 'gpu_say "Nothing to change in the BIOS."\n      gpu_step_complete 3\n' in BASH


# ── a new label gets a new width ──

def test_a_button_grows_with_its_label():
    async def go():
        app = NextApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            from test_app_wizard import _advance_to_step
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_gpu")
            await pilot.pause()
            btn = app.query_one("#gp_primary_btn", Button)
            btn.label = "Attach the card now"
            await pilot.pause()
            assert btn.outer_size.width >= len("Attach the card now") + 2
            btn.label = "Go"
            await pilot.pause()
            assert btn.outer_size.width < len("Attach the card now")
    run(go)


# ── finish screen ──

def test_the_summary_names_the_vm_and_says_what_comes_next(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: ["VMID NAME STATUS", "106 tahoe-gpu stopped"])
    from test_gpu_setup import SEQUOIA
    env = Env(tmp_path, vms={106: ("stopped", SEQUOIA)})
    add_card(env.root)
    s = chosen(env, vmid=106)
    text_ = s.summary()
    assert "  VM: 106 tahoe-gpu" in text_ and NEXT_AMD in text_ and "Next, in macOS" not in text_
    s.state["gpu"]["vendor"] = "nvidia"
    text_ = s.summary()
    assert NEXT_NVIDIA in text_ and NULLMOTH_DOC in text_ and "We do not ship or support the driver." in text_
    s.state["vm"] = 999  # not in the list: the id alone
    assert re.search(r"VM: 999$", s.summary(), re.M)
    monkeypatch.setattr(gpu_setup, "list_macos_vms", lambda: (_ for _ in ()).throw(OSError("no qm")))
    assert re.search(r"VM: 999$", s.summary(), re.M)


def test_bash_summary_matches(bash, tmp_path):
    from osx_proxmox_next import host_setup
    state = {**host_setup.new_state(), "vm": 106, "gpu": {
        "bdf": "0000:01:00", "vendor": "nvidia", "device": 1, "boot_vga": False, "name": "RTX 3060", "identity": None}}
    snippet = ("qm() { echo 'name: tahoe-gpu'; }\ngpu_state_save \"$STATE\"\ngpu_summary")
    out = bash(snippet, env={"STATE": json.dumps(state), "GPU_ROOT": str(tmp_path / "root")}).stdout
    assert "  VM: 106 tahoe-gpu" in out and NEXT_NVIDIA in out and "  Card: RTX 3060 at 0000:01:00" in out
    state["gpu"]["vendor"] = "amd"
    out = bash(snippet, env={"STATE": json.dumps(state), "GPU_ROOT": str(tmp_path / "root")}).stdout
    assert NEXT_AMD in out and "Next, in macOS" not in out


def test_a_new_screen_starts_at_its_top(tmp_path):
    env = Env(tmp_path, virt=False, tables=(), efi=False)
    add_card(env.root)

    async def go():
        app = NextApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await open_panel(pilot, app, env)
            await settle(pilot, lambda: "Works natively" in text(app, "#gp_body"))
            panel = app.query_one("#gpu_panel")
            panel.scroll_to(y=panel.max_scroll_y, animate=False, immediate=True, force=True)
            await pilot.pause()
            assert panel.scroll_y > 0
            await press(pilot, app, "#gp_primary_btn")
            await settle(pilot, lambda: "Step 3" in text(app, "#gp_title"))
            await pilot.pause(0.2)
            assert panel.scroll_y == 0
    run(go)
