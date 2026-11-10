from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from osx_proxmox_next import executor, igpu_cli, igpu_engine, igpu_watchdog


@pytest.fixture(scope="module")
def daemon():
    path = (
        Path(igpu_engine.__file__).parent
        / "data"
        / "gpu"
        / "igpu_power_watchdog.py"
    )
    spec = importlib.util.spec_from_file_location(
        "osx_proxmox_next_test_igpu_power_watchdog", path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Will be calling reboot(2) with flags: 0x0", "restart"),
        ("Will be calling reboot(2) with flags: 0x8", "shutdown"),
        ("Will be calling reboot(2) with flags: 0x4008", "shutdown"),
        ("ordinary kernel console output", None),
    ],
)
def test_daemon_intent_of_recognizes_reboot_flags(daemon, line, expected):
    assert daemon.intent_of(line) == expected


def test_daemon_applies_when_iGPU_and_serial_socket_are_present(daemon):
    config = (
        "hostpci0: 0000:00:02.0,pcie=1,x-vga=1\n"
        "serial0: socket\n"
    )

    assert daemon.applies(config) is True


@pytest.mark.parametrize(
    "config",
    [
        "serial0: socket\n",
        "hostpci0: 0000:00:02.0,pcie=1,x-vga=1\n",
    ],
)
def test_daemon_does_not_apply_when_iGPU_or_serial_socket_is_missing(daemon, config):
    assert daemon.applies(config) is False


def test_daemon_ignores_iGPU_and_serial_settings_in_snapshot_sections(daemon):
    config = (
        "serial0: socket\n"
        "[snap]\n"
        "hostpci0: 0000:00:02.0,pcie=1,x-vga=1\n"
    )

    assert daemon.applies(config) is False


def test_daemon_io_ops_sums_read_and_write_operations(daemon):
    status = (
        " rd_operations: 120\n"
        " wr_operations: 35\n"
        " failed_rd_operations: 999\n"
        " failed_wr_operations: 888\n"
    )

    assert daemon.io_ops(status) == 155


def test_daemon_io_ops_is_none_when_operation_counters_are_absent(daemon):
    assert daemon.io_ops("status: running\n") is None


def test_tracker_arms_checks_disk_activity_and_then_decides(daemon):
    tracker = daemon.Tracker()
    now = 100.0

    assert tracker.feed(
        "Will be calling reboot(2) with flags: 0x0", now
    ) == "armed"
    assert tracker.intent == "restart"
    assert tracker.due(now + daemon.GRACE - 0.01) is None
    assert tracker.due(now + daemon.GRACE) == "check"

    tracker.start_quiet(155, now + daemon.GRACE)

    assert tracker.due(now + daemon.GRACE + daemon.QUIET - 0.01) is None
    assert tracker.due(now + daemon.GRACE + daemon.QUIET) == "decide"


def test_tracker_clears_itself_when_firmware_reports_a_reset(daemon):
    tracker = daemon.Tracker()

    assert tracker.feed(
        "Will be calling reboot(2) with flags: 0x8", 100.0
    ) == "armed"
    assert tracker.feed("BdsDxe: reset", 101.0) == "reset"
    assert tracker.intent is None
    assert tracker.ops is None
    assert tracker.due(200.0) is None


def test_step_stops_and_starts_a_hung_restart(daemon):
    calls = []
    state = {"stopped": False}

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            if "--verbose" in argv:
                return 0, " rd_operations: 120\n wr_operations: 35\n"
            return 0, "status: stopped" if state["stopped"] else "status: running"
        if argv[:2] == ["qm", "stop"]:
            state["stopped"] = True
            return 0, ""
        if argv[:2] == ["qm", "start"]:
            state["stopped"] = False
            return 0, ""
        return 1, "unexpected command"

    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x0", 100.0)
    tracker.start_quiet(155, 100.0 + daemon.GRACE)

    acted = daemon.step(
        "100",
        tracker,
        100.0 + daemon.GRACE + daemon.QUIET,
        runner=runner,
        clock=lambda: 1000.0,
        sleep=lambda _seconds: None,
    )

    actions = [
        argv
        for argv in calls
        if argv[:2] == ["qm", "stop"] or argv[:2] == ["qm", "start"]
    ]
    assert acted is True
    assert actions == [
        ["qm", "stop", "100"],
        ["qm", "start", "100"],
    ]


