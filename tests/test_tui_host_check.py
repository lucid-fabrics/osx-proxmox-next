"""Host check: failing checks first, a plain reason, the exact commands, and a fix that runs after a confirmation."""
from __future__ import annotations

import threading

from textual.widgets import Button, RichLog

import tui_fakes as F
from osx_proxmox_next.app import NextApp
from osx_proxmox_next.preflight import PreflightCheck
from osx_proxmox_next.screens.base import ChoiceDialog
from osx_proxmox_next.screens.home import HomeScreen
from osx_proxmox_next.screens.host_check import HostCheckScreen, PassedSummary, check_title, fix_label, short_name
from osx_proxmox_next.widgets.chrome import KeyBar

SIZE = (100, 30)


async def open_check(pilot, app):
    await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
    await F.settle(pilot, lambda: app.host_checks is not None)
    await pilot.press("h")
    await F.settle(pilot, lambda: isinstance(app.screen, HostCheckScreen))
    await F.settle(pilot, lambda: bool(app.screen.query(".hc-item")) or bool(app.screen.query(PassedSummary)))
    return app.screen


def keys(app) -> str:
    return app.screen.query_one(KeyBar).render().plain


def body(screen) -> str:
    return "\n".join(F.text_of(w) for w in screen.query("#hc-body Static, #hc-body PassedSummary"))


def test_titles_short_names_and_labels():
    assert check_title(PreflightCheck("KVM ignore_msrs", False, "")) == "KVM ignore_msrs is off"
    assert check_title(PreflightCheck("dmg2img available", False, "")) == "dmg2img is not installed"
    assert check_title(PreflightCheck("/dev/kvm present", False, "")) == "/dev/kvm is missing"
    assert check_title(PreflightCheck("Root privileges", False, "")) == "osx-next is not running as root"
    assert check_title(PreflightCheck("IOMMU enabled", False, "")) == "IOMMU enabled needs attention"
    assert short_name(PreflightCheck("qm available", True, "")) == "qm"
    assert short_name(PreflightCheck("/dev/kvm present", True, "")) == "/dev/kvm"
    assert short_name(PreflightCheck("IOMMU enabled", True, "")) == "IOMMU"
    assert short_name(PreflightCheck("Root privileges", True, "")) == "root"
    one = F.failing_checks()[:1]
    assert fix_label(one) == "Fix it" and fix_label(F.failing_checks()) == "Fix both"
    three = [PreflightCheck(str(i), False, "", fix=F.MSRS_FIX[:1]) for i in range(3)]
    assert fix_label(three) == "Fix 3"


@F.tui
async def test_a_failing_host_lists_the_problems_first_with_why_and_the_exact_commands(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        assert F.text_of(screen.query_one("#hc-title")) == "This host needs two small fixes"
        assert F.text_of(screen.query_one("#hc-count")) == "12 other checks passed"
        text = body(screen)
        assert text.index("KVM ignore_msrs is off") < text.index("dmg2img is not installed") < text.index("12 passed")
        assert "macOS hangs at the Apple logo without it. No reboot needed." in text
        assert "echo Y > /sys/module/kvm/parameters/ignore_msrs" in text and "update-initramfs -k all -u" in text
        assert "apt install -y dmg2img" in text and "These 4 commands run as root, exactly as shown." in text
        assert screen.query_one("#hc-fix", Button).label.plain == "Fix both" and app.focused.id == "hc-fix"
        assert "Fix both" in keys(app) and "Check again" in keys(app) and "Back" in keys(app)
        assert "2 to fix" in screen.query_one("#topbar").render().plain


@F.tui
async def test_a_good_host_says_so_and_has_no_fix_key(monkeypatch):
    F.patch_host(monkeypatch)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        assert F.text_of(screen.query_one("#hc-title")) == "This host is ready"
        assert F.text_of(screen.query_one("#hc-count")) == "14 checks passed"
        assert "Fix" not in keys(app) and not screen.query("#hc-fix")
        await pilot.press("f")                              # nothing to fix: nothing happens
        assert app.screen is screen
        await pilot.press("escape")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_problems_without_a_safe_fix_show_the_explanation_only(monkeypatch):
    checks = F.good_checks()
    checks[-2] = PreflightCheck("/dev/kvm present", False, "Required for hardware acceleration")
    checks[-1] = PreflightCheck("Root privileges", False, "Current UID must be root (uid=0) for full workflow")
    F.patch_host(monkeypatch, checks=checks)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        assert F.text_of(screen.query_one("#hc-title")) == "This host has 2 problems"
        text = body(screen)
        assert "/dev/kvm is missing" in text and "Without hardware virtualization" in text
        assert "osx-next is not running as root" in text and "Start osx-next as root." in text
        assert "Current UID must be root" in text           # the technical line follows the plain one
        assert not screen.query("#hc-fix") and "Fix" not in keys(app)
    checks = F.good_checks()
    checks[-1] = PreflightCheck("Root privileges", False, "x")
    F.patch_host(monkeypatch, checks=checks)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        assert F.text_of((await open_check(pilot, app)).query_one("#hc-title")) == "This host has 1 problem"


@F.tui
async def test_a_single_fixable_problem_says_fix_it(monkeypatch):
    checks = [c for c in F.failing_checks() if c.name != "dmg2img available"]
    F.patch_host(monkeypatch, checks=checks)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        assert F.text_of(screen.query_one("#hc-title")) == "This host needs one small fix"
        assert screen.query_one("#hc-fix", Button).label.plain == "Fix it"
        assert "These 3 commands" in body(screen)
        one_cmd = [PreflightCheck("dmg2img available", False, "", fix=F.failing_checks()[1].fix)] + F.good_checks()[1:]
        F.patch_host(monkeypatch, checks=one_cmd)


@F.tui
async def test_a_single_command_is_worded_in_the_singular(monkeypatch):
    one_cmd = [PreflightCheck("dmg2img available", False, "", fix=F.failing_checks()[1].fix)] + F.good_checks()[1:]
    F.patch_host(monkeypatch, checks=one_cmd)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        assert "This command runs as root, exactly as shown." in body(screen)


@F.tui
async def test_the_passed_checks_are_one_line_that_expands(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        passed = screen.query_one(PassedSummary)
        assert passed.render().plain.startswith("✓ 12 passed  qm · pvesm")
        passed.focus()
        await pilot.press("enter")
        text = passed.render().plain
        assert "✓ qm available  /usr/bin/qm" in text and passed.get_content_height(None, None, 200) == 13
        await pilot.press("space")
        assert passed.get_content_height(None, None, 200) == 1
        await pilot.click(PassedSummary)
        assert passed.open


@F.tui
async def test_not_now_goes_back(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await open_check(pilot, app)
        await pilot.click("#hc-notnow")
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen))


@F.tui
async def test_f_confirms_then_runs_the_commands_with_live_output_and_checks_again(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    ran: list[str] = []

    def run_fix(commands, on_line):
        for command in commands:
            on_line(f"$ {command.display}")
            ran.append(command.display)
        on_line("all quiet")
        return True

    monkeypatch.setattr("osx_proxmox_next.screens.host_check._is_root", lambda: True)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_fix_commands", run_fix)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_preflight", F.good_checks)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        await pilot.press("f")
        dialog = app.screen
        assert isinstance(dialog, ChoiceDialog) and app.focused.name == "no"
        assert F.text_of(dialog.query(".dialog-title").first()) == "Run 4 commands as root?"
        shown = [F.text_of(w) for w in dialog.query(".dialog-line")]
        assert shown[0] == "echo Y > /sys/module/kvm/parameters/ignore_msrs" and shown[-1] == "apt install -y dmg2img"
        assert dialog.query_one("#choice-yes", Button).label.plain == "Fix both"
        await pilot.press("escape")                         # cancel: nothing ran
        assert ran == [] and app.screen is screen
        await pilot.press("f")
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: len(ran) == 4)
        await F.settle(pilot, lambda: F.text_of(screen.query_one("#hc-title")) == "This host is ready")
        log_lines = [s.text for s in screen.query_one("#fix-log", RichLog).lines]
        assert "$ update-initramfs -k all -u" in log_lines and "all quiet" in log_lines
        assert "Done. Checking the host again." in log_lines
        assert not screen.running and app.host_checks and all(c.ok for c in app.host_checks)
        assert "host ready" in screen.query_one("#topbar").render().plain


