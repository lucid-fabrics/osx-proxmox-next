import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Input, Static

from osx_proxmox_next import app as app_module
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.executor import ApplyResult


def test_wizard_starts_with_guided_status() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.click("#nav_wizard")
            assert (
                "Step 1" in app.wizard_status_text
                or "Preflight has failures" in app.wizard_status_text
            )

    asyncio.run(_run())


def test_apply_dry_does_not_create_snapshot(monkeypatch) -> None:
    calls = {"snapshots": 0}

    def fake_snapshot(_vmid: int):
        calls["snapshots"] += 1
        raise AssertionError("dry apply must not snapshot")

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):  # type: ignore[no-untyped-def]
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)
                class _R:
                    ok = True
                    returncode = 0
                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/fake.log"))

    monkeypatch.setattr(app_module, "create_snapshot", fake_snapshot)
    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(90, 28)) as pilot:
            await pilot.click("#nav_wizard")
            app.preflight_has_run = True
            await pilot.press("a")
            await pilot.pause()
            await pilot.pause()

    asyncio.run(_run())
    assert calls["snapshots"] == 0


def test_use_recommended_populates_empty_fields() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid").value = ""
            app.query_one("#name").value = ""
            app.query_one("#macos").value = ""
            await pilot.click("#defaults")
            await pilot.pause()
            assert app.query_one("#vmid").value.strip() == "900"
            assert app.query_one("#name").value.strip() == "macos-sequoia"
            assert app.query_one("#macos").value.strip() == "sequoia"

    asyncio.run(_run())


def test_locked_fields_and_storage_quick_select() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            assert app.query_one("#macos").disabled is True
            await pilot.click("#toggle_advanced")
            assert app.query_one("#cores").disabled is True
            await pilot.click("#back_basic")
            await pilot.click("#storage_pick_0")
            assert app.query_one("#storage").value.strip() != ""

    asyncio.run(_run())


def test_invalid_form_blocks_step3_navigation() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid").value = "abc"
            await pilot.click("#goto_step3")
            await pilot.pause()
            assert app.step_page == 2
            assert app.query_one("#vmid").has_class("invalid")
            assert "Fix highlighted fields" in app.wizard_status_text

    asyncio.run(_run())


def test_use_recommended_uses_next_available_vmid(monkeypatch) -> None:
    monkeypatch.setattr(NextApp, "_detect_next_vmid", lambda self: 1234)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            assert app.query_one("#vmid").value.strip() == "1234"

    asyncio.run(_run())


def test_macos_switch() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#macos_sonoma")
            await pilot.pause()
            assert app.query_one("#macos", Input).value == "sonoma"
            assert app.query_one("#name", Input).value == "macos-sonoma"
            assert app.smbios_identity is not None
            assert app.smbios_identity.model == "iMacPro1,1"
            await pilot.click("#macos_tahoe")
            await pilot.pause()
            assert app.query_one("#macos", Input).value == "tahoe"
            assert app.smbios_identity.model == "MacPro7,1"

    asyncio.run(_run())


def test_generate_smbios_button() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.pause()
            await pilot.click("#goto_step2")
            await pilot.pause()
            app._toggle_advanced()
            await pilot.pause()
            app._generate_smbios()
            await pilot.pause()
            assert app.smbios_identity is not None
            assert app.smbios_identity.serial != ""
            assert "SMBIOS identity generated" in app.wizard_status_text

    asyncio.run(_run())


def test_advanced_toggle_visibility() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            section = app.query_one("#advanced_section")
            assert section.has_class("hidden")
            await pilot.click("#toggle_advanced")
            await pilot.pause()
            assert not section.has_class("hidden")
            assert app.query_one("#basic_grid").has_class("hidden")
            await pilot.click("#back_basic")
            await pilot.pause()
            assert section.has_class("hidden")
            assert not app.query_one("#basic_grid").has_class("hidden")

    asyncio.run(_run())


def test_step_nav_prev_next() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            assert app.step_page == 1
            await pilot.click("#goto_step2")
            assert app.step_page == 2
            await pilot.click("#goto_step1")
            assert app.step_page == 1
            # prev at step 1 stays at 1
            await pilot.press("b")
            assert app.step_page == 1

    asyncio.run(_run())


def test_apply_blocked_during_run() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            app.apply_running = True
            app._apply(execute=False)
            assert "already running" in app.wizard_status_text

    asyncio.run(_run())


def test_apply_live_blocked_no_preflight() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            app.preflight_has_run = True
            app.preflight_ok = False
            app._apply(execute=True)
            assert "preflight has failures" in app.wizard_status_text.lower()

    asyncio.run(_run())


