import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Button, Checkbox, Input, Static

from osx_proxmox_next import app as app_module
from osx_proxmox_next.app import NextApp, WizardState
from osx_proxmox_next.executor import ApplyResult
from osx_proxmox_next.planner import PlanStep


# ── Helper ──────────────────────────────────────────────────────────

async def _advance_to_step(pilot, app, target_step, monkeypatch=None):
    """Advance the wizard to the given step by selecting defaults.

    Step layout (6-step wizard):
      1 = Preflight
      2 = OS / Manage VMs
      3 = Storage
      4 = Config
      5 = Review & Dry Run
      6 = Install
    """
    # Step 1 → 2: pass preflight
    if target_step >= 2:
        app.state.preflight_done = True
        app.state.preflight_ok = True
        app.query_one("#preflight_next_btn", Button).disabled = False
        await pilot.click("#preflight_next_btn")
        await pilot.pause()
    # Step 2 → 3: select OS
    if target_step >= 3:
        await pilot.click("#os_sequoia")
        await pilot.pause()
        await pilot.click("#next_btn")
        await pilot.pause()
    # Step 3 → 4: accept default storage
    if target_step >= 4:
        await pilot.click("#next_btn_3")
        await pilot.pause()
    # Step 4 → 5: validate config
    if target_step >= 5:
        if monkeypatch:
            monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])
            monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])
        await pilot.click("#next_btn_4")
        await pilot.pause()


# ── Navigation Tests ────────────────────────────────────────────────

def test_wizard_starts_at_step1() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            assert app.current_step == 1
            assert not app.query_one("#step1").has_class("step_hidden")
            assert app.query_one("#step2").has_class("step_hidden")

    asyncio.run(_run())


def test_next_blocked_without_preflight() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            assert app.query_one("#preflight_next_btn", Button).disabled is True
            app._go_next()
            assert app.current_step == 1

    asyncio.run(_run())


def test_forward_backward_navigation() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            # Pass preflight → step 2
            app.state.preflight_done = True
            app.state.preflight_ok = True
            app.query_one("#preflight_next_btn", Button).disabled = False
            await pilot.click("#preflight_next_btn")
            await pilot.pause()
            assert app.current_step == 2
            # Select OS → step 3
            await pilot.click("#os_sequoia")
            await pilot.pause()
            await pilot.click("#next_btn")
            await pilot.pause()
            assert app.current_step == 3
            # Back → step 2
            await pilot.click("#back_btn_3")
            await pilot.pause()
            assert app.current_step == 2

    asyncio.run(_run())


def test_back_at_step1_stays() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app._go_back()
            assert app.current_step == 1

    asyncio.run(_run())


def test_step3_next_without_storage_blocked() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 3)
            app.state.selected_storage = ""
            app._go_next()
            assert app.current_step == 3

    asyncio.run(_run())


def test_step5_next_requires_dry_run() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            # Force to step 5 directly
            app.current_step = 5
            await pilot.pause()
            app.state.dry_run_ok = False
            app._go_next()
            assert app.current_step == 5

    asyncio.run(_run())


def test_step_bar_updates() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            bar_text = app.query_one("#step_bar", Static).content
            assert "[>] 1.Preflight" in bar_text
            # Pass preflight → step 2
            app.state.preflight_done = True
            app.state.preflight_ok = True
            app.query_one("#preflight_next_btn", Button).disabled = False
            await pilot.click("#preflight_next_btn")
            await pilot.pause()
            bar_text = app.query_one("#step_bar", Static).content
            assert "[>] 2.OS" in bar_text
            assert "[x] 1.Preflight" in bar_text

    asyncio.run(_run())


def test_step_visibility_toggles() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.current_step = 4
            await pilot.pause()
            assert not app.query_one("#step4").has_class("step_hidden")
            assert app.query_one("#step1").has_class("step_hidden")
            assert app.query_one("#step2").has_class("step_hidden")
            assert app.query_one("#step3").has_class("step_hidden")
            assert app.query_one("#step5").has_class("step_hidden")
            assert app.query_one("#step6").has_class("step_hidden")

    asyncio.run(_run())


# ── Step 1: Preflight ─────────────────────────────────────────────

def test_preflight_step_blocks_until_ok() -> None:
    """Step 1 Continue button is disabled when checks fail."""
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            checks = [
                PreflightCheck("qm available", False, "not found"),
            ]
            app._finish_preflight(checks)
            assert app.query_one("#preflight_next_btn", Button).disabled is True
            app._go_next()
            assert app.current_step == 1

    asyncio.run(_run())


def test_preflight_step_enables_on_success() -> None:
    """Step 1 Continue enables when all checks pass."""
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            checks = [
                PreflightCheck("qm available", True, "/usr/sbin/qm"),
                PreflightCheck("Root privileges", True, "uid=0"),
            ]
            app._finish_preflight(checks)
            assert app.query_one("#preflight_next_btn", Button).disabled is False

    asyncio.run(_run())


