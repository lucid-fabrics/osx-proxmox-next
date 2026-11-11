"""The host-side crash recorder: serial log, incident snapshot, report bundle (data/gpu/igpu_diag.py)."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import shutil
import struct
import tarfile
import tempfile
import threading
from pathlib import Path

import pytest

from osx_proxmox_next import igpu_engine, igpu_report

PANIC = "panic(cpu 2 caller 0xffffff80003f83f2): [com.apple.driver.AppleIntelFramebuffer]: setPowerState timeout"
CONFIG = ('name: macos-tahoe\ndescription: macOS Tahoe 26 VM\nsmbios1: uuid=abc,base64=1\n'
          'args: -device isa-applesmc,osk="secret" -smbios type=2,serial=QUJD,uuid=1234\n'
          'net0: vmxnet3=BC:24:11:AA:BB:CC,bridge=vmbr0\nmachine: pc\n[snap]\nnet0: vmxnet3=11:22:33:44:55:66\n')


@pytest.fixture
def sockdir():
    """A short path: AF_UNIX paths are limited to about 100 characters, pytest's tmp_path is longer on macOS."""
    path = Path(tempfile.mkdtemp(prefix="q", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def diag():
    path = Path(igpu_engine.__file__).parent / "data" / "gpu" / "igpu_diag.py"
    spec = importlib.util.spec_from_file_location("osx_test_igpu_diag", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_sysfs(root: Path, values: dict[int, int] | None = None, size: int = 1 << 20) -> Path:
    """A sysfs tree with a BAR0 file, one coretemp sensor, throttle counters and a cpu model."""
    dev = root / "bus/pci/devices/0000:00:02.0"
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "device").write_text("0x46a6\n")
    buf = bytearray(size)
    for off, val in (values or {}).items():
        struct.pack_into("<I", buf, off, val)
    (dev / "resource0").write_bytes(bytes(buf))
    hw = root / "class/hwmon/hwmon3"
    hw.mkdir(parents=True, exist_ok=True)
    (hw / "name").write_text("coretemp\n")
    (hw / "temp1_input").write_text("94000\n")
    (hw / "temp1_label").write_text("Package id 0\n")
    cpu = root / "devices/system/cpu/cpu0"
    (cpu / "thermal_throttle").mkdir(parents=True, exist_ok=True)
    (cpu / "thermal_throttle/core_throttle_count").write_text("7\n")
    (cpu / "thermal_throttle/package_throttle_count").write_text("4000000\n")
    (cpu / "cpufreq").mkdir(exist_ok=True)
    (cpu / "cpufreq/scaling_cur_freq").write_text("1200000\n")
    return root


def runner(conf: str = CONFIG, dmesg: str = "vfio-pci 0000:00:02.0: reset\nusb 1-1: new\ni915 stuff\n"):
    def run(argv, timeout=5):
        if argv[0] == "qm":
            return (0, conf) if conf else (1, "no such vm")
        if argv[0] == "dmesg":
            return 0, dmesg
        return 1, ""
    return run


def qmp_server(path: Path, reply: str = "RAX=0 RIP=ffff\nCPU#1\nRIP=0010") -> threading.Thread:
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rw")
        f.write('{"QMP": {}}\n')
        f.flush()
        for line in f:
            msg = json.loads(line)
            if msg["execute"] == "qmp_capabilities":
                f.write('{"return": {}}\n')
            else:
                f.write('{"event": "x"}\n{"return": %s}\n' % json.dumps(reply))
            f.flush()
            if msg["execute"] != "qmp_capabilities":
                break
        conn.close()
        srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


# ── serial log ───────────────────────────────────────────────────────

def test_serial_log_appends_and_rotates_within_its_cap(diag, tmp_path):
    log = diag.SerialLog(7, base=str(tmp_path), max_bytes=100, files=3)
    for n in range(10):
        log.write(b"x" * 40 + b"%d\n" % n)
    log.close()
    names = sorted(p.name for p in (tmp_path / "7").iterdir())
    assert names == ["serial.log", "serial.log.1", "serial.log.2"]
    assert all(p.stat().st_size <= 100 for p in (tmp_path / "7").iterdir())
    assert b"9\n" in (tmp_path / "7/serial.log").read_bytes()


def test_serial_log_marks_a_reconnect_and_survives_a_write_error(diag, tmp_path):
    log = diag.SerialLog(7, base=str(tmp_path))
    log.mark("serial console connected")
    assert b"serial console connected" in (tmp_path / "7/serial.log").read_bytes()
    log._fh.close()  # writing to a closed file raises ValueError, a full disk raises OSError: both stop the copy quietly
    blocker = tmp_path / "blocked"
    blocker.write_text("file, not a directory")
    bad = diag.SerialLog(8, base=str(blocker))
    bad.write(b"data")
    assert bad.failed
    bad.write(b"more")
    bad.close()


def test_serial_log_keeps_writing_after_rotation_reopens(diag, tmp_path):
    log = diag.SerialLog(7, base=str(tmp_path), max_bytes=10, files=2)
    log.write(b"123456789")
    log.write(b"abc")
    log.close()
    assert (tmp_path / "7/serial.log.1").read_bytes() == b"123456789"
    assert (tmp_path / "7/serial.log").read_bytes() == b"abc"


def test_tail_bytes_reads_the_end_and_tolerates_a_missing_file(diag, tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"0123456789")
    assert diag.tail_bytes(str(f), 4) == b"6789"
    assert diag.tail_bytes(str(tmp_path / "none"), 4) == b""


# ── readers ──────────────────────────────────────────────────────────

def test_igpu_registers_come_from_bar0_and_all_ones_is_called_out(diag, tmp_path):
    sysfs = fake_sysfs(tmp_path, {0x2030: 0x1234, 0x22034: 0x40, 0xCEB8: 0x7F3})
    regs = diag.read_igpu_registers(str(sysfs))
    assert regs["RCS_TAIL"] == 0x1234 and regs["BCS_HEAD"] == 0x40 and regs["FAULT_TLB_DATA0"] == 0x7F3
    text = diag.format_registers(regs)
    assert "RCS_TAIL" in text and "ALL ONES" not in text
    assert "ALL ONES" in diag.format_registers({"A": 0xFFFFFFFF, "B": 0xFFFFFFFF})


def test_igpu_registers_beyond_a_small_bar_are_left_out(diag, tmp_path):
    sysfs = fake_sysfs(tmp_path, size=0x30000)
    regs = diag.read_igpu_registers(str(sysfs))
    assert "BCS_TAIL" in regs and "PLANE_SURF_1_A" not in regs


def test_host_snapshot_has_temperature_throttling_and_identity(diag, tmp_path):
    sysfs = fake_sysfs(tmp_path / "sys")
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor: 0\nmodel name\t: 12th Gen Intel(R) Core(TM) i5-1240P\n")
    load = tmp_path / "loadavg"
    load.write_text("0.5 0.4 0.3 1/200 99\n")
    text = diag.host_snapshot(str(sysfs), str(load), str(cpuinfo))
    assert "i5-1240P" in text and "8086:46a6" in text and "coretemp Package id 0: 94.0 C" in text
    assert "cores 7, package 4000000" in text and "1200 MHz" in text and "0.5 0.4 0.3" in text


def test_host_snapshot_survives_an_empty_sysfs(diag, tmp_path):
    text = diag.host_snapshot(str(tmp_path), str(tmp_path / "x"), str(tmp_path / "y"))
    assert "cpu: unknown" in text and "package 0" in text and "unknown" in text


def test_redact_config_blanks_identity_and_drops_snapshots(diag):
    out = diag.redact_config(CONFIG)
    assert "BC:24:11" not in out and "11:22:33" not in out and "snap" not in out
    assert "secret" not in out and "QUJD" not in out and "1234" not in out and "abc" not in out
    assert "machine: pc" in out and "xx:xx:xx:xx:xx:xx" in out


def test_macos_of_reads_the_description_or_the_name(diag):
    assert diag.macos_of(CONFIG) == "Tahoe"
    assert diag.macos_of("name: macos-sonoma\n") == "Sonoma"
    assert diag.macos_of("name: other\n") == "unknown"


def test_versions_are_read_from_the_state_file_and_described(diag, tmp_path):
    assert diag.read_versions(5, str(tmp_path)) == {}
    (tmp_path / "5.json").write_text(json.dumps({"whateverxe_version": "0.9.0", "whateverxe_source": "latest",
                                                  "graphics_version": "0.4.1", "graphics_source": "chosen",
                                                  "amfipass_version": "1.4.1-Mod"}))
    lines = diag.describe_versions(diag.read_versions(5, str(tmp_path)))
    assert lines == ["WhateverXe 0.9.0 (latest when installed)", "WhateverXe Graphics 0.4.1 (chosen by the user)",
                     "AMFIPass 1.4.1-Mod (pinned)"]
    (tmp_path / "6.json").write_text("[1]")
    assert diag.read_versions(6, str(tmp_path)) == {}


def test_panic_text_starts_at_the_last_panic_line(diag):
    lines = ["boot", PANIC, "Backtrace", "more"]
    assert diag.panic_text(lines).splitlines() == [PANIC, "Backtrace", "more"]
    assert diag.panic_text(["a", "b"]).splitlines() == ["a", "b"]


def test_qmp_hmp_returns_the_monitor_text_and_reports_errors(diag, sockdir):
    tmp_path = sockdir
    sock = tmp_path / "9.qmp"
    thread = qmp_server(sock)
    assert "RIP=ffff" in diag.qmp_hmp(9, "info registers -a", 3, str(tmp_path))
    thread.join(2)
    with pytest.raises(OSError):
        diag.qmp_hmp(10, "info registers -a", 0.3, str(tmp_path))


def test_qmp_hmp_raises_when_qemu_answers_with_an_error(diag, sockdir):
    tmp_path = sockdir
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(tmp_path / "11.qmp"))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rw")
        f.write('{"QMP": {}}\n')
        f.flush()
        for line in f:
            f.write('{"return": {}}\n' if "capabilities" in line else '{"error": {"desc": "nope"}}\n')
            f.flush()
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(OSError, match="nope"):
        diag.qmp_hmp(11, "x", 3, str(tmp_path))
    srv.close()


def test_qmp_hmp_raises_when_qemu_closes_the_socket(diag, sockdir):
    tmp_path = sockdir
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(tmp_path / "12.qmp"))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b'{"QMP": {}}\n')
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(OSError):  # "QMP closed", or a broken pipe when the close wins the race
        diag.qmp_hmp(12, "x", 3, str(tmp_path))
    srv.close()