def test_validate_form_branches() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            # Set all invalid
            app.query_one("#vmid", Input).value = "abc"
            app.query_one("#name", Input).value = "x"
            app.query_one("#memory", Input).value = "100"
            app.query_one("#disk", Input).value = "10"
            app.query_one("#bridge", Input).value = "eth0"
            app.query_one("#storage", Input).value = ""
            result = app._validate_form_inputs(quiet=False)
            assert result is False
            assert app.query_one("#vmid", Input).has_class("invalid")
            assert app.query_one("#name", Input).has_class("invalid")
            assert app.query_one("#memory", Input).has_class("invalid")
            assert app.query_one("#disk", Input).has_class("invalid")
            assert app.query_one("#bridge", Input).has_class("invalid")
            assert app.query_one("#storage", Input).has_class("invalid")

    asyncio.run(_run())


def test_validate_tahoe_no_installer() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#macos", Input).value = "tahoe"
            app.query_one("#installer_path", Input).value = ""
            result = app._validate_form_inputs(quiet=False)
            assert result is False
            assert app.query_one("#installer_path", Input).has_class("invalid")

    asyncio.run(_run())


def test_plan_generation_output(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.action_generate_plan()
            await pilot.pause()
            assert app.plan_output_text != ""
            assert "Create VM shell" in app.plan_output_text

    asyncio.run(_run())


def test_input_changed_validation() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            vmid_input = app.query_one("#vmid", Input)
            vmid_input.value = "5"
            # Trigger on_input_changed by setting value which fires Changed
            await pilot.pause()
            # The on_input_changed handler calls _validate_form_inputs(quiet=True)
            # which should mark vmid as invalid
            assert vmid_input.has_class("invalid")

    asyncio.run(_run())


def test_render_health_dashboard() -> None:
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            checks = [
                PreflightCheck("qm available", True, "/usr/sbin/qm"),
                PreflightCheck("/dev/kvm present", False, "not found"),
            ]
            result = app._render_health_dashboard("Health 1/2", checks)
            assert "Host Health Dashboard" in result
            assert "ATTENTION" in result
            assert "PASS" in result
            assert "FAIL" in result

    asyncio.run(_run())


def test_render_preflight_report() -> None:
    from osx_proxmox_next.preflight import PreflightCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            checks = [
                PreflightCheck("qm available", True, "/usr/sbin/qm"),
            ]
            result = app._render_preflight_report(checks)
            assert "Preflight Report" in result
            assert "1/1" in result
            assert "PASS" in result

    asyncio.run(_run())


def test_check_assets_ok(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, "")],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._check_assets()
            assert "Assets check OK" in app.wizard_status_text

    asyncio.run(_run())