def test_step_stops_a_hung_shutdown_without_starting_it(daemon):
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            if "--verbose" in argv:
                return 0, " rd_operations: 120\n wr_operations: 35\n"
            return 0, "status: running"
        if argv[:2] == ["qm", "stop"]:
            return 0, ""
        if argv[:2] == ["qm", "start"]:
            return 0, ""
        return 1, "unexpected command"

    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x8", 100.0)
    tracker.start_quiet(155, 100.0 + daemon.GRACE)

    acted = daemon.step(
        "100",
        tracker,
        100.0 + daemon.GRACE + daemon.QUIET,
        runner=runner,
        clock=lambda: 1000.0,
        sleep=lambda _seconds: None,
    )

    actions = [
        argv
        for argv in calls
        if argv[:2] == ["qm", "stop"] or argv[:2] == ["qm", "start"]
    ]
    assert acted is True
    assert actions == [["qm", "stop", "100"]]


def test_step_leaves_a_hung_guest_alone_when_disk_counters_move(daemon):
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            if "--verbose" in argv:
                return 0, " rd_operations: 121\n wr_operations: 35\n"
            return 0, "status: running"
        return 1, "unexpected command"

    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x0", 100.0)
    tracker.start_quiet(155, 100.0 + daemon.GRACE)

    acted = daemon.step(
        "100",
        tracker,
        100.0 + daemon.GRACE + daemon.QUIET,
        runner=runner,
        clock=lambda: 1000.0,
        sleep=lambda _seconds: None,
    )

    assert acted is False
    assert not any(argv[:2] == ["qm", "stop"] for argv in calls)
    assert not any(argv[:2] == ["qm", "start"] for argv in calls)


def test_step_does_nothing_when_the_VM_is_already_stopped_at_the_check(daemon):
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            return 0, "status: stopped"
        return 1, "unexpected command"

    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x0", 100.0)

    acted = daemon.step(
        "100",
        tracker,
        100.0 + daemon.GRACE,
        runner=runner,
        clock=lambda: 1000.0,
        sleep=lambda _seconds: None,
    )

    assert acted is False
    assert calls == [["qm", "status", "100"]]


def test_recover_retries_a_failed_stop_with_overrule_shutdown(daemon):
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "stop"]:
            if "--overrule-shutdown" in argv:
                return 0, ""
            return 1, "VM lock is held"
        return 1, "unexpected command"

    recovered = daemon.recover(
        "100",
        "shutdown",
        runner=runner,
        clock=lambda: 1000.0,
        sleep=lambda _seconds: None,
    )

    assert recovered is True
    assert calls == [
        ["qm", "stop", "100"],
        ["qm", "stop", "100", "--overrule-shutdown", "1"],
    ]


def test_recover_gives_up_when_the_VM_stays_running_past_the_stop_deadline(daemon):
    calls = []
    now = [0.0]

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            return 0, "status: running"
        if argv[:2] == ["qm", "stop"]:
            return 0, ""
        if argv[:2] == ["qm", "start"]:
            return 0, ""
        return 1, "unexpected command"

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    recovered = daemon.recover(
        "100",
        "restart",
        runner=runner,
        clock=clock,
        sleep=sleep,
    )

    assert recovered is False
    assert now[0] == daemon.STOP_WAIT
    assert any(argv[:2] == ["qm", "stop"] for argv in calls)
    assert not any(argv[:2] == ["qm", "start"] for argv in calls)