@F.tui
async def test_a_failing_fix_stops_and_the_checks_run_again(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    monkeypatch.setattr("osx_proxmox_next.screens.host_check._is_root", lambda: True)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_fix_commands", lambda commands, on_line: False)
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_preflight", F.failing_checks)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        screen.action_fix()
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: screen.fix_ok is False)
        await F.settle(pilot, lambda: bool(screen.query("#hc-fix")) and not screen.running)
        assert any("2 problem(s) remain" in n.message for n in app._notifications)
        assert "Stopped. Checking the host again." in [s.text for s in screen.query_one("#fix-log", RichLog).lines]


@F.tui
async def test_fixing_without_root_says_so_and_runs_nothing(monkeypatch):
    F.patch_host(monkeypatch, checks=F.failing_checks())
    monkeypatch.setattr("osx_proxmox_next.screens.host_check._is_root", lambda: False)
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        screen = await open_check(pilot, app)
        await pilot.press("f")
        assert app.screen is screen
        assert any("needs root" in n.message for n in app._notifications)


@F.tui
async def test_a_fix_in_progress_blocks_back_and_asks_before_quitting(monkeypatch):
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
        screen = await open_check(pilot, app)
        screen.action_fix()
        await pilot.press("tab")
        await pilot.press("enter")
        await F.settle(pilot, lambda: screen.running)
        assert screen.busy()
        await pilot.press("escape")
        assert app.screen is screen                         # no leaving mid-fix
        screen.action_fix()                                 # a second fix does not start
        screen.action_recheck()                             # nor a recheck
        await pilot.press("q")
        assert isinstance(app.screen, ChoiceDialog)
        await pilot.press("escape")
        gate.set()
        await F.settle(pilot, lambda: not screen.running)


@F.tui
async def test_r_checks_again_and_the_screen_checks_by_itself_when_nothing_was_run_yet(monkeypatch):
    F.patch_host(monkeypatch)
    results = {"checks": F.failing_checks()}
    monkeypatch.setattr("osx_proxmox_next.screens.host_check.run_preflight", lambda: list(results["checks"]))
    app = NextApp()
    async with app.run_test(size=SIZE) as pilot:
        await F.settle(pilot, lambda: isinstance(app.screen, HomeScreen) and app.screen.loaded)
        app.host_checks = None                              # the background check has not finished
        await pilot.press("h")
        await F.settle(pilot, lambda: isinstance(app.screen, HostCheckScreen))
        await F.settle(pilot, lambda: F.text_of(app.screen.query_one("#hc-title")) == "This host needs two small fixes")
        results["checks"] = F.good_checks()
        await pilot.press("r")
        await F.settle(pilot, lambda: F.text_of(app.screen.query_one("#hc-title")) == "This host is ready")