def test_check_assets_missing(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck
    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "provide OC")],
    )
    monkeypatch.setattr(
        app_module, "suggested_fetch_commands",
        lambda cfg: ["# place OC image"],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._check_assets()
            assert "missing" in app.wizard_status_text.lower()

    asyncio.run(_run())


def test_storage_pick_invalid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            # Simulate invalid storage_pick button
            from textual.widgets import Button as Btn
            class FakeEvent:
                class button:
                    id = "storage_pick_999"
            app.on_button_pressed(FakeEvent())
            assert "Invalid storage" in app.wizard_status_text

    asyncio.run(_run())


def test_home_nav() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#nav_home")
            await pilot.pause()
            assert not app.query_one("#home_view").has_class("hidden")
            assert app.query_one("#wizard_view").has_class("hidden")

    asyncio.run(_run())


def test_preflight_nav() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_preflight")
            await pilot.pause()
            assert not app.query_one("#preflight_view").has_class("hidden")

    asyncio.run(_run())


def test_stage_labels() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            # _set_stage stores the text in the widget via update()
            # Verify stage was set by checking the workflow_stage variable
            assert app.workflow_stage >= 1

    asyncio.run(_run())


def test_finish_apply_ok() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#plan_output").remove_class("hidden")
            app._finish_apply(execute=False, ok=True, log_path=Path("/tmp/log.txt"), snapshot=None)
            assert "Dry" in app.wizard_status_text
            assert "completed" in app.wizard_status_text.lower()

    asyncio.run(_run())


def test_finish_apply_fail_with_rollback() -> None:
    from osx_proxmox_next.rollback import RollbackSnapshot

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#plan_output").remove_class("hidden")
            snap = RollbackSnapshot(vmid=900, path=Path("/tmp/snap.conf"))
            app._finish_apply(execute=True, ok=False, log_path=Path("/tmp/log.txt"), snapshot=snap)
            assert "failed" in app.wizard_status_text.lower()
            assert "qm destroy 900" in app.wizard_status_text

    asyncio.run(_run())


def test_detect_storage_fallback() -> None:
    async def _run() -> None:
        app = NextApp()
        # storage_targets already set in __init__, verify fallback behavior
        assert len(app.storage_targets) >= 1
        assert app.storage_targets[0] in ("local-lvm", "local")

    asyncio.run(_run())


def test_detect_vmid_pvesh(monkeypatch) -> None:
    from subprocess import check_output as real_check_output

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
    call_count = [0]

    def fake_check_output(cmd, **kw):
        call_count[0] += 1
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n900   macos-test\n905   macos-test2\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        assert result == 906

    asyncio.run(_run())


def test_detect_vmid_fallback(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        assert app._detect_next_vmid() == 900

    asyncio.run(_run())


def test_validate_only_flow(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            await pilot.click("#goto_step3")
            await pilot.pause()
            assert app.step_page == 3
            await pilot.click("#validate")
            await pilot.pause()
            assert app.plan_output_text != ""

    asyncio.run(_run())


def test_handle_validation_tahoe_toggle() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app._handle_validation_issues(["Tahoe requires installer_path to a full installer image."])
            await pilot.pause()
            assert not app.query_one("#advanced_section").has_class("hidden")
            assert "Tahoe" in app.wizard_status_text

    asyncio.run(_run())


def test_goto_step3_blocks_invalid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid", Input).value = "abc"
            app.action_goto_step3()
            await pilot.pause()
            assert app.step_page != 3

    asyncio.run(_run())


def test_apply_dry_generates_plan_if_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [],
    )

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)
                class _R:
                    ok = True
                    returncode = 0
                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/fake.log"))

    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = True
            app.preflight_ok = True
            # Clear any existing plan
            app.last_config = None
            app.last_steps = []
            app._apply(execute=False)
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()
            # Plan should have been auto-generated
            assert app.last_steps or app.plan_output_text

    asyncio.run(_run())


def test_next_step_from_step2_validates() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid", Input).value = "abc"
            app.step_page = 2
            app.action_next_step()
            await pilot.pause()
            # Should not advance to step 3 due to validation
            assert app.step_page != 3

    asyncio.run(_run())


def test_handle_validation_generic_issues() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app._handle_validation_issues(["VM name must be at least 3 characters."])
            await pilot.pause()
            assert "Validation failed" in app.wizard_status_text

    asyncio.run(_run())


def test_read_form_invalid_vmid() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid", Input).value = "not-a-number"
            result = app._read_form()
            assert result is None
            assert "VMID must be a number" in app.wizard_status_text

    asyncio.run(_run())


def test_action_go_wizard_runs_preflight_first_time() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            app.preflight_has_run = False
            await pilot.click("#nav_wizard")
            await pilot.pause()
            assert app.preflight_has_run is True

    asyncio.run(_run())


def test_show_hides_other_views() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app._show("wizard_view")
            await pilot.pause()
            assert not app.query_one("#wizard_view").has_class("hidden")
            assert app.query_one("#home_view").has_class("hidden")
            assert app.query_one("#preflight_view").has_class("hidden")

    asyncio.run(_run())


def test_set_stage_marks_completed() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            app._set_stage(3)
            assert app.workflow_stage == 3

    asyncio.run(_run())


def test_finish_apply_live_ok_sets_stage5() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#plan_output").remove_class("hidden")
            app._finish_apply(execute=True, ok=True, log_path=Path("/tmp/log.txt"), snapshot=None)
            assert app.workflow_stage == 5
            assert "Live" in app.wizard_status_text

    asyncio.run(_run())


def test_update_apply_progress_before_and_after() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#apply_progress").remove_class("hidden")
            app.query_one("#plan_output").remove_class("hidden")
            # Before result
            app._update_apply_progress(1, 2, "Test Step", None)
            assert "Running 1/2" in app.wizard_status_text
            # After result
            class FakeResult:
                ok = True
                returncode = 0
            app._update_apply_progress(1, 2, "Test Step", FakeResult())
            assert "OK 1/2" in app.plan_output_text

    asyncio.run(_run())


def test_run_preflight_ok() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            # Preflight runs automatically, check state
            assert app.preflight_has_run is True

    asyncio.run(_run())


def test_action_apply_live(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, "")],
    )

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)
                class _R:
                    ok = True
                    returncode = 0
                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/fake.log"))

    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)
    monkeypatch.setattr(app_module, "create_snapshot", lambda vmid: None)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = True
            app.preflight_ok = True
            app.action_apply_live()
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

    asyncio.run(_run())


def test_apply_not_preflight_run() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = False
            app._apply(execute=False)
            await pilot.pause()
            # Should have run preflight automatically
            assert app.preflight_has_run is True

    asyncio.run(_run())