def test_rerun_preflight() -> None:
    """_rerun_preflight resets state and re-disables button."""
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            # First complete preflight successfully
            checks = [PreflightCheck("qm available", True, "/usr/sbin/qm")]
            app._finish_preflight(checks)
            assert app.state.preflight_ok is True
            assert app.query_one("#preflight_next_btn", Button).disabled is False
            # Rerun should reset
            app._rerun_preflight()
            assert app.state.preflight_done is False
            assert app.state.preflight_ok is False
            assert app.query_one("#preflight_next_btn", Button).disabled is True

    asyncio.run(_run())


# ── Step 2: OS Selection ────────────────────────────────────────────

def test_select_os_sonoma() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#os_sonoma")
            await pilot.pause()
            assert app.state.selected_os == "sonoma"
            assert app.state.smbios is not None
            assert app.state.smbios.model == "iMacPro1,1"
            assert app.query_one("#os_sonoma").has_class("os_selected")
            assert not app.query_one("#os_sequoia").has_class("os_selected")

    asyncio.run(_run())


def test_select_os_sequoia() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#os_sequoia")
            await pilot.pause()
            assert app.state.selected_os == "sequoia"
            assert app.query_one("#next_btn", Button).disabled is False

    asyncio.run(_run())


def test_select_os_tahoe() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#os_tahoe")
            await pilot.pause()
            assert app.state.selected_os == "tahoe"
            assert app.state.smbios.model == "MacPro7,1"

    asyncio.run(_run())


def test_switch_os_deselects_previous() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#os_sonoma")
            await pilot.pause()
            assert app.query_one("#os_sonoma").has_class("os_selected")
            await pilot.click("#os_tahoe")
            await pilot.pause()
            assert not app.query_one("#os_sonoma").has_class("os_selected")
            assert app.query_one("#os_tahoe").has_class("os_selected")

    asyncio.run(_run())


def test_os_invalid_key_ignored() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            # Simulate button with unknown os key
            class FakeEvent:
                class button:
                    id = "os_bogus"
            app.on_button_pressed(FakeEvent())
            assert app.state.selected_os == ""

    asyncio.run(_run())


def test_next_blocked_without_os_selection() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            assert app.query_one("#next_btn", Button).disabled is True
            app._go_next()
            assert app.current_step == 2

    asyncio.run(_run())


# ── Step 3: Storage Selection ───────────────────────────────────────

def test_storage_preselected() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            assert app.state.selected_storage == app.state.storage_targets[0]

    asyncio.run(_run())


def test_storage_click_selects() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 3)
            assert app.current_step == 3
            await pilot.click("#storage_0")
            await pilot.pause()
            assert app.state.selected_storage == app.state.storage_targets[0]

    asyncio.run(_run())


def test_storage_invalid_index_ignored() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            class FakeEvent:
                class button:
                    id = "storage_999"
            app.on_button_pressed(FakeEvent())
            # No crash, storage unchanged

    asyncio.run(_run())


def test_storage_non_numeric_ignored() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            class FakeEvent:
                class button:
                    id = "storage_abc"
            app.on_button_pressed(FakeEvent())

    asyncio.run(_run())


def test_storage_selection_updates_buttons() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 3)
            if len(app.state.storage_targets) >= 2:
                await pilot.click("#storage_1")
                await pilot.pause()
                assert app.query_one("#storage_1").has_class("storage_selected")
                assert not app.query_one("#storage_0").has_class("storage_selected")

    asyncio.run(_run())


def test_detect_storage_fallback() -> None:
    async def _run() -> None:
        app = NextApp()
        targets = app.state.storage_targets
        assert len(targets) >= 1
        assert targets[0] in ("local-lvm", "local")

    asyncio.run(_run())


# ── Step 4: Configuration ──────────────────────────────────────────

def test_prefill_form_on_step3_next() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            assert app.query_one("#name", Input).value == "macos-sequoia"
            assert app.query_one("#storage_input", Input).value != ""

    asyncio.run(_run())


def test_suggest_defaults_button() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.query_one("#vmid", Input).value = ""
            app.query_one("#name", Input).value = ""
            await pilot.click("#suggest_btn")
            await pilot.pause()
            assert app.query_one("#vmid", Input).value.strip() == "900"
            assert app.query_one("#name", Input).value.strip() == "macos-sequoia"

    asyncio.run(_run())


def test_generate_smbios_button() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            old_serial = app.state.smbios.serial if app.state.smbios else ""
            await pilot.click("#smbios_btn")
            await pilot.pause()
            assert app.state.smbios is not None
            # May be same or different, but must be set
            assert app.state.smbios.serial != ""

    asyncio.run(_run())


def test_smbios_preview_none() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.state.smbios = None
            app._update_smbios_preview()
            await pilot.pause()
            text = app.query_one("#smbios_preview", Static).content
            assert "not generated" in str(text)

    asyncio.run(_run())


def test_validate_form_all_invalid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.query_one("#vmid", Input).value = "abc"
            app.query_one("#name", Input).value = "x"
            app.query_one("#memory", Input).value = "100"
            app.query_one("#disk", Input).value = "10"
            app.query_one("#bridge", Input).value = "eth0"
            app.query_one("#storage_input", Input).value = ""
            result = app._validate_form(quiet=False)
            assert result is False
            assert app.query_one("#vmid", Input).has_class("invalid")
            assert app.query_one("#name", Input).has_class("invalid")
            assert app.query_one("#memory", Input).has_class("invalid")
            assert app.query_one("#disk", Input).has_class("invalid")
            assert app.query_one("#bridge", Input).has_class("invalid")
            assert app.query_one("#storage_input", Input).has_class("invalid")

    asyncio.run(_run())