def test_still_applies_uses_the_config_directory_and_detects_missing_settings(
    daemon, tmp_path, monkeypatch
):
    monkeypatch.setattr(daemon, "CONF_DIR", str(tmp_path))
    monkeypatch.setattr(daemon, "PVE_READY", str(tmp_path / "100.conf"))

    (tmp_path / "100.conf").write_text(
        "hostpci0: 0000:00:02.0,pcie=1,x-vga=1\n"
        "serial0: socket\n",
        encoding="utf-8",
    )
    (tmp_path / "101.conf").write_text(
        "serial0: socket\n",
        encoding="utf-8",
    )

    assert daemon.still_applies("100") is None
    assert daemon.still_applies("101") == (
        "the VM no longer has the iGPU and a serial socket"
    )
    assert daemon.still_applies("999") == "the VM config is gone"


def test_daemon_main_rejects_invalid_arguments(daemon, capsys):
    assert daemon.main(["igpu_power_watchdog.py", "not-a-vmid"]) == 64
    assert "usage: igpu_power_watchdog.py <vmid>" in capsys.readouterr().err


def test_watchdog_applies_only_to_tahoe():
    assert igpu_watchdog.applies_to("tahoe") is True

    for version in ("ventura", "sonoma", "sequoia", "Tahoe", None):
        assert igpu_watchdog.applies_to(version) is False


@pytest.mark.parametrize(
    ("cfg", "expect_serial_step"),
    [
        ({}, True),
        ({"serial0": "socket"}, False),
    ],
)
def test_enable_steps_has_the_expected_commands_in_order(
    monkeypatch, tmp_path, cfg, expect_serial_step
):
    oc_calls = []

    def fake_oc_step(vmid, action, description):
        oc_calls.append((vmid, action))
        return SimpleNamespace(
            title=description,
            argv=["oc", action, str(vmid)],
        )

    monkeypatch.setattr(igpu_watchdog, "oc_step", fake_oc_step)

    steps = igpu_watchdog.enable_steps(100, cfg, data=tmp_path)

    expected = [
        [
            "install",
            "-D",
            "-m",
            "755",
            str(tmp_path / igpu_watchdog.DAEMON_FILE),
            igpu_watchdog.DAEMON_PATH,
        ],
        [
            "install",
            "-D",
            "-m",
            "644",
            str(tmp_path / igpu_watchdog.UNIT_FILE),
            igpu_watchdog.UNIT_PATH,
        ],
        ["systemctl", "daemon-reload"],
    ]
    if expect_serial_step:
        expected.append(["qm", "set", "100", "--serial0", "socket"])
    expected.extend(
        [
            ["oc", "serial", "100"],
            ["systemctl", "enable", "osx-next-power-watchdog@100.service"],
            ["systemctl", "restart", "osx-next-power-watchdog@100.service"],
        ]
    )

    assert [step.argv for step in steps] == expected
    assert oc_calls == [("100", "serial")]


def test_disable_steps_stops_the_unit_and_removes_the_serial_boot_argument(monkeypatch):
    oc_calls = []

    def fake_oc_step(vmid, action, description):
        oc_calls.append((vmid, action))
        return SimpleNamespace(
            title=description,
            argv=["oc", action, str(vmid)],
        )

    monkeypatch.setattr(igpu_watchdog, "oc_step", fake_oc_step)

    steps = igpu_watchdog.disable_steps(100)

    assert [step.argv for step in steps] == [
        [
            "bash",
            "-c",
            "systemctl disable --now osx-next-power-watchdog@100.service "
            "2>/dev/null || true",
        ],
        ["oc", "unserial", "100"],
    ]
    assert oc_calls == [("100", "unserial")]


def _vm_config(with_igpu=True):
    lines = []
    if with_igpu:
        lines.append(
            f"hostpci0: {igpu_engine.IGD_SLOT},pcie=1,x-vga=1"
        )
    lines.append("serial0: socket")
    return "\n".join(lines) + "\n"


def _patch_prepare_inputs(monkeypatch, config_raw, status, version):
    info = SimpleNamespace(config_raw=config_raw, status=status)
    monkeypatch.setattr(
        igpu_watchdog,
        "fetch_vm_info",
        lambda vmid, adapter=None: info,
    )
    monkeypatch.setattr(
        igpu_watchdog,
        "detect_macos",
        lambda cfg: version,
    )


