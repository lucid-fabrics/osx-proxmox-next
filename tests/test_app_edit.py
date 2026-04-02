"""Tests for the Edit VM panel (EditModeMixin) in the manage tab."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from textual.widgets import Button, Checkbox, Input, Static

from osx_proxmox_next.app import NextApp
from osx_proxmox_next.executor import ApplyResult
from osx_proxmox_next.services import edit_service


# ── Helpers ──────────────────────────────────────────────────────────


async def _advance_to_manage(pilot, app) -> None:
    """Advance to step 2 and switch to Manage tab."""
    app.state.preflight_done = True
    app.state.preflight_ok = True
    app.query_one("#preflight_next_btn", Button).disabled = False
    await pilot.click("#preflight_next_btn")
    await pilot.pause()
    await pilot.click("#mode_manage")
    await pilot.pause()


# ── Edit VMID validation ──────────────────────────────────────────────


def test_edit_form_hidden_until_valid_vmid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            # edit_form should start hidden
            assert app.query_one("#edit_form").has_class("hidden")
            # invalid vmid keeps form hidden
            app.query_one("#edit_vmid", Input).value = "abc"
            await pilot.pause()
            assert app.query_one("#edit_form").has_class("hidden")

    asyncio.run(_run())


def test_edit_form_shows_on_valid_vmid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "900"
            await pilot.pause()
            assert not app.query_one("#edit_form").has_class("hidden")

    asyncio.run(_run())


def test_edit_apply_btn_disabled_without_any_field() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "900"
            await pilot.pause()
            # No fields filled → button disabled
            assert app.query_one("#edit_apply_btn", Button).disabled is True

    asyncio.run(_run())


def test_edit_apply_btn_enabled_with_one_field() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "900"
            await pilot.pause()
            app.query_one("#edit_cores", Input).value = "4"
            await pilot.pause()
            assert app.query_one("#edit_apply_btn", Button).disabled is False

    asyncio.run(_run())


def test_edit_vmid_out_of_range_keeps_form_hidden() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "5"
            await pilot.pause()
            assert app.query_one("#edit_form").has_class("hidden")

    asyncio.run(_run())


# ── Validation rejection via _run_edit ───────────────────────────────


def test_edit_run_edit_blocked_while_running() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.state.edit_running = True
            # should silently return without crashing
            app._run_edit()

    asyncio.run(_run())


def test_edit_run_edit_invalid_vmid_noop() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "abc"
            await pilot.pause()
            app._run_edit()  # should not raise

    asyncio.run(_run())


def test_edit_run_edit_shows_validation_errors() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "900"
            await pilot.pause()
            # bad cores value — validation should reject
            app.query_one("#edit_cores", Input).value = "1"  # below MIN_CORES
            await pilot.pause()
            app._run_edit()
            await pilot.pause()
            result_box = app.query_one("#edit_result", Static)
            assert not result_box.has_class("hidden")
            assert result_box.has_class("edit_result_fail")

    asyncio.run(_run())


# ── Start-after checkbox ──────────────────────────────────────────────


def test_edit_start_after_checkbox_updates_state() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            # Enter valid VMID so the edit form (and checkbox) become visible
            app.query_one("#edit_vmid", Input).value = "900"
            await pilot.pause()
            assert app.state.edit_start_after is False
            await pilot.click("#edit_start_after_cb")
            await pilot.pause()
            assert app.state.edit_start_after is True
            await pilot.click("#edit_start_after_cb")
            await pilot.pause()
            assert app.state.edit_start_after is False

    asyncio.run(_run())


# ── Edit success / failure via monkeypatched edit_service ────────────


def test_edit_apply_success(monkeypatch) -> None:
    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)

                class _R:
                    ok = True
                    returncode = 0

                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/edit.log"))

    monkeypatch.setattr(edit_service, "apply_plan", fake_apply_plan)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "900"
            await pilot.pause()
            app.query_one("#edit_cores", Input).value = "4"
            await pilot.pause()
            await pilot.click("#edit_apply_btn")
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.edit_done:
                    break
            assert app.state.edit_ok is True
            result_text = str(app.query_one("#edit_result", Static).content)
            assert "Changes applied" in result_text

    asyncio.run(_run())


def test_edit_apply_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        edit_service,
        "apply_plan",
        lambda steps, execute=False, on_step=None, adapter=None: ApplyResult(
            ok=False, results=[], log_path=Path("/tmp/fail.log")
        ),
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_vmid", Input).value = "900"
            await pilot.pause()
            app.query_one("#edit_memory", Input).value = "8192"
            await pilot.pause()
            await pilot.click("#edit_apply_btn")
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.edit_done:
                    break
            assert app.state.edit_ok is False
            result_text = str(app.query_one("#edit_result", Static).content)
            assert "Failed" in result_text

    asyncio.run(_run())


# ── _update_edit_log ─────────────────────────────────────────────────


def test_edit_update_log_before_result() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_log").remove_class("hidden")
            app._update_edit_log(1, 2, "Stop VM", None)
            log_text = str(app.query_one("#edit_log", Static).content)
            assert "Running 1/2: Stop VM" in log_text

    asyncio.run(_run())


def test_edit_update_log_with_ok_result() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.query_one("#edit_log").remove_class("hidden")

            class _R:
                ok = True

            app._update_edit_log(1, 1, "Set cores", _R())
            log_text = str(app.query_one("#edit_log", Static).content)
            assert "OK 1/1: Set cores" in log_text

    asyncio.run(_run())


# ── _finish_edit ─────────────────────────────────────────────────────


def test_edit_finish_edit_success_clears_running() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.state.edit_running = True
            app._finish_edit(ok=True, log_path=Path("/tmp/ok.log"))
            await pilot.pause()
            assert app.state.edit_running is False
            assert app.state.edit_ok is True
            result_box = app.query_one("#edit_result", Static)
            assert not result_box.has_class("hidden")
            assert not result_box.has_class("edit_result_fail")

    asyncio.run(_run())


def test_edit_finish_edit_failure_marks_fail() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            await _advance_to_manage(pilot, app)
            app.state.edit_running = True
            app._finish_edit(ok=False, log_path=Path("/tmp/fail.log"))
            await pilot.pause()
            assert app.state.edit_ok is False
            result_box = app.query_one("#edit_result", Static)
            assert result_box.has_class("edit_result_fail")

    asyncio.run(_run())


# ── WizardState edit defaults ────────────────────────────────────────


def test_wizard_state_edit_defaults() -> None:
    from osx_proxmox_next.models import WizardState
    state = WizardState()
    assert state.edit_running is False
    assert state.edit_done is False
    assert state.edit_ok is False
    assert state.edit_log == []
    assert state.edit_start_after is False