# ── incident ─────────────────────────────────────────────────────────

def collect(diag, tmp_path, sockdir=None, **kw):
    sysfs = fake_sysfs(tmp_path / "sys", {0x2030: 5})
    base = tmp_path / "log"
    (base / "9").mkdir(parents=True, exist_ok=True)
    (base / "9/serial.log").write_bytes(b"line1\n" + PANIC.encode() + b"\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "9.json").write_text(json.dumps({"whateverxe_version": "0.9.0", "whateverxe_source": "latest"}))
    os.environ["IGPU_STATE_DIR"] = str(state)
    args = dict(base=str(base), runner=runner(), sysfs=str(sysfs), sock_dir=str(sockdir or tmp_path), stamp="20260101T000000Z")
    args.update(kw)
    return Path(diag.collect_incident(9, "panic", "macOS panicked", ["x", PANIC, "trace"], **args))


@pytest.fixture(autouse=True)
def _state_env():
    old = os.environ.get("IGPU_STATE_DIR")
    yield
    if old is None:
        os.environ.pop("IGPU_STATE_DIR", None)
    else:
        os.environ["IGPU_STATE_DIR"] = old


def test_incident_folder_has_every_part(diag, tmp_path, sockdir):
    qmp_server(sockdir / "9.qmp")
    inc = collect(diag, tmp_path, sockdir)
    names = sorted(p.name for p in inc.iterdir())
    assert names == ["dmesg.txt", "host.txt", "igpu-registers.txt", "incident.json", "panic.txt", "serial-tail.txt",
                     "vcpu-registers.txt", "versions.json", "vm.conf"]
    assert inc.name == "20260101T000000Z-panic"
    assert PANIC in (inc / "panic.txt").read_text() and "RCS_TAIL" in (inc / "igpu-registers.txt").read_text()
    assert "RIP=ffff" in (inc / "vcpu-registers.txt").read_text()
    assert "vfio-pci" in (inc / "dmesg.txt").read_text() and "usb 1-1" not in (inc / "dmesg.txt").read_text()
    assert "secret" not in (inc / "vm.conf").read_text()
    assert json.loads((inc / "versions.json").read_text())["macos_detected"] == "Tahoe"
    meta = json.loads((inc / "incident.json").read_text())
    assert meta["kind"] == "panic" and meta["skipped"] == [] and meta["seconds"] < diag.BUDGET


def test_a_failing_part_is_recorded_and_the_rest_still_written(diag, tmp_path):
    inc = collect(diag, tmp_path, runner=runner(conf="", dmesg=""), sysfs=str(tmp_path / "nowhere"))
    meta = json.loads((inc / "incident.json").read_text())
    skipped = " ".join(meta["skipped"])
    assert "vcpu-registers.txt" in skipped and "vm.conf" in skipped and "igpu-registers.txt" in skipped
    assert (inc / "panic.txt").exists() and (inc / "serial-tail.txt").exists()


def test_dmesg_failure_is_recorded(diag, tmp_path):
    def run(argv, timeout=5):
        return 1, "boom"
    inc = collect(diag, tmp_path, runner=run)
    assert "dmesg.txt: dmesg failed" in " ".join(json.loads((inc / "incident.json").read_text())["skipped"])


def test_parts_are_skipped_once_the_budget_is_used_up(diag, tmp_path):
    ticks = iter(range(0, 1000, 5))
    inc = collect(diag, tmp_path, clock=lambda: next(ticks), budget=8)
    meta = json.loads((inc / "incident.json").read_text())
    assert any("out of time" in s for s in meta["skipped"])
    assert (inc / "incident.json").exists()


def test_snapshot_files_are_size_capped(diag, tmp_path):
    big = "x" * (diag.FILE_MAX * 2)
    inc = collect(diag, tmp_path, runner=runner(dmesg="vfio " + big))
    assert (inc / "dmesg.txt").stat().st_size <= diag.FILE_MAX + 20


def test_old_incidents_are_pruned_to_the_newest_ten(diag, tmp_path):
    for n in range(13):
        collect(diag, tmp_path, stamp="2026010%02dT000000Z" % n)
    kept = sorted(p.name for p in (tmp_path / "log/9/incidents").iterdir())
    assert len(kept) == diag.INCIDENT_KEEP and kept[0].startswith("2026010" + "03")
    diag.prune(str(tmp_path / "nope"))  # a missing folder is fine


# ── report ───────────────────────────────────────────────────────────

def test_report_bundles_incidents_versions_and_a_pasteable_summary(diag, tmp_path, sockdir):
    qmp_server(sockdir / "9.qmp")
    inc = collect(diag, tmp_path, sockdir)
    path, summary = diag.build_report(9, str(tmp_path / "out"), base=str(tmp_path / "log"), runner=runner(),
                                      sysfs=str(tmp_path / "sys"), stamp="20260102T000000Z")
    assert path.endswith("igpu-report-9-20260102T000000Z.tar.gz")
    with tarfile.open(path) as tar:
        names = tar.getnames()
        text = tar.extractfile("igpu-report-9-20260102T000000Z/summary.txt").read().decode()
    top = "igpu-report-9-20260102T000000Z/"
    for want in ("versions.json", "vm.conf", "host.txt", "serial-tail.txt", "summary.txt",
                 "incidents/%s/panic.txt" % inc.name):
        assert top + want in names
    assert text == summary
    assert "VM 9" in summary and "macOS: Tahoe" in summary and "WhateverXe 0.9.0 (latest when installed)" in summary
    assert "Crashes recorded: 1" in summary and "setPowerState timeout" in summary
    assert "Nothing was sent anywhere" in summary and "github.com/b00t0x/WhateverXe/issues" in summary
    assert "secret" not in summary


def test_report_without_incidents_or_state_says_so(diag, tmp_path):
    os.environ["IGPU_STATE_DIR"] = str(tmp_path / "nostate")
    path, summary = diag.build_report(4, str(tmp_path / "out"), base=str(tmp_path / "log"), runner=runner(conf=""),
                                      sysfs=str(tmp_path / "sys"))
    assert os.path.exists(path)
    assert "No crash was recorded yet" in summary and "unknown (this VM was set up before" in summary
    assert "macOS: unknown" in summary


def test_report_takes_the_config_from_the_last_incident_when_qm_fails(diag, tmp_path):
    collect(diag, tmp_path)
    _path, summary = diag.build_report(9, str(tmp_path / "out"), base=str(tmp_path / "log"),
                                      runner=runner(conf=""), sysfs=str(tmp_path / "sys"))
    assert "macOS: Tahoe" in summary


def test_report_tolerates_an_incident_without_its_meta_file(diag, tmp_path):
    inc = collect(diag, tmp_path)
    (inc / "incident.json").unlink()
    _path, summary = diag.build_report(9, str(tmp_path / "out"), base=str(tmp_path / "log"), runner=runner(),
                                      sysfs=str(tmp_path / "sys"))
    assert "?: " in summary


# ── command line and the package wrapper ─────────────────────────────

def test_main_report_incident_and_versions(diag, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("IGPU_LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setenv("IGPU_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GPU_SYSFS", str(fake_sysfs(tmp_path / "sys")))
    monkeypatch.setattr(diag, "run", lambda argv, timeout=5: (1, "none"))
    assert diag.main(["x", "incident", "3", "by", "hand"]) == 0
    assert "incidents" in capsys.readouterr().out
    assert diag.main(["x", "report", "3", "--out", str(tmp_path / "o")]) == 0
    assert "iGPU report for VM 3" in capsys.readouterr().out
    assert diag.main(["x", "report", "3"]) == 0
    capsys.readouterr()
    assert diag.main(["x", "versions", "3"]) == 0
    assert diag.main(["x"]) == 64


def test_run_helper_reports_missing_commands(diag):
    assert diag.run(["/nonexistent-binary-xyz"])[0] == 1
    assert diag.run(["true"])[0] == 0


def test_the_package_wrapper_runs_the_same_file(tmp_path, monkeypatch):
    monkeypatch.setenv("IGPU_LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setenv("IGPU_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GPU_SYSFS", str(fake_sysfs(tmp_path / "sys")))
    igpu_engine.write_state(3, igpu_engine.picks_state(
        {"whateverxe": {"version": "0.9.0", "tag": "0.9.0", "sha256": "a" * 64, "source": "latest"}}, "tahoe"))
    path, summary = igpu_report.report(3, tmp_path / "o")
    assert path.exists() and "WhateverXe 0.9.0" in summary
    assert igpu_report.versions_lines(3)[0].startswith("WhateverXe 0.9.0")


def test_the_recorder_and_the_engine_describe_the_versions_the_same_way(diag):
    for state in ({}, {"whateverxe_version": "0.9.0", "whateverxe_source": "latest", "graphics_version": "0.4.0",
                       "graphics_source": "chosen", "amfipass_version": "1.4.1-Mod"}):
        assert diag.describe_versions(state) == igpu_engine.describe_state(state)


def test_the_recorders_register_offsets_name_the_rings_and_the_fault_registers(diag):
    names = diag.IGPU_REGS
    for want in ("RCS_HEAD", "RCS_TAIL", "BCS_HEAD", "BCS_TAIL", "PLANE_SURF_1_A", "FAULT_TLB_DATA0", "FAULT_TLB_DATA1"):
        assert want in names
    assert names["BCS_TAIL"] == igpu_engine.ACCEL_REGS["BCS_TAIL"] and names["RCS_TAIL"] == igpu_engine.ACCEL_REGS["RCS_TAIL"]
    assert names["PLANE_SURF_1_A"] == igpu_engine.ACCEL_REGS["PLANE_SURF_1_A"]
    assert all(off % 4 == 0 for off in names.values())  # 32-bit reads only


# ── audit fixes ──────────────────────────────────────────────────────

def test_the_register_values_are_read_as_32_bit_words(diag, tmp_path):
    sysfs = fake_sysfs(tmp_path, {0x2030: 0xDEADBEEF, 0x2034: 0x01020304})
    regs = diag.read_igpu_registers(str(sysfs))
    assert regs["RCS_TAIL"] == 0xDEADBEEF and regs["RCS_HEAD"] == 0x01020304


def test_qmp_gives_up_at_the_deadline_even_while_events_keep_arriving(diag, sockdir):
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sockdir / "13.qmp"))
    srv.listen(1)
    stop = threading.Event()

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b'{"QMP": {}}\n')
        conn.recv(4096)
        while not stop.is_set():
            try:
                conn.sendall(b'{"event": "noise"}\n')
            except OSError:
                break
            stop.wait(0.02)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    import time
    began = time.monotonic()
    with pytest.raises(OSError, match="did not answer in time"):
        diag.qmp_hmp(13, "info registers -a", 0.5, str(sockdir))
    stop.set()
    srv.close()
    assert time.monotonic() - began < 2


def test_a_greeting_that_arrives_in_pieces_is_still_read_as_one_line(diag, sockdir):
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(sockdir / "14.qmp"))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.sendall(b'{"QMP": {"vers')
        threading.Event().wait(0.1)
        conn.sendall(b'ion": 1}}\n')
        f = conn.makefile("rw")
        for text in f:
            f.write('{"return": {}}\n' if "capabilities" in text else '{"return": "RIP=1"}\n')
            f.flush()
            if "human" in text:
                break
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    assert diag.qmp_hmp(14, "x", 3, str(sockdir)) == "RIP=1"
    srv.close()