def test_validate_form_valid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            result = app._validate_form(quiet=True)
            assert result is True
            assert not app.query_one("#vmid", Input).has_class("invalid")

    asyncio.run(_run())


def test_validate_form_quiet_no_notification() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.query_one("#vmid", Input).value = "abc"
            result = app._validate_form(quiet=True)
            assert result is False

    asyncio.run(_run())


def test_input_changed_triggers_validation() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.query_one("#vmid", Input).value = "5"
            await pilot.pause()
            assert app.query_one("#vmid", Input).has_class("invalid")

    asyncio.run(_run())


def test_step4_next_validation_blocks() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.query_one("#vmid", Input).value = "abc"
            await pilot.click("#next_btn_4")
            await pilot.pause()
            assert app.current_step == 4

    asyncio.run(_run())


def test_step4_next_read_form_fails() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            # Make _validate_form pass but _read_form fail
            from unittest.mock import patch
            with patch.object(app, "_validate_form", return_value=True):
                with patch.object(app, "_read_form", return_value=None):
                    app._go_next()
            assert app.current_step == 4

    asyncio.run(_run())


def test_step4_next_domain_validation_fails(monkeypatch) -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            monkeypatch.setattr(
                app_module, "validate_config",
                lambda cfg: ["Fake domain error."],
            )
            await pilot.click("#next_btn_4")
            await pilot.pause()
            assert app.current_step == 4
            errors_text = str(app.query_one("#form_errors", Static).content)
            assert "Fake domain error" in errors_text

    asyncio.run(_run())


def test_read_form_invalid_vmid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.query_one("#vmid", Input).value = "not-a-number"
            result = app._read_form()
            assert result is None

    asyncio.run(_run())


def test_read_form_success() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            config = app._read_form()
            assert config is not None
            assert config.macos == "sequoia"
            assert config.vmid == 900

    asyncio.run(_run())


def test_read_form_no_smbios() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.state.smbios = None
            config = app._read_form()
            assert config is not None
            assert config.smbios_serial == ""

    asyncio.run(_run())


def test_preflight_checks_running() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.preflight_done = False
            app._update_preflight_display()
            text = str(app.query_one("#preflight_checks", Static).content)
            assert "Checking" in text

    asyncio.run(_run())


def test_preflight_checks_ok() -> None:
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.preflight_done = True
            app.state.preflight_ok = True
            app.state.preflight_checks = [
                PreflightCheck("qm available", True, "/usr/sbin/qm"),
                PreflightCheck("dmg2img available", True, "/usr/bin/dmg2img"),
            ]
            app._update_preflight_display()
            text = str(app.query_one("#preflight_checks", Static).content)
            assert "All 2 checks passed" in text
            assert "qm available" in text
            assert "dmg2img available" in text

    asyncio.run(_run())


def test_preflight_checks_failed() -> None:
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.preflight_done = True
            app.state.preflight_ok = False
            app.state.preflight_checks = [
                PreflightCheck("qm available", False, "not found"),
                PreflightCheck("dmg2img available", False, "Not found. Install with: apt install dmg2img"),
            ]
            app._update_preflight_display()
            text = str(app.query_one("#preflight_checks", Static).content)
            assert "2 check(s) failed" in text
            assert "qm available: not found" in text
            assert "apt install dmg2img" in text

    asyncio.run(_run())


def test_preflight_checks_mixed() -> None:
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.preflight_done = True
            app.state.preflight_ok = False
            app.state.preflight_checks = [
                PreflightCheck("qm available", True, "/usr/sbin/qm"),
                PreflightCheck("dmg2img available", False, "Not found. Install with: apt install dmg2img"),
            ]
            app._update_preflight_display()
            text = str(app.query_one("#preflight_checks", Static).content)
            assert "1 check(s) failed" in text
            assert "qm available" in text
            assert "dmg2img available" in text

    asyncio.run(_run())


# ── Step 5: Review & Dry Run ───────────────────────────────────────

def test_step4_to_step5_with_assets_ok(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 5, monkeypatch)
            assert app.current_step == 5
            assert app.state.assets_ok is True
            assert app.query_one("#dry_run_btn", Button).disabled is False

    asyncio.run(_run())


def test_config_summary_displayed(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 5, monkeypatch)
            summary = str(app.query_one("#config_summary", Static).content)
            assert "Sequoia" in summary or "sequoia" in summary
            assert "Create VM shell" in summary

    asyncio.run(_run())


def test_config_summary_with_installer_path(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.query_one("#installer_path", Input).value = "/tmp/test.iso"
            monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])
            await pilot.click("#next_btn_4")
            await pilot.pause()
            summary = str(app.query_one("#config_summary", Static).content)
            assert "Installer" in summary

    asyncio.run(_run())


def test_render_config_summary_no_config() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.config = None
            app._render_config_summary()
            # No crash

    asyncio.run(_run())