def test_prepare_reports_a_missing_VM(monkeypatch):
    monkeypatch.setattr(
        igpu_watchdog,
        "fetch_vm_info",
        lambda vmid, adapter=None: None,
    )

    issues, steps = igpu_watchdog.prepare(100, adapter=object())

    assert issues == ["VM 100 not found."]
    assert steps == []


def test_prepare_refuses_a_running_VM(monkeypatch):
    _patch_prepare_inputs(
        monkeypatch,
        config_raw=_vm_config(),
        status="running",
        version="tahoe",
    )

    issues, steps = igpu_watchdog.prepare(100, adapter=object())

    assert issues == ["VM 100 is running. Stop it first: qm stop 100"]
    assert steps == []


def test_prepare_refuses_a_VM_without_the_iGPU(monkeypatch):
    _patch_prepare_inputs(
        monkeypatch,
        config_raw=_vm_config(with_igpu=False),
        status="stopped",
        version="tahoe",
    )

    issues, steps = igpu_watchdog.prepare(100, adapter=object())

    assert issues == [
        "VM 100 does not have the iGPU on hostpci0. "
        "Set it up first: osx-next-cli igpu-finish 100"
    ]
    assert steps == []



# ── hand-written from here on ──────────────────────────────────────────

def test_prepare_refuses_a_release_that_restarts_normally(monkeypatch):
    _patch_prepare_inputs(monkeypatch, config_raw=_vm_config(), status="stopped", version="sequoia")
    issues, steps = igpu_watchdog.prepare(100, adapter=object())
    assert issues == ["VM 100 runs macOS Sequoia, which restarts and shuts down normally with the iGPU. "
                      "The watchdog is only for Tahoe."] and steps == []


@pytest.mark.parametrize("version", ["tahoe", None])
def test_prepare_builds_the_enable_steps_for_tahoe_or_an_unknown_release(monkeypatch, version):
    _patch_prepare_inputs(monkeypatch, config_raw=_vm_config(), status="stopped", version=version)
    issues, steps = igpu_watchdog.prepare(100, adapter=object())
    assert issues == [] and steps[-1].argv == ["systemctl", "restart", "osx-next-power-watchdog@100.service"]
    # serial0 is already a socket in this config, so no qm set step
    assert not any(s.argv[:2] == ["qm", "set"] for s in steps)


def test_prepare_remove_needs_only_a_stopped_vm(monkeypatch):
    _patch_prepare_inputs(monkeypatch, config_raw="name: x\n", status="stopped", version="sequoia")
    issues, steps = igpu_watchdog.prepare(100, remove=True, adapter=object())
    assert issues == [] and "disable --now" in steps[0].argv[2]
    _patch_prepare_inputs(monkeypatch, config_raw="name: x\n", status="running", version="sequoia")
    issues, steps = igpu_watchdog.prepare(100, remove=True, adapter=object())
    assert issues == ["VM 100 is running. Stop it first: qm stop 100"] and steps == []


def _applied(ok, title="Start the power watchdog"):
    return SimpleNamespace(ok=ok, log_path="/tmp/apply.log",
                           results=[SimpleNamespace(ok=ok, title=title)])


@pytest.mark.parametrize("remove,ok,code,text", [
    (False, True, 0, "The power watchdog of VM 100 is running."),
    (True, True, 0, "The power watchdog of VM 100 is removed."),
    (False, False, 4, "FAILED at: Start the power watchdog. Log: /tmp/apply.log"),
])
def test_run_watchdog_applies_the_plan_and_reports(monkeypatch, capsys, remove, ok, code, text):
    monkeypatch.setattr(igpu_watchdog, "prepare", lambda vmid, remove=False: ([], ["step"]))
    seen = []
    monkeypatch.setattr(executor, "apply_plan", lambda steps, execute=False: seen.append((steps, execute)) or _applied(ok))
    assert igpu_cli.run_watchdog(SimpleNamespace(vmid=100, remove=remove)) == code
    assert text in capsys.readouterr().out and seen == [(["step"], True)]