def test_the_vcpu_dump_is_taken_last_so_a_stuck_monitor_costs_only_the_leftover_budget(diag, tmp_path, sockdir):
    inc = collect(diag, tmp_path, sockdir)  # no QMP server in sockdir: the dump fails, everything else is on disk
    meta = json.loads((inc / "incident.json").read_text())
    assert meta["files"][-1] == "versions.json" and "vcpu-registers.txt" in " ".join(meta["skipped"])
    assert {"panic.txt", "igpu-registers.txt", "host.txt", "vm.conf", "dmesg.txt"} <= set(meta["files"])


def test_redaction_also_covers_keys_addresses_generation_id_and_free_text(diag):
    conf = ("description: macOS%20Sequoia%2015%20VM, secret note\nvmgenid: c8eeeaae-282e\nipconfig0: ip=10.0.0.5/24\n"
            "cipassword: hunter2\nsshkeys: ssh-rsa%20AAAA\nname: macos-sequoia\n")
    out = diag.redact_config(conf)
    assert "secret note" not in out and "c8eeeaae" not in out and "10.0.0.5" not in out
    assert "hunter2" not in out and "AAAA" not in out
    assert "description: macOS Sequoia" in out and "name: macos-sequoia" in out
    assert diag.redact_config("description: just a note\n") == "description: (redacted)\n"