def test_check_assets_missing_not_downloadable(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            monkeypatch.setattr(
                app_module, "required_assets",
                lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing", downloadable=False)],
            )
            await pilot.click("#next_btn_4")
            await pilot.pause()
            status = str(app.query_one("#download_status", Static).content)
            assert "Provide path manually" in status

    asyncio.run(_run())


def test_check_assets_no_config() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.config = None
            app._check_and_download_assets()
            # No crash

    asyncio.run(_run())


def test_download_worker_success(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadProgress

    download_calls = {"opencore": 0, "recovery": 0}

    def fake_download_opencore(macos, dest, on_progress=None):
        download_calls["opencore"] += 1
        if on_progress:
            on_progress(DownloadProgress(downloaded=500, total=1000, phase="opencore"))
            on_progress(DownloadProgress(downloaded=800, total=0, phase="opencore"))
        return dest / f"opencore-{macos}.iso"

    def fake_download_recovery(macos, dest, on_progress=None):
        download_calls["recovery"] += 1
        if on_progress:
            on_progress(DownloadProgress(downloaded=1000, total=1000, phase="recovery"))
        return dest / f"{macos}-recovery.img"

    monkeypatch.setattr(app_module, "download_opencore", fake_download_opencore)
    monkeypatch.setattr(app_module, "download_recovery", fake_download_recovery)
    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            # Set required_assets AFTER _advance_to_step (which doesn't use it for step 4)
            monkeypatch.setattr(
                app_module, "required_assets",
                lambda cfg: [
                    AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "", downloadable=True),
                    AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "", downloadable=True),
                ],
            )
            await pilot.click("#next_btn_4")
            await pilot.pause()
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if not app.state.download_running:
                    break
            assert download_calls["opencore"] == 1
            assert download_calls["recovery"] == 1
            assert app.state.downloads_complete is True

    asyncio.run(_run())


def test_download_worker_opencore_error(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadError

    def raise_dl_error(*a, **kw):
        raise DownloadError("fail")

    monkeypatch.setattr(app_module, "download_opencore", raise_dl_error)
    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            monkeypatch.setattr(
                app_module, "required_assets",
                lambda cfg: [
                    AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "", downloadable=True),
                ],
            )
            await pilot.click("#next_btn_4")
            await pilot.pause()
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if not app.state.download_running:
                    break
            assert len(app.state.download_errors) > 0
            status = str(app.query_one("#download_status", Static).content)
            assert "Download errors" in status

    asyncio.run(_run())


def test_download_worker_recovery_error(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadError

    def raise_dl_error(*a, **kw):
        raise DownloadError("recovery fail")

    monkeypatch.setattr(app_module, "download_recovery", raise_dl_error)
    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            monkeypatch.setattr(
                app_module, "required_assets",
                lambda cfg: [
                    AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "", downloadable=True),
                ],
            )
            await pilot.click("#next_btn_4")
            await pilot.pause()
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if not app.state.download_running:
                    break
            status = str(app.query_one("#download_status", Static).content)
            assert "Download errors" in status

    asyncio.run(_run())


def test_update_download_progress() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.query_one("#download_progress").remove_class("hidden")
            app._update_download_progress("opencore", 50)
            await pilot.pause()
            status = str(app.query_one("#download_status", Static).content)
            assert "50%" in status
            # At 100%, show "Finalizing"
            app._update_download_progress("opencore", 100)
            await pilot.pause()
            status = str(app.query_one("#download_status", Static).content)
            assert "Finalizing" in status

    asyncio.run(_run())


def test_finish_download_success() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.download_running = True
            app._finish_download([])
            assert app.state.download_running is False
            assert app.state.downloads_complete is True

    asyncio.run(_run())


def test_finish_download_errors() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.download_running = True
            app._finish_download(["OpenCore: network error"])
            assert app.state.download_running is False
            assert len(app.state.download_errors) > 0

    asyncio.run(_run())


def test_dry_run_blocked_while_running() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.apply_running = True
            app._run_dry_apply()
            # No crash, early return

    asyncio.run(_run())


def test_dry_run_blocked_no_plan() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.plan_steps = []
            app._run_dry_apply()
            assert app.state.apply_running is False

    asyncio.run(_run())


def test_dry_run_success(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])
    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)
                class _R:
                    ok = True
                    returncode = 0
                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/dry.log"))

    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 5, monkeypatch)
            await pilot.click("#dry_run_btn")
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if not app.state.apply_running:
                    break
            assert app.state.dry_run_ok is True
            assert app.query_one("#next_btn_5", Button).disabled is False

    asyncio.run(_run())


def test_dry_run_failure(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])
    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        return ApplyResult(ok=False, results=[], log_path=Path("/tmp/dry-fail.log"))

    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 5, monkeypatch)
            await pilot.click("#dry_run_btn")
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if not app.state.apply_running:
                    break
            assert app.state.dry_run_ok is False
            assert app.query_one("#dry_run_btn", Button).disabled is False

    asyncio.run(_run())


def test_update_dry_progress_before_result() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.query_one("#dry_progress").remove_class("hidden")
            app.query_one("#dry_log").remove_class("hidden")
            app._update_dry_progress(1, 3, "Test Step", None)
            log_text = str(app.query_one("#dry_log", Static).content)
            assert "Running 1/3" in log_text

    asyncio.run(_run())