def test_run_watchdog_refuses_without_changing_anything(monkeypatch, capsys):
    monkeypatch.setattr(igpu_watchdog, "prepare", lambda vmid, remove=False: (["VM 100 not found."], []))
    monkeypatch.setattr(executor, "apply_plan", lambda *a, **k: pytest.fail("nothing may be applied"))
    assert igpu_cli.run_watchdog(SimpleNamespace(vmid=100, remove=False)) == 2
    assert "REFUSED: VM 100 not found." in capsys.readouterr().out


def test_the_cli_parses_and_dispatches_igpu_watchdog(monkeypatch):
    from osx_proxmox_next import cli
    got = []
    monkeypatch.setattr(igpu_cli, "run_watchdog", lambda args: got.append((args.vmid, args.remove)) or 0)
    assert cli.run_cli(["igpu-watchdog", "996", "--remove"]) == 0 and got == [(996, True)]


BANNER = "Darwin Kernel Version 25.6.0: Mon Oct 12 00:33:09 PDT 2026; root:xnu-12377/RELEASE_X86_64"


def test_a_panic_log_repeating_the_kernel_banner_is_not_a_reset(daemon):
    """A panic prints "Kernel version:" and then the banner: the guest is still hung."""
    tracker = daemon.Tracker()
    tracker.feed("com.apple.xpc.launchd|x (system) <Notice>: Will be calling reboot(2) with flags: 0x0", 1.0)
    assert tracker.feed("Kernel version:", 2.0) is None and tracker.feed(BANNER, 2.0) is None
    assert tracker.intent == "restart"


def test_a_new_kernel_banner_is_a_reset_even_without_the_firmware_lines(daemon):
    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x0", 1.0)
    assert tracker.feed("MACH Reboot", 2.0) is None and tracker.feed(BANNER, 9.0) == "reset"
    assert tracker.intent is None


def test_a_second_reboot_line_during_the_quiet_check_starts_over(daemon):
    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x0", 0.0)
    tracker.start_quiet(5, daemon.GRACE)
    tracker.feed("Will be calling reboot(2) with flags: 0x8", daemon.GRACE + 1)
    assert tracker.intent == "shutdown" and tracker.ops is None
    assert tracker.due(daemon.GRACE + daemon.QUIET + 1) is None  # the GRACE clock restarted


def test_io_ops_ignores_network_counters(daemon):
    """Measured live: a hung Tahoe kernel still answers ARP and TCP, so netout moves while the guest is stuck."""
    base = "\t\trd_operations: 10\n\t\twr_operations: 5\nnetin: 999\nnetout: 566586\n"
    assert daemon.io_ops(base) == 15
    assert daemon.io_ops(base.replace("566586", "566960")) == 15
    assert daemon.io_ops("netout: 7\n") is None


def test_a_missing_config_only_counts_while_the_cluster_file_system_is_up(daemon, monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "CONF_DIR", str(tmp_path))
    monkeypatch.setattr(daemon, "PVE_READY", str(tmp_path / ".version"))
    assert daemon.still_applies("7") is None  # /etc/pve empty: pve-cluster restarting
    (tmp_path / ".version").write_text("1")
    assert daemon.still_applies("7") == "the VM config is gone"
    (tmp_path / "7.conf").mkdir()  # unreadable for another reason
    assert daemon.still_applies("7") is None


def test_the_igpu_may_be_written_with_host_equals(daemon):
    assert daemon.applies("hostpci0: host=0000:00:02.0,legacy-igd=1\nserial0: socket\n")


def test_watch_reads_lines_split_across_reads_and_joined_in_one(daemon, monkeypatch, capsys):
    monkeypatch.setattr(daemon, "run", lambda argv, timeout=120: pytest.fail(f"no command expected: {argv}"))
    path, _ = _serve(["Will be calling reboot(2) with flags: 0x0\r\nMACH Reboot\r\nBdsDxe: loading Boot0004"],
                     keep_open=False)
    daemon.watch("7", path)
    out = capsys.readouterr().out
    assert "asked for a restart" in out and "reset by itself" in out