def test_apply_validation_fail() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid", Input).value = "abc"
            app.preflight_has_run = True
            app._apply(execute=False)
            await pilot.pause()
            assert "Fix highlighted fields" in app.wizard_status_text

    asyncio.run(_run())


def test_apply_read_form_none() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.query_one("#vmid", Input).value = "not-a-number"
            app.preflight_has_run = True
            app._apply(execute=False)
            await pilot.pause()
            assert "Fix highlighted fields" in app.wizard_status_text

    asyncio.run(_run())


def test_apply_live_missing_assets(monkeypatch) -> None:
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing")],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = True
            app.preflight_ok = True
            app._apply(execute=True)
            await pilot.pause()
            assert "missing assets" in app.wizard_status_text.lower()

    asyncio.run(_run())


def test_generate_plan_validation_fail() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid", Input).value = "abc"
            app.action_generate_plan()
            await pilot.pause()
            assert "Fix highlighted fields" in app.wizard_status_text

    asyncio.run(_run())


def test_generate_plan_read_form_none() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.query_one("#vmid", Input).value = "not-a-number"
            app.action_generate_plan()
            await pilot.pause()
            assert "Fix highlighted fields" in app.wizard_status_text

    asyncio.run(_run())


def test_generate_plan_domain_validation_fails() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.query_one("#macos", Input).value = "tahoe"
            app.query_one("#installer_path", Input).value = ""
            app.action_generate_plan()
            await pilot.pause()
            # Tahoe without installer triggers validation or domain error
            assert app.last_steps == []

    asyncio.run(_run())


def test_set_macos_name_not_prefixed() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            # Set a custom name that doesn't start with "macos-"
            app.query_one("#name", Input).value = "custom-vm"
            app._set_macos("sonoma")
            await pilot.pause()
            # _set_macos checks name.startswith("macos-") at line 454
            # "custom-vm" doesn't match, so line 455 is skipped (branch coverage)
            # But _apply_host_defaults resets it. We verify the branch was hit.
            assert app.query_one("#macos", Input).value == "sonoma"

    asyncio.run(_run())


def test_validate_only_read_form_none() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.query_one("#vmid", Input).value = "not-a-number"
            app._validate_only()
            await pilot.pause()
            assert "VMID must be a number" in app.wizard_status_text

    asyncio.run(_run())


def test_validate_only_with_issues() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.query_one("#macos", Input).value = "tahoe"
            app.query_one("#installer_path", Input).value = ""
            app._validate_only()
            await pilot.pause()
            assert "Tahoe" in app.wizard_status_text or "Validation" in app.wizard_status_text

    asyncio.run(_run())


def test_handle_validation_tahoe_autofill_success(monkeypatch) -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#macos", Input).value = "tahoe"
            app.query_one("#installer_path", Input).value = "/tmp/tahoe.iso"
            app._handle_validation_issues(["Tahoe requires installer_path to a full installer image."])
            await pilot.pause()
            assert "Tahoe installer detected automatically" in app.wizard_status_text

    asyncio.run(_run())


def test_handle_validation_tahoe_advanced_already_shown() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            # Show advanced first
            app._toggle_advanced()
            await pilot.pause()
            app.query_one("#macos", Input).value = "tahoe"
            app._handle_validation_issues(["Tahoe requires installer_path to a full installer image."])
            await pilot.pause()
            assert "Tahoe" in app.wizard_status_text

    asyncio.run(_run())


def test_smbios_preview_none() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.smbios_identity = None
            app._update_smbios_preview()
            await pilot.pause()
            assert app.smbios_identity is None  # remains None, preview cleared

    asyncio.run(_run())


def test_autofill_tahoe_current_set() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#macos", Input).value = "tahoe"
            app.query_one("#installer_path", Input).value = "/tmp/existing.iso"
            result = app._autofill_tahoe_installer_path()
            assert result is True

    asyncio.run(_run())