def test_update_dry_progress_after_result() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.query_one("#dry_progress").remove_class("hidden")
            app.query_one("#dry_log").remove_class("hidden")

            class FakeResult:
                ok = True
                returncode = 0

            app._update_dry_progress(1, 3, "Test Step", FakeResult())
            log_text = str(app.query_one("#dry_log", Static).content)
            assert "OK 1/3" in log_text

    asyncio.run(_run())


# ── Step 6: Install ────────────────────────────────────────────────

def test_prepare_install_step() -> None:
    from osx_proxmox_next.domain import VmConfig

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.config = VmConfig(
                vmid=900, name="test", macos="sequoia",
                cores=8, memory_mb=16384, disk_gb=128,
                bridge="vmbr0", storage="local-lvm",
            )
            app._prepare_install_step()
            label = str(app.query_one("#install_btn", Button).label)
            assert "Sequoia" in label
            assert not app.query_one("#install_btn").has_class("hidden")

    asyncio.run(_run())


def test_prepare_install_no_config() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.config = None
            app._prepare_install_step()
            # No crash

    asyncio.run(_run())


def test_live_install_blocked_while_running() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.apply_running = True
            app._run_live_install()
            # No crash, early return

    asyncio.run(_run())


def test_live_install_blocked_no_config() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.config = None
            app._run_live_install()
            assert app.state.apply_running is False

    asyncio.run(_run())


def test_live_install_blocked_no_preflight() -> None:
    from osx_proxmox_next.domain import VmConfig
    from osx_proxmox_next.planner import PlanStep

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.config = VmConfig(
                vmid=900, name="test", macos="sequoia",
                cores=8, memory_mb=16384, disk_gb=128,
                bridge="vmbr0", storage="local-lvm",
            )
            app.state.plan_steps = [PlanStep("Echo", ["echo", "hi"])]
            app.state.preflight_ok = False
            app._run_live_install()
            assert app.state.apply_running is False

    asyncio.run(_run())


def test_live_install_success(monkeypatch) -> None:
    from osx_proxmox_next.preflight import PreflightCheck
    from osx_proxmox_next.domain import VmConfig
    from osx_proxmox_next.planner import PlanStep
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        app_module, "run_preflight",
        lambda: [PreflightCheck("qm", True, "ok"), PreflightCheck("root", True, "ok")],
    )

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)
                class _R:
                    ok = True
                    returncode = 0
                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/live.log"))

    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)
    monkeypatch.setattr(app_module, "create_snapshot", lambda vmid: RollbackSnapshot(vmid=vmid, path=Path("/tmp/snap.conf")))

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            # Wait for preflight to complete with all-ok mocked checks
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.preflight_done:
                    break
            assert app.state.preflight_ok is True
            app.state.config = VmConfig(
                vmid=900, name="test", macos="sequoia",
                cores=8, memory_mb=16384, disk_gb=128,
                bridge="vmbr0", storage="local-lvm",
            )
            app.state.plan_steps = [PlanStep("Echo", ["echo", "hi"])]
            app._run_live_install()
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.live_done:
                    break
            assert app.state.live_ok is True
            result_text = str(app.query_one("#result_box", Static).content)
            assert "completed successfully" in result_text
            assert "ko-fi" in result_text

    asyncio.run(_run())


def test_live_install_failure(monkeypatch) -> None:
    from osx_proxmox_next.preflight import PreflightCheck
    from osx_proxmox_next.domain import VmConfig
    from osx_proxmox_next.planner import PlanStep
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        app_module, "run_preflight",
        lambda: [PreflightCheck("qm", True, "ok"), PreflightCheck("root", True, "ok")],
    )
    monkeypatch.setattr(app_module, "apply_plan", lambda steps, execute=False, on_step=None, adapter=None: ApplyResult(ok=False, results=[], log_path=Path("/tmp/fail.log")))
    monkeypatch.setattr(app_module, "create_snapshot", lambda vmid: RollbackSnapshot(vmid=vmid, path=Path("/tmp/snap.conf")))

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.preflight_done:
                    break
            assert app.state.preflight_ok is True
            app.state.config = VmConfig(
                vmid=900, name="test", macos="sequoia",
                cores=8, memory_mb=16384, disk_gb=128,
                bridge="vmbr0", storage="local-lvm",
            )
            app.state.plan_steps = [PlanStep("Echo", ["echo", "hi"])]
            app._run_live_install()
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.live_done:
                    break
            assert app.state.live_ok is False
            result_text = str(app.query_one("#result_box", Static).content)
            assert "FAILED" in result_text
            assert "qm destroy 900" in result_text

    asyncio.run(_run())


def test_finish_live_install_ok_no_snapshot() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app._finish_live_install(ok=True, log_path=Path("/tmp/log.txt"), snapshot=None)
            assert app.state.live_ok is True
            result_text = str(app.query_one("#result_box", Static).content)
            assert "completed" in result_text

    asyncio.run(_run())


def test_finish_live_install_fail_no_snapshot() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app._finish_live_install(ok=False, log_path=Path("/tmp/log.txt"), snapshot=None)
            assert app.state.live_ok is False
            result_text = str(app.query_one("#result_box", Static).content)
            assert "FAILED" in result_text
            assert "qm destroy" not in result_text

    asyncio.run(_run())