def test_a_vm_whose_counters_cannot_be_read_is_left_alone(daemon):
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            return (1, "") if "--verbose" in argv else (0, "status: running")
        return 0, ""
    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x8", 0.0)
    assert daemon.step("7", tracker, daemon.GRACE, runner) is False
    assert daemon.step("7", tracker, daemon.GRACE + daemon.QUIET, runner) is False
    assert not any(a[:2] == ["qm", "stop"] for a in calls) and tracker.intent is None


def test_recover_reports_a_failed_stop_and_a_failed_start(daemon, capsys):
    assert daemon.recover("7", "shutdown", lambda argv: (1, "locked")) is False
    assert "qm stop failed: locked" in capsys.readouterr().out

    def runner(argv):
        if argv[:2] == ["qm", "status"]:
            return 0, "status: stopped"
        return (1, "no iGPU") if argv[:2] == ["qm", "start"] else (0, "")
    assert daemon.recover("7", "restart", runner, clock=lambda: 0.0, sleep=lambda s: None) is False
    out = capsys.readouterr().out
    assert f"qm start failed (try {daemon.START_TRIES} of {daemon.START_TRIES}): no iGPU" in out


def test_recover_retries_qm_start_while_the_cluster_has_no_quorum(daemon, capsys):
    starts, slept = [], []

    def runner(argv):
        if argv[:2] == ["qm", "status"]:
            return 0, "status: stopped"
        if argv[:2] == ["qm", "start"]:
            starts.append(argv)
            return (1, "cluster not ready - no quorum?") if len(starts) < 3 else (0, "")
        return 0, ""
    assert daemon.recover("7", "restart", runner, clock=lambda: 0.0, sleep=slept.append) is True
    assert len(starts) == 3 and slept == [daemon.START_RETRY, daemon.START_RETRY]
    assert "try 1 of" in capsys.readouterr().out


def test_run_returns_the_exit_code_and_output(daemon):
    assert daemon.run(["sh", "-c", "echo out; echo err >&2; exit 3"]) == (3, "out\nerr\n")
    rc, out = daemon.run(["/nonexistent/binary"])
    assert rc == 1 and out