def test_detect_storage_targets_success(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        return "Name      Type  Status\nlocal-lvm dir   active\nnfs-store nfs   active\n"

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert "local-lvm" in targets
        assert "nfs-store" in targets

    asyncio.run(_run())


def test_detect_storage_targets_no_default(monkeypatch) -> None:
    def fake_check_output(cmd, **kw):
        return "Name     Type  Status\ncustom1  dir   active\n"

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert targets[0] == "local-lvm"
        assert "custom1" in targets

    asyncio.run(_run())


def test_detect_vmid_pvesh_non_digit(monkeypatch) -> None:
    """pvesh returns non-digit, non-JSON output."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            return "not-a-number\n"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        assert result == 900

    asyncio.run(_run())


def test_detect_vmid_qm_list_empty(monkeypatch) -> None:
    """qm list returns no VMs → 900."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        assert result == 900

    asyncio.run(_run())


def test_finish_apply_fail_no_snapshot() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#plan_output").remove_class("hidden")
            app._finish_apply(execute=False, ok=False, log_path=Path("/tmp/log.txt"), snapshot=None)
            assert "failed" in app.wizard_status_text.lower()

    asyncio.run(_run())


def test_next_step_from_step1() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            app.step_page = 1
            app.action_next_step()
            await pilot.pause()
            assert app.step_page == 2

    asyncio.run(_run())


def test_next_step_from_step3() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            app.step_page = 3
            app.action_next_step()
            await pilot.pause()
            # Should stay at 3 (max)
            assert app.step_page == 3

    asyncio.run(_run())


def test_on_button_pressed_unmapped() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            status_before = app.wizard_status_text
            class FakeEvent:
                class button:
                    id = "unknown_button_id"
            app.on_button_pressed(FakeEvent())
            # Unmapped button should not change state
            assert app.wizard_status_text == status_before

    asyncio.run(_run())


def test_check_assets_read_form_none() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid", Input).value = "not-a-number"
            app._check_assets()
            await pilot.pause()
            assert "VMID must be a number" in app.wizard_status_text

    asyncio.run(_run())


def test_action_go_wizard_already_run() -> None:
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            app.preflight_has_run = True
            app.workflow_stage = 4
            app.action_go_wizard()
            await pilot.pause()
            # stage 4 → page=3 (since workflow_stage > 3)
            assert app.step_page == 3

    asyncio.run(_run())


def test_run_function(monkeypatch) -> None:
    """Cover the run() function at line 985."""
    from osx_proxmox_next import app as app_mod
    called = [False]
    original_run = NextApp.run

    def fake_run(self):
        called[0] = True

    monkeypatch.setattr(NextApp, "run", fake_run)
    app_mod.run()
    assert called[0] is True


def test_generate_plan_form_valid_but_read_fails(monkeypatch) -> None:
    """action_generate_plan: validation passes but _read_form returns None (line 342)."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            monkeypatch.setattr(app, "_validate_form_inputs", lambda quiet=False: True)
            monkeypatch.setattr(app, "_read_form", lambda: None)
            app.action_generate_plan()
            await pilot.pause()
            assert app.last_config is None  # plan was not generated

    asyncio.run(_run())


def test_generate_plan_domain_issues(monkeypatch) -> None:
    """action_generate_plan: form valid, read_form ok, but domain validation fails (lines 346-347)."""
    from osx_proxmox_next.domain import VmConfig

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            monkeypatch.setattr(app, "_validate_form_inputs", lambda quiet=False: True)
            monkeypatch.setattr(app, "_read_form", lambda: VmConfig(
                vmid=900, name="macos-tahoe", macos="tahoe", cores=8,
                memory_mb=16384, disk_gb=160, bridge="vmbr0", storage="local-lvm",
                installer_path="",
            ))
            app.action_generate_plan()
            await pilot.pause()
            assert app.last_steps == []  # plan not generated due to domain validation

    asyncio.run(_run())


def test_apply_form_valid_but_read_fails(monkeypatch) -> None:
    """_apply: validation passes but _read_form returns None (line 390)."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = True
            app.preflight_ok = True
            monkeypatch.setattr(app, "_validate_form_inputs", lambda quiet=False: True)
            monkeypatch.setattr(app, "_read_form", lambda: None)
            app._apply(execute=False)
            await pilot.pause()
            assert app.apply_running is False  # apply did not start

    asyncio.run(_run())


def test_apply_plan_generation_fails(monkeypatch) -> None:
    """_apply: plan generation returns no steps (lines 401-404)."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = True
            app.preflight_ok = True
            app.last_config = None
            app.last_steps = []
            monkeypatch.setattr(app, "action_generate_plan", lambda: None)
            app._apply(execute=False)
            await pilot.pause()
            assert app.apply_running is False  # apply aborted, plan was empty

    asyncio.run(_run())


def test_run_preflight_all_ok(monkeypatch) -> None:
    """Cover lines 533, 541-543 when all preflight checks pass."""
    from osx_proxmox_next.preflight import PreflightCheck

    monkeypatch.setattr(
        app_module, "run_preflight",
        lambda: [
            PreflightCheck("qm available", True, "/usr/sbin/qm"),
            PreflightCheck("pvesm available", True, "/usr/sbin/pvesm"),
            PreflightCheck("pvesh available", True, "/usr/sbin/pvesh"),
            PreflightCheck("qemu-img available", True, "/usr/bin/qemu-img"),
            PreflightCheck("/dev/kvm present", True, "hardware acceleration ok"),
            PreflightCheck("Root privileges", True, "uid=0"),
        ],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.pause()
            app._run_preflight()
            await pilot.pause()
            assert app.preflight_ok is True
            assert "Step 1 complete" in app.wizard_status_text
            assert app.step_page == 2

    asyncio.run(_run())


def test_autofill_tahoe_finds_candidate(monkeypatch) -> None:
    """Cover lines 689-691: _autofill_tahoe_installer_path finds a candidate."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#macos", Input).value = "tahoe"
            app.query_one("#installer_path", Input).value = ""
            monkeypatch.setattr(app, "_find_tahoe_installer_path", lambda: "/tmp/tahoe-full.iso")
            result = app._autofill_tahoe_installer_path()
            assert result is True
            assert "auto-detected" in app.wizard_status_text

    asyncio.run(_run())