def test_update_live_progress() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.query_one("#live_progress").remove_class("hidden")
            app.query_one("#live_log").remove_class("hidden")
            app._update_live_progress(1, 3, "Step1", None)
            log_text = str(app.query_one("#live_log", Static).content)
            assert "Running 1/3" in log_text

            class FakeResult:
                ok = False
                returncode = 1
            app._update_live_progress(2, 3, "Step2", FakeResult())
            log_text = str(app.query_one("#live_log", Static).content)
            assert "FAIL 2/3" in log_text

    asyncio.run(_run())


# ── Detection Tests ─────────────────────────────────────────────────

def test_detect_vmid_pvesh(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            return "910\n"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 910

    asyncio.run(_run())


def test_detect_vmid_qm_list(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n900   macos-test\n905   macos-test2\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 906

    asyncio.run(_run())


def test_detect_vmid_fallback(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "check_output", lambda cmd, **kw: (_ for _ in ()).throw(Exception("no")))

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 900

    asyncio.run(_run())


def test_detect_vmid_pvesh_non_digit(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            return "not-a-number\n"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 900

    asyncio.run(_run())


def test_detect_vmid_pvesh_out_of_range(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            return "50"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 900

    asyncio.run(_run())


def test_detect_vmid_pvesh_json_object(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            return '{"data": 200}'
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 900

    asyncio.run(_run())


def test_detect_vmid_qm_list_empty(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 900

    asyncio.run(_run())


def test_detect_vmid_qm_list_non_digit(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n900   test\n      \nstatus running\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 901

    asyncio.run(_run())


def test_detect_vmid_boundary_low(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n50    test\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 100

    asyncio.run(_run())


def test_detect_vmid_boundary_high(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n999999 test\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 999999

    asyncio.run(_run())


def test_detect_storage_success(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        return "Name      Type  Status\nlocal-lvm dir   active\nnfs-store nfs   active\n"

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert "local-lvm" in targets
        assert "nfs-store" in targets

    asyncio.run(_run())


def test_detect_storage_no_default(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        return "Name     Type  Status\ncustom1  dir   active\n"

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert targets[0] == "local-lvm"
        assert "custom1" in targets

    asyncio.run(_run())


def test_detect_storage_dedup(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        return "Name      Type\nlocal-lvm dir\nlocal-lvm dir\ncustom1   nfs\n"

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert targets.count("local-lvm") == 1

    asyncio.run(_run())


def test_detect_storage_empty_line(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        return "Name  Type\n\n   \nlocal dir\n"

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert "local-lvm" in targets

    asyncio.run(_run())


# ── Preflight Worker ────────────────────────────────────────────────

def test_preflight_runs_on_mount() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            # Give preflight thread time to complete
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.preflight_done:
                    break
            assert app.state.preflight_done is True

    asyncio.run(_run())


def test_finish_preflight_all_ok(monkeypatch) -> None:
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            checks = [
                PreflightCheck("qm available", True, "/usr/sbin/qm"),
                PreflightCheck("Root privileges", True, "uid=0"),
            ]
            app._finish_preflight(checks)
            assert app.state.preflight_ok is True
            assert app.query_one("#preflight_next_btn", Button).disabled is False

    asyncio.run(_run())


def test_finish_preflight_some_fail(monkeypatch) -> None:
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            checks = [
                PreflightCheck("qm available", True, "/usr/sbin/qm"),
                PreflightCheck("Root privileges", False, "not root"),
            ]
            app._finish_preflight(checks)
            assert app.state.preflight_ok is False
            assert app.query_one("#preflight_next_btn", Button).disabled is True

    asyncio.run(_run())


# ── Edge Cases ──────────────────────────────────────────────────────

def test_unmapped_button_pressed() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()

            class FakeEvent:
                class button:
                    id = "totally_unknown_button"

            app.on_button_pressed(FakeEvent())
            # No crash

    asyncio.run(_run())


def test_run_function(monkeypatch) -> None:
    from osx_proxmox_next import app as app_mod
    called = [False]

    def fake_run(self):
        called[0] = True

    monkeypatch.setattr(NextApp, "run", fake_run)
    app_mod.run()
    assert called[0] is True


def test_wizard_state_defaults() -> None:
    state = WizardState()
    assert state.selected_os == ""
    assert state.vmid == 900
    assert state.preflight_done is False
    assert state.dry_run_ok is False
    assert state.live_ok is False


def test_append_log_rolling_window() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.query_one("#dry_log").remove_class("hidden")
            for i in range(20):
                app._append_log("#dry_log", f"line {i}")
            log_text = str(app.query_one("#dry_log", Static).content)
            assert "line 19" in log_text
            assert "line 0" not in log_text

    asyncio.run(_run())


def test_on_mount_no_storage_targets(monkeypatch) -> None:
    monkeypatch.setattr(NextApp, "_detect_storage_targets", lambda self: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            assert app.state.selected_storage == ""

    asyncio.run(_run())


def test_suggest_defaults_generates_smbios_if_missing() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            app.state.smbios = None
            app._apply_host_defaults()
            assert app.state.smbios is not None

    asyncio.run(_run())


def test_suggest_defaults_keeps_existing_smbios() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            original = app.state.smbios
            app._apply_host_defaults()
            assert app.state.smbios is original

    asyncio.run(_run())


def test_go_next_step6_noop() -> None:
    """_go_next at step 6 does nothing (no step 7)."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.current_step = 6
            await pilot.pause()
            app._go_next()
            assert app.current_step == 6

    asyncio.run(_run())


def test_step5_to_step6_transition(monkeypatch) -> None:
    """Dry run OK -> Next: Install transitions to step 6."""
    from osx_proxmox_next.domain import VmConfig

    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])
    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 5, monkeypatch)
            # Simulate dry run passing
            app.state.dry_run_ok = True
            app.query_one("#next_btn_5", Button).disabled = False
            await pilot.click("#next_btn_5")
            await pilot.pause()
            assert app.current_step == 6
            label = str(app.query_one("#install_btn", Button).label)
            assert "Sequoia" in label

    asyncio.run(_run())


def test_download_worker_skips_non_downloadable(monkeypatch) -> None:
    """Download worker skips assets with downloadable=False."""
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadProgress

    download_calls = {"opencore": 0}

    def fake_download_opencore(macos, dest, on_progress=None):
        download_calls["opencore"] += 1
        return dest / f"opencore-{macos}.iso"

    monkeypatch.setattr(app_module, "download_opencore", fake_download_opencore)
    monkeypatch.setattr(app_module, "validate_config", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 4)
            # Mix of downloadable and non-downloadable
            monkeypatch.setattr(
                app_module, "required_assets",
                lambda cfg: [
                    AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "", downloadable=True),
                    AssetCheck("Extra thing", Path("/tmp/extra"), False, "", downloadable=False),
                ],
            )
            await pilot.click("#next_btn_4")
            await pilot.pause()
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if not app.state.download_running:
                    break
            assert download_calls["opencore"] == 1

    asyncio.run(_run())


def test_rebuild_plan_after_download(monkeypatch) -> None:
    """After downloads complete, plan is rebuilt with correct asset paths."""
    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 5, monkeypatch)
            assert app.state.plan_steps
            old_steps = list(app.state.plan_steps)
            # Simulate a rebuild
            app._rebuild_plan_after_download()
            # Plan was rebuilt (new list object)
            assert app.state.plan_steps is not old_steps

    asyncio.run(_run())


def test_rebuild_plan_after_download_no_config(monkeypatch) -> None:
    """Rebuild gracefully handles invalid form (returns None)."""
    monkeypatch.setattr(app_module, "required_assets", lambda cfg: [])

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 5, monkeypatch)
            old_config = app.state.config
            # Break the form so _read_form returns None
            app.query_one("#vmid", Input).value = "invalid"
            app._rebuild_plan_after_download()
            # Config unchanged since form was invalid
            assert app.state.config is old_config

    asyncio.run(_run())


# ── Manage Mode Tests ───────────────────────────────────────────────


def test_manage_mode_toggle() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            assert not app.state.manage_mode
            assert not app.query_one("#create_panel").has_class("hidden")
            assert app.query_one("#manage_panel").has_class("hidden")
            # Switch to manage
            await pilot.click("#mode_manage")
            await pilot.pause()
            assert app.state.manage_mode is True
            assert app.query_one("#create_panel").has_class("hidden")
            assert not app.query_one("#manage_panel").has_class("hidden")
            assert app.query_one("#mode_manage").has_class("mode_active")
            assert not app.query_one("#mode_create").has_class("mode_active")
            # Switch back to create
            await pilot.click("#mode_create")
            await pilot.pause()
            assert app.state.manage_mode is False
            assert not app.query_one("#create_panel").has_class("hidden")
            assert app.query_one("#manage_panel").has_class("hidden")

    asyncio.run(_run())


def test_manage_vm_list_populated(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "qm" and cmd[1] == "list":
            return (
                "VMID  NAME          STATUS\n"
                "106   macos-test    running\n"
                "\n"
                "200   linux-vm      stopped\n"
            )
        if cmd[0] == "qm" and cmd[1] == "config":
            vmid = cmd[2]
            if vmid == "106":
                return 'args: -device isa-applesmc,osk="test"\n'
            return "ostype: l26\n"  # non-macOS
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.uninstall_vm_list:
                    break
            display = str(app.query_one("#vm_list_display", Static).content)
            assert "106" in display
            assert "macos-test" in display
            assert "200" not in display
            assert "linux-vm" not in display

    asyncio.run(_run())


def test_manage_vm_list_empty(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        raise Exception("qm not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
            display = str(app.query_one("#vm_list_display", Static).content)
            assert "No macOS VMs found" in display

    asyncio.run(_run())


def test_manage_vm_list_no_macos_vms(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "qm" and cmd[1] == "list":
            return "VMID  NAME          STATUS\n200   linux-vm      running\n"
        if cmd[0] == "qm" and cmd[1] == "config":
            return "ostype: l26\n"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
            display = str(app.query_one("#vm_list_display", Static).content)
            assert "No macOS VMs found" in display

    asyncio.run(_run())


def test_manage_vm_list_config_failure(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        if cmd[0] == "qm" and cmd[1] == "list":
            return "VMID  NAME          STATUS\n106   macos-test    running\n"
        if cmd[0] == "qm" and cmd[1] == "config":
            raise Exception("config failed")
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
            display = str(app.query_one("#vm_list_display", Static).content)
            # Config failed → VM skipped → no macOS VMs
            assert "No macOS VMs found" in display

    asyncio.run(_run())


def test_manage_vmid_input_enables_destroy() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            assert app.query_one("#manage_destroy_btn", Button).disabled is True
            app.query_one("#manage_vmid", Input).value = "106"
            await pilot.pause()
            assert app.query_one("#manage_destroy_btn", Button).disabled is False

    asyncio.run(_run())


def test_manage_vmid_invalid_keeps_destroy_disabled() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_vmid", Input).value = "abc"
            await pilot.pause()
            assert app.query_one("#manage_destroy_btn", Button).disabled is True

    asyncio.run(_run())


def test_manage_vmid_out_of_range() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_vmid", Input).value = "5"
            await pilot.pause()
            assert app.query_one("#manage_destroy_btn", Button).disabled is True

    asyncio.run(_run())


def test_manage_purge_toggle() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            assert app.state.uninstall_purge is True
            cb = app.query_one("#manage_purge_cb", Checkbox)
            assert cb.value is True
            # Toggle OFF via click (fires on_checkbox_changed)
            await pilot.click("#manage_purge_cb")
            await pilot.pause()
            assert cb.value is False
            assert app.state.uninstall_purge is False
            # Toggle back ON
            await pilot.click("#manage_purge_cb")
            await pilot.pause()
            assert cb.value is True
            assert app.state.uninstall_purge is True
            # Cover else branch: unknown checkbox ID is ignored
            fake_cb = Checkbox("fake", id="other_cb")
            event = Checkbox.Changed(fake_cb, fake_cb.value)
            app.on_checkbox_changed(event)
            assert app.state.uninstall_purge is True  # unchanged

    asyncio.run(_run())


def test_manage_destroy_blocked_while_running() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app.state.uninstall_running = True
            app._run_destroy()
            # No crash, early return

    asyncio.run(_run())


def test_manage_destroy_invalid_vmid_noop() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_vmid", Input).value = "abc"
            app._run_destroy()
            assert app.state.uninstall_running is False

    asyncio.run(_run())


def test_manage_destroy_vmid_out_of_range_noop() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_vmid", Input).value = "50"
            app._run_destroy()
            assert app.state.uninstall_running is False

    asyncio.run(_run())


def test_manage_destroy_success(monkeypatch) -> None:
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        app_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=Path("/tmp/snap.conf")),
    )

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)
                class _R:
                    ok = True
                    returncode = 0
                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/destroy.log"))

    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_vmid", Input).value = "106"
            await pilot.pause()
            await pilot.click("#manage_destroy_btn")
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.uninstall_done:
                    break
            assert app.state.uninstall_ok is True
            result_text = str(app.query_one("#manage_result", Static).content)
            assert "successfully" in result_text

    asyncio.run(_run())


def test_manage_destroy_failure(monkeypatch) -> None:
    from osx_proxmox_next.rollback import RollbackSnapshot

    monkeypatch.setattr(
        app_module, "create_snapshot",
        lambda vmid: RollbackSnapshot(vmid=vmid, path=Path("/tmp/snap.conf")),
    )
    monkeypatch.setattr(
        app_module, "apply_plan",
        lambda steps, execute=False, on_step=None, adapter=None: ApplyResult(
            ok=False, results=[], log_path=Path("/tmp/fail.log")
        ),
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_vmid", Input).value = "106"
            await pilot.pause()
            await pilot.click("#manage_destroy_btn")
            for _ in range(30):
                await pilot.pause()
                time.sleep(0.05)
                if app.state.uninstall_done:
                    break
            assert app.state.uninstall_ok is False
            result_text = str(app.query_one("#manage_result", Static).content)
            assert "Failed" in result_text

    asyncio.run(_run())


def test_manage_update_destroy_log() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_log").remove_class("hidden")
            app._update_destroy_log(1, 2, "Stop VM", None)
            log_text = str(app.query_one("#manage_log", Static).content)
            assert "Running 1/2" in log_text

            class FakeResult:
                ok = True
            app._update_destroy_log(2, 2, "Destroy VM", FakeResult())
            log_text = str(app.query_one("#manage_log", Static).content)
            assert "OK 2/2" in log_text

    asyncio.run(_run())


def test_manage_finish_destroy_refreshes_list(monkeypatch) -> None:
    refresh_calls = []
    monkeypatch.setattr(NextApp, "_refresh_vm_list", lambda self: refresh_calls.append(1))

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await _advance_to_step(pilot, app, 2)
            await pilot.click("#mode_manage")
            await pilot.pause()
            app.query_one("#manage_vmid", Input).value = "106"
            await pilot.pause()
            app.state.uninstall_running = True
            app._finish_destroy(ok=True, log_path=Path("/tmp/log.txt"))
            assert app.state.uninstall_running is False
            assert len(refresh_calls) > 0

    asyncio.run(_run())


def test_wizard_state_manage_defaults() -> None:
    state = WizardState()
    assert state.manage_mode is False
    assert state.uninstall_vm_list == []
    assert state.uninstall_purge is True
    assert state.uninstall_running is False
    assert state.uninstall_done is False
    assert state.uninstall_ok is False