def test_old_incident_folders_are_removed_even_with_a_subfolder_inside(diag, tmp_path):
    root = tmp_path / "incidents"
    for name in ("a", "b", "c"):
        (root / name / "sub").mkdir(parents=True)
        (root / name / "sub" / "f").write_text("x")
    diag.prune(str(root), keep=1)
    assert [p.name for p in root.iterdir()] == ["c"]


def test_the_report_summary_says_what_is_and_is_not_blanked(diag, tmp_path):
    _path, summary = diag.build_report(9, str(tmp_path / "o"), base=str(tmp_path / "l"), runner=runner(), sysfs=str(tmp_path))
    assert "serial log and the host kernel messages are copied as they are" in summary


def test_the_panic_text_of_a_nested_panic_starts_at_its_first_line(diag):
    lines = ["boot", "Nested panic detected - entry count: 2", "Double fault at 0x1, registers:", "CR0: 0x1"]
    assert diag.panic_text(lines).splitlines()[0].startswith("Nested panic detected")


def test_a_nested_panic_keeps_the_panic_it_follows_but_an_old_one_is_left_out(diag):
    first = ["old", PANIC, "trace"] + ["noise"] * 500 + [PANIC, "trace2"] + ["x"] * 5 + ["Nested panic detected - entry count: 2"]
    assert diag.panic_text(first).splitlines()[:2] == [PANIC, "trace2"]