def test_find_tahoe_installer_no_match(monkeypatch, tmp_path) -> None:
    """Cover return '', loop exhaustion (713→710), and dir-skip (716→715)."""
    iso_dir = tmp_path / "iso_empty"
    iso_dir.mkdir()
    # Existing dir but no matching files — exhausts all patterns
    (iso_dir / "unrelated.txt").write_text("not an iso")
    # Directory matching a tahoe pattern (is_file() → False, covers 716→715)
    (iso_dir / "tahoe-fakedir.iso").mkdir()

    real_path = Path

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()

            def fake_path(p):
                if p == "/mnt/pve":
                    return tmp_path / "no_mnt_pve"  # doesn't exist
                if p == "/var/lib/vz/template/iso":
                    return iso_dir
                if p == "/var/lib/vz/snippets":
                    return tmp_path / "nonexistent"
                return real_path(p)

            monkeypatch.setattr(app_module, "Path", fake_path)
            result = app._find_tahoe_installer_path()
            assert result == ""

    asyncio.run(_run())


def test_find_tahoe_installer_mnt_pve(monkeypatch, tmp_path) -> None:
    """Cover _find_tahoe_installer_path: mnt_pve iteration, iso_path exists/not, pattern match/miss."""
    mnt_pve = tmp_path / "mnt_pve"
    mnt_pve.mkdir()
    # Storage with valid template/iso containing a match
    storage_ok = mnt_pve / "wd2tb"
    iso_dir = storage_ok / "template" / "iso"
    iso_dir.mkdir(parents=True)
    (iso_dir / "macos-tahoe-full.iso").write_text("fake")
    # Dir with matching name (should be skipped by is_file check)
    (iso_dir / "tahoe-dir.iso").mkdir()
    # Storage WITHOUT template/iso (covers iso_path.exists() False → back to loop)
    storage_no_iso = mnt_pve / "noiso"
    storage_no_iso.mkdir()

    real_path = Path

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()

            def fake_path(p):
                if p == "/mnt/pve":
                    return mnt_pve
                if p == "/var/lib/vz/template/iso":
                    return tmp_path / "nonexistent1"
                if p == "/var/lib/vz/snippets":
                    return tmp_path / "nonexistent2"
                return real_path(p)

            monkeypatch.setattr(app_module, "Path", fake_path)
            result = app._find_tahoe_installer_path()
            assert "tahoe" in result
            assert result.endswith(".iso")

    asyncio.run(_run())


def test_detect_storage_targets_with_output(monkeypatch) -> None:
    """Cover lines 725-734: successful pvesm output parsing."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesm":
            return "Name      Type     Status\nlocal-lvm dir      active\nnfs-data  nfs      active\nceph1     rbd      active\n"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert "local-lvm" in targets
        assert "nfs-data" in targets
        assert "ceph1" in targets

    asyncio.run(_run())


def test_detect_vmid_pvesh_out_of_range_digit(monkeypatch) -> None:
    """Cover line 741→743: pvesh returns digit but out of valid range."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            return "50"  # isdigit=True, int=50, but < 100 → falls to json.loads
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        # 50 is out of range for both digit and JSON paths → qm list → fallback
        assert result == 900

    asyncio.run(_run())


def test_detect_vmid_pvesh_json_object(monkeypatch) -> None:
    """pvesh returns non-digit string that parses as non-int JSON."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            return '{"data": 200}'  # not an int when parsed as JSON
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        # JSON parsed but not an int → falls through to qm list
        assert result == 900

    asyncio.run(_run())


def test_detect_vmid_qm_list_non_digit_line(monkeypatch) -> None:
    """Cover 754→752: qm list line where parts[0] is not a digit."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n900   test\n      \nstatus running\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        assert result == 901

    asyncio.run(_run())