def test_disable_turns_the_unit_off(daemon, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(daemon, "run", lambda argv, timeout=120: seen.append(argv) or (0, ""))
    daemon.disable("7", "the VM config is gone")
    assert seen == [["systemctl", "disable", "osx-next-power-watchdog@7.service"]]
    assert "the VM config is gone; disabling" in capsys.readouterr().out


def test_main_disables_the_unit_once_the_vm_no_longer_applies(daemon, monkeypatch):
    seen = []
    monkeypatch.setattr(daemon, "still_applies", lambda vmid: "the VM config is gone")
    monkeypatch.setattr(daemon, "run", lambda argv, timeout=120: seen.append(argv) or (0, ""))
    assert daemon.main(["igpu_power_watchdog.py", "7"]) == 0 and seen[0][:2] == ["systemctl", "disable"]


def test_main_keeps_retrying_while_the_vm_is_not_running(daemon, monkeypatch):
    answers = iter([None, "the VM config is gone"])
    monkeypatch.setattr(daemon, "still_applies", lambda vmid: next(answers))
    monkeypatch.setattr(daemon, "run", lambda argv, timeout=120: (0, ""))
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(daemon.time, "monotonic", lambda: next(ticks))
    slept = []
    monkeypatch.setattr(daemon.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(daemon, "SOCK_DIR", "/nonexistent")
    assert daemon.main(["igpu_power_watchdog.py", "7"]) == 0 and slept == [3]


def _serve(lines, keep_open):
    """A one-shot serial socket in a short /tmp path (AF_UNIX paths are limited to ~104 bytes)."""
    import socket
    import tempfile
    import threading
    d = tempfile.mkdtemp(dir="/tmp")
    path = f"{d}/7.serial0"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen(1)
    done = threading.Event()

    def serve():
        conn, _ = srv.accept()
        for line in lines:
            conn.sendall(line.encode() + b"\r\n")
        if keep_open:
            done.wait(10)
        conn.close()
        srv.close()
    threading.Thread(target=serve, daemon=True).start()
    return path, done


def test_watch_stops_a_guest_that_hung_after_asking_to_shut_down(daemon, monkeypatch):
    calls = []

    def runner(argv, timeout=120):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            return 0, ("\trd_operations: 5\n\twr_operations: 1\n" if "--verbose" in argv else "status: running")
        return 0, ""
    monkeypatch.setattr(daemon, "run", runner)
    monkeypatch.setattr(daemon, "GRACE", 0)
    monkeypatch.setattr(daemon, "QUIET", 0)
    monkeypatch.setattr(daemon, "POLL", 0.05)
    path, done = _serve(["launchd quiesce", "<Notice>: Will be calling reboot(2) with flags: 0x8"], keep_open=True)
    daemon.watch("7", path)
    done.set()
    assert ["qm", "stop", "7"] in calls and ["qm", "start", "7"] not in calls


def test_watch_leaves_a_guest_that_reset_by_itself(daemon, monkeypatch, capsys):
    monkeypatch.setattr(daemon, "run", lambda argv, timeout=120: pytest.fail(f"no command expected: {argv}"))
    path, _ = _serve(["Will be calling reboot(2) with flags: 0x0", "MACH Reboot",
                      "BdsDxe: loading Boot0004 \"UEFI QEMU HARDDISK\""], keep_open=False)
    daemon.watch("7", path)
    out = capsys.readouterr().out
    assert "asked for a restart" in out and "the guest reset by itself" in out


def test_watch_notes_a_guest_that_powered_off_by_itself(daemon, monkeypatch, capsys):
    monkeypatch.setattr(daemon, "run", lambda argv, timeout=120: pytest.fail(f"no command expected: {argv}"))
    path, _ = _serve(["Will be calling reboot(2) with flags: 0x8", "CPU halted"], keep_open=False)
    daemon.watch("7", path)
    assert "powered off by itself" in capsys.readouterr().out


def test_watch_logs_a_kernel_panic_and_leaves_a_vm_that_went_away(daemon, monkeypatch, capsys):
    monkeypatch.setattr(daemon, "run", lambda argv, timeout=120: pytest.fail(f"no command expected: {argv}"))
    path, _ = _serve(["panic(cpu 1 caller 0xffffff8012eb08a8): AppleIntelFramebuffer::setPowerState timeout"],
                     keep_open=False)
    daemon.watch("7", path)
    out = capsys.readouterr().out
    assert "VM 7: kernel panic: panic(cpu 1 caller" in out and "restart by itself after the panic" in out


def test_watch_restarts_a_guest_that_stays_down_after_a_panic(daemon, monkeypatch, capsys):
    """Seen live in a heavy GPU soak: WhateverXe hangs the GPU, the kernel panics and the VM spins forever."""
    calls, state = [], {"stopped": False}

    def runner(argv, timeout=120):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            if "--verbose" in argv:
                return 0, "\trd_operations: 5\n\twr_operations: 1\n"
            return 0, "status: stopped" if state["stopped"] else "status: running"
        state["stopped"] = argv[:2] == ["qm", "stop"]
        return 0, ""
    monkeypatch.setattr(daemon, "run", runner)
    monkeypatch.setattr(daemon, "GRACE", 0)
    monkeypatch.setattr(daemon, "QUIET", 0)
    monkeypatch.setattr(daemon, "POLL", 0.05)
    path, done = _serve(["panic(cpu 1 caller 0xffffff8012eb08a8): AppleIntelFramebuffer::setPowerState timeout",
                         "Kernel version:", "Darwin Kernel Version 25.6.0"], keep_open=True)
    history = []
    daemon.watch("7", path, history)
    done.set()
    assert [a for a in calls if a[:2] in (["qm", "stop"], ["qm", "start"])] == [["qm", "stop", "7"], ["qm", "start", "7"]]
    assert len(history) == 1 and "macOS panicked, then hung" in capsys.readouterr().out



PANIC = "panic(cpu 2 caller 0xffffff8001234567): AppleIntelFramebuffer::setPowerState timeout"


def _panic_runner(calls, state, ops=("120", "35")):
    """qm status reports running until qm stop; the disk counters come from ops, one pair per --verbose call."""
    reads = iter(ops) if isinstance(ops, list) else None

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["qm", "status"]:
            if "--verbose" in argv:
                rd, wr = next(reads) if reads else ops
                return 0, f" rd_operations: {rd}\n wr_operations: {wr}\n"
            return 0, "status: stopped" if state["stopped"] else "status: running"
        state["stopped"] = argv[:2] == ["qm", "stop"]
        return 0, ""
    return runner


def _actions(calls):
    return [a for a in calls if a[:2] in (["qm", "stop"], ["qm", "start"])]


def test_tracker_arms_on_a_kernel_panic_and_clears_on_a_reset(daemon):
    tracker = daemon.Tracker()
    assert tracker.feed(PANIC, 1.0) == "panic" and tracker.intent == "panic"
    assert tracker.feed(PANIC, 5.0) is None and tracker.since == 1.0  # a nested panic does not restart GRACE
    assert tracker.feed("BdsDxe: loading Boot0004", 9.0) == "reset" and tracker.intent is None


def test_a_panic_after_a_shutdown_request_keeps_the_shutdown(daemon):
    tracker = daemon.Tracker()
    tracker.feed("Will be calling reboot(2) with flags: 0x8", 1.0)
    assert tracker.feed(PANIC, 2.0) is None and tracker.intent == "shutdown"


def test_step_restarts_a_vm_that_stays_down_after_a_panic(daemon):
    calls, state, history = [], {"stopped": False}, []
    tracker = daemon.Tracker()
    tracker.feed(PANIC, 100.0)
    tracker.start_quiet(155, 100.0 + daemon.GRACE)
    assert daemon.step("100", tracker, 100.0 + daemon.GRACE + daemon.QUIET, _panic_runner(calls, state),
                       clock=lambda: 1000.0, sleep=lambda _s: None, history=history) is True
    assert _actions(calls) == [["qm", "stop", "100"], ["qm", "start", "100"]] and history == [1000.0]


def test_a_panic_with_busy_disks_is_watched_again_not_forgotten(daemon):
    calls, state = [], {"stopped": False}
    runner = _panic_runner(calls, state, ops=[("120", "40"), ("120", "40")])
    tracker = daemon.Tracker()
    tracker.feed(PANIC, 0.0)
    tracker.start_quiet(155, daemon.GRACE)
    now = daemon.GRACE + daemon.QUIET
    assert daemon.step("7", tracker, now, runner, clock=lambda: 0.0, sleep=lambda _s: None, history=[]) is False
    assert tracker.intent == "panic" and tracker.ops == 160 and not _actions(calls)
    assert daemon.step("7", tracker, now + daemon.QUIET, runner, clock=lambda: 0.0, sleep=lambda _s: None,
                       history=[]) is True
    assert _actions(calls) == [["qm", "stop", "7"], ["qm", "start", "7"]]


def test_panic_allowed_counts_only_restarts_inside_the_window(daemon):
    now = 100000.0
    assert daemon.panic_allowed([now - daemon.PANIC_WINDOW + 1] * daemon.PANIC_MAX, now) is False
    assert daemon.panic_allowed([now - daemon.PANIC_WINDOW - 1] * daemon.PANIC_MAX, now) is True
    assert daemon.panic_allowed([now] * (daemon.PANIC_MAX - 1), now) is True


def test_too_many_panics_in_an_hour_leave_the_vm_stopped(daemon, capsys):
    calls, state = [], {"stopped": False}
    history = [1000.0 - n for n in range(daemon.PANIC_MAX)]
    tracker = daemon.Tracker()
    tracker.feed(PANIC, 100.0)
    tracker.start_quiet(155, 100.0 + daemon.GRACE)
    assert daemon.step("100", tracker, 100.0 + daemon.GRACE + daemon.QUIET, _panic_runner(calls, state),
                       clock=lambda: 1000.0, sleep=lambda _s: None, history=history) is True
    assert _actions(calls) == [["qm", "stop", "100"]] and len(history) == daemon.PANIC_MAX
    assert "leaving it stopped" in capsys.readouterr().out