def test_detect_vmid_boundary_low(monkeypatch) -> None:
    """Cover line 758: next_vmid < 100 → returns 100."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            # Return VM with ID 50 → next would be 51 which is < 100
            return "VMID  NAME\n50    test\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        assert result == 100

    asyncio.run(_run())


def test_detect_vmid_boundary_high(monkeypatch) -> None:
    """Cover line 760: next_vmid > 999999 → returns 999999."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesh":
            raise Exception("not found")
        if cmd[0] == "qm":
            return "VMID  NAME\n999999 test\n"
        raise Exception("unknown")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        result = app._detect_next_vmid()
        assert result == 999999

    asyncio.run(_run())


def test_apply_with_existing_plan(monkeypatch) -> None:
    """Cover 401→406: _apply when last_config and last_steps are already set."""
    from osx_proxmox_next.domain import VmConfig
    from osx_proxmox_next.planner import PlanStep

    def fake_apply_plan(steps, execute=False, on_step=None, adapter=None):
        for idx, step in enumerate(steps, start=1):
            if on_step:
                on_step(idx, len(steps), step, None)
                class _R:
                    ok = True
                    returncode = 0
                on_step(idx, len(steps), step, _R())
        return ApplyResult(ok=True, results=[], log_path=Path("/tmp/fake.log"))

    monkeypatch.setattr(app_module, "apply_plan", fake_apply_plan)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = True
            app.preflight_ok = True
            # Pre-set last_config and last_steps so line 401 is False → jumps to 406
            app.last_config = VmConfig(
                vmid=900, name="macos-sequoia", macos="sequoia",
                cores=8, memory_mb=16384, disk_gb=128,
                bridge="vmbr0", storage="local-lvm",
            )
            app.last_steps = [PlanStep("Echo", ["echo", "hello"])]
            app._apply(execute=False)
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

    asyncio.run(_run())


def test_detect_storage_dedup(monkeypatch) -> None:
    """Cover line 730→726: name already in targets (dedup branch)."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesm":
            return "Name      Type\nlocal-lvm dir\nlocal-lvm dir\ncustom1   nfs\n"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        # local-lvm should appear only once
        assert targets.count("local-lvm") == 1

    asyncio.run(_run())


def test_detect_storage_empty_line(monkeypatch) -> None:
    """Cover line 728→726: empty parts in line."""
    def fake_check_output(cmd, **kw):
        if cmd[0] == "pvesm":
            return "Name  Type\n\n   \nlocal dir\n"
        raise Exception("not found")

    monkeypatch.setattr(app_module, "check_output", fake_check_output)

    async def _run() -> None:
        app = NextApp()
        targets = app._detect_storage_targets()
        assert "local-lvm" in targets  # inserted as default

    asyncio.run(_run())


def test_apply_live_missing_downloadable_assets(monkeypatch) -> None:
    """Live apply blocked with downloadable assets suggests Download Missing button."""
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.preflight_has_run = True
            app.preflight_ok = True
            app._apply(execute=True)
            await pilot.pause()
            assert "Download Missing" in app.wizard_status_text

    asyncio.run(_run())


def test_download_missing_assets_no_missing(monkeypatch) -> None:
    """Download Missing button with no missing assets shows no-op message."""
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), True, "")],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._download_missing_assets()
            await pilot.pause()
            assert "No downloadable assets missing" in app.wizard_status_text

    asyncio.run(_run())


def test_download_missing_already_running(monkeypatch) -> None:
    """Download Missing button does nothing if download already running."""
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.download_running = True
            app._download_missing_assets()
            await pilot.pause()
            assert "already running" in app.wizard_status_text.lower()

    asyncio.run(_run())


def test_download_missing_read_form_none() -> None:
    """Download Missing button with invalid form returns early."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            app.query_one("#vmid", Input).value = "not-a-number"
            app._download_missing_assets()
            await pilot.pause()

    asyncio.run(_run())


def test_download_missing_triggers_worker(monkeypatch) -> None:
    """Download Missing button triggers threaded download with progress."""
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadProgress
    import time

    download_calls = {"opencore": 0, "recovery": 0}

    def fake_download_opencore(macos, dest, on_progress=None):
        download_calls["opencore"] += 1
        if on_progress:
            on_progress(DownloadProgress(downloaded=500, total=1000, phase="opencore"))
            # Also test total=0 path (unknown size)
            on_progress(DownloadProgress(downloaded=800, total=0, phase="opencore"))
        return dest / f"opencore-{macos}.iso"

    def fake_download_recovery(macos, dest, on_progress=None):
        download_calls["recovery"] += 1
        if on_progress:
            on_progress(DownloadProgress(downloaded=1000, total=1000, phase="recovery"))
        return dest / f"{macos}-recovery.img"

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [
            AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "missing", downloadable=True),
            AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "missing", downloadable=True),
        ],
    )
    monkeypatch.setattr(app_module, "download_opencore", fake_download_opencore)
    monkeypatch.setattr(app_module, "download_recovery", fake_download_recovery)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._download_missing_assets()
            # Wait for background thread
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if not app.download_running:
                    break
            assert download_calls["opencore"] == 1
            assert download_calls["recovery"] == 1

    asyncio.run(_run())


def test_download_missing_with_errors(monkeypatch) -> None:
    """Download Missing button handles OpenCore errors gracefully."""
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadError
    import time

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [
            AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "missing", downloadable=True),
        ],
    )

    def fake_download_opencore(macos, dest, on_progress=None):
        raise DownloadError("network failure")

    monkeypatch.setattr(app_module, "download_opencore", fake_download_opencore)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._download_missing_assets()
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if not app.download_running:
                    break
            assert "Download errors" in app.wizard_status_text

    asyncio.run(_run())


def test_download_missing_recovery_then_opencore(monkeypatch) -> None:
    """Worker processes recovery before opencore — exercises loop-back branch."""
    from osx_proxmox_next.assets import AssetCheck
    import time

    download_calls = {"opencore": 0, "recovery": 0}

    def fake_download_opencore(macos, dest, on_progress=None):
        download_calls["opencore"] += 1
        return dest / f"opencore-{macos}.iso"

    def fake_download_recovery(macos, dest, on_progress=None):
        download_calls["recovery"] += 1
        return dest / f"{macos}-recovery.img"

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [
            AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "missing", downloadable=True),
            AssetCheck("OpenCore image", Path("/tmp/oc.iso"), False, "missing", downloadable=True),
        ],
    )
    monkeypatch.setattr(app_module, "download_opencore", fake_download_opencore)
    monkeypatch.setattr(app_module, "download_recovery", fake_download_recovery)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._download_missing_assets()
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if not app.download_running:
                    break
            assert download_calls["opencore"] == 1
            assert download_calls["recovery"] == 1

    asyncio.run(_run())


def test_download_missing_recovery_error(monkeypatch) -> None:
    """Download Missing button handles recovery errors gracefully."""
    from osx_proxmox_next.assets import AssetCheck
    from osx_proxmox_next.downloader import DownloadError
    import time

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [
            AssetCheck("Installer / recovery image", Path("/tmp/rec.iso"), False, "missing", downloadable=True),
        ],
    )

    def fake_download_recovery(macos, dest, on_progress=None):
        raise DownloadError("recovery download failed")

    monkeypatch.setattr(app_module, "download_recovery", fake_download_recovery)

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._download_missing_assets()
            for _ in range(20):
                await pilot.pause()
                time.sleep(0.05)
                if not app.download_running:
                    break
            assert "Download errors" in app.wizard_status_text
            assert "Recovery" in app.wizard_status_text

    asyncio.run(_run())


def test_update_download_progress() -> None:
    """Cover _update_download_progress method."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            from textual.widgets import ProgressBar as PB
            app.query_one("#apply_progress").remove_class("hidden")
            app._update_download_progress("opencore", 50)
            await pilot.pause()
            assert "50%" in app.wizard_status_text

    asyncio.run(_run())


def test_finish_download_success() -> None:
    """Cover _finish_download with no errors."""
    from osx_proxmox_next.assets import AssetCheck

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.download_running = True
            app._finish_download([])
            await pilot.pause()
            assert not app.download_running

    asyncio.run(_run())


def test_finish_download_errors() -> None:
    """Cover _finish_download with errors."""
    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app.download_running = True
            app._finish_download(["OpenCore: fail"])
            await pilot.pause()
            assert not app.download_running
            assert "Download errors" in app.wizard_status_text

    asyncio.run(_run())


def test_check_assets_downloadable_message(monkeypatch) -> None:
    """_check_assets shows download hint when assets are downloadable."""
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "missing", downloadable=True)],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._check_assets()
            assert "Download Missing" in app.wizard_status_text

    asyncio.run(_run())


def test_check_assets_non_downloadable_message(monkeypatch) -> None:
    """_check_assets shows manual message when assets are not downloadable."""
    from osx_proxmox_next.assets import AssetCheck

    monkeypatch.setattr(
        app_module, "required_assets",
        lambda cfg: [AssetCheck("OC", Path("/tmp/oc.iso"), False, "provide OC", downloadable=False)],
    )

    async def _run() -> None:
        app = NextApp()
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.click("#nav_wizard")
            await pilot.click("#goto_step2")
            await pilot.click("#defaults")
            await pilot.pause()
            app._check_assets()
            assert "Provide path manually" in app.wizard_status_text

    asyncio.run(_run())
