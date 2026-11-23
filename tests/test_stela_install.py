"""The install driver: the menu keys, the serial progress and the finish, against a fake host with a fake clock."""
from __future__ import annotations

import socket
import threading
from types import SimpleNamespace

import pytest

from osx_proxmox_next import stela_install
from osx_proxmox_next.stela import StelaConfig
from osx_proxmox_next.stela_install import MENU_KEYS, NEEDS_GPU, Limits, PveHost, default_log_path, run_install

MENU = ["STELA UEFI MOUNT version=0.5.0", "HOLD No disk is chosen for macOS yet: choose Install macOS first."]
CHOSEN = ["CHOICE 2 (Install macOS)"]
INSTALL = ["STEP Starting the macOS installer t=178.6", "STEP DETAIL installer: format media | 5 blocks executed ms=1000"]
RESTART = ["STEP Restarting the PC t=2835.4", "STELA RESTART code=0x15"]
MACOS = ["STEP Starting macOS t=17.9"]


class FakeHost:
    """Serves one batch of serial lines per poll, on a clock that only the host moves.

    `dhcp_on` lists the polls (counted from 0) during which the guest sends a DHCP request."""

    def __init__(self, batches, dhcp_on=()):
        self.batches = list(batches)
        self.dhcp_on = set(dhcp_on)
        self.polls = 0
        self.dhcp: list[float] = []
        self.clock = 0.0
        self.keys: list[tuple[float, str]] = []

    def poll(self, timeout):
        self.clock += timeout
        if self.polls in self.dhcp_on:
            self.dhcp.append(self.clock)
        self.polls += 1
        return self.batches.pop(0) if self.batches else []

    def sendkey(self, key):
        self.keys.append((self.clock, key))

    def dhcp_times(self):
        return list(self.dhcp)

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds


def run(host, limits=Limits(), resume=False):
    said: list[str] = []
    return run_install(host, said.append, limits, resume), said


def test_a_full_install_sends_the_menu_keys_once_and_ends_at_setup_assistant():
    host = FakeHost([MENU, CHOSEN, INSTALL, [], RESTART, MACOS, [], [], []], dhcp_on=[6])
    result, said = run(host, Limits(dhcp_grace_s=2.0))
    assert result.ok and "Setup Assistant" in result.message
    assert [k for _, k in host.keys] == list(MENU_KEYS) == ["ret", "ret", "ret", "ret", "tab", "ret"]
    assert list(result.seconds) == ["menu", "install chosen", "installer started", "installer done",
                                    "macos first start", "guest dhcp", "setup assistant"]
    assert result.seconds["guest dhcp"] < result.seconds["setup assistant"]
    assert "Stela's menu is up" in said and "Installer: format media" in said
    assert any("Starting the installed macOS (boot 1)" in line for line in said)


def test_the_keys_wait_for_the_menu_to_settle_and_go_two_seconds_apart():
    host = FakeHost([MENU, CHOSEN, INSTALL, RESTART, MACOS], dhcp_on=[5])
    run(host, Limits(dhcp_grace_s=1.0, settle_s=6.0, key_gap_s=2.0))
    times = [t for t, _ in host.keys]
    assert times[0] >= 7.0 and all(b - a == 2.0 for a, b in zip(times, times[1:]))


def test_a_dhcp_request_from_the_installer_before_macos_starts_does_not_finish_it():
    host = FakeHost([MENU, CHOSEN, INSTALL, RESTART, [], [], []], dhcp_on=[2])
    result, _ = run(host, Limits(stall_s=3.0, dhcp_grace_s=1.0))
    assert not result.ok and "No output" in result.message


def test_a_restart_after_the_dhcp_request_means_it_was_not_the_last_boot():
    # the first start of the installed macOS asks for an address, then Stela restarts the PC for its repair step
    host = FakeHost([MENU, CHOSEN, INSTALL, RESTART, MACOS, [], [], RESTART, MACOS] + [[]] * 12, dhcp_on=[5, 10])
    result, said = run(host, Limits(dhcp_grace_s=6.0, stall_s=500))
    assert result.ok
    assert host.polls >= 16                             # decided on the later boot, not on the early request
    assert any("restarted the PC again" in line for line in said)


def test_no_gpu_after_the_install_says_macos_is_on_the_disk_and_how_to_add_a_card():
    gpu_fatal = "STELA FATAL macOS cannot run without a GPU: no GPU started; the lines above say why"
    host = FakeHost([MENU, CHOSEN, INSTALL, RESTART, MACOS, [gpu_fatal]])
    result, said = run(host)
    assert not result.ok and result.message == NEEDS_GPU
    assert "stela finish --vmid <id> --gpu-pci" in said[-1]


def test_the_same_fatal_before_the_install_is_just_a_stela_stop():
    host = FakeHost([MENU, ["STELA FATAL macOS cannot run without a GPU: no GPU started"]])
    result, _ = run(host)
    assert not result.ok and result.message.startswith("Stela stopped: macOS cannot run")


def test_resume_sends_no_keys_and_ends_when_the_installed_macos_asks_for_an_address():
    host = FakeHost([["STELA UEFI MOUNT version=0.5.0", "CHOICE 1 (macOS)"], MACOS, [], []], dhcp_on=[2])
    result, said = run(host, Limits(dhcp_grace_s=1.0), resume=True)
    assert result.ok and host.keys == []
    assert list(result.seconds) == ["macos first start", "guest dhcp", "setup assistant"]
    assert not any("menu" in line.lower() for line in said)


def test_a_stela_fatal_line_ends_it_with_the_reason():
    host = FakeHost([MENU, ["STELA FATAL Installing macOS needs a network. No supported network adapter."]])
    result, said = run(host)
    assert not result.ok and "No supported network adapter" in result.message and said[-1] == result.message


def test_keys_that_never_choose_the_install_are_reported():
    host = FakeHost([MENU] + [[]] * 200)
    result, _ = run(host, Limits(chosen_timeout_s=20.0, stall_s=10_000))
    assert not result.ok and "menu keys had no effect" in result.message


def test_silence_from_stela_is_a_failure_with_its_phase():
    host = FakeHost([MENU, CHOSEN, INSTALL] + [[]] * 50)
    result, _ = run(host, Limits(stall_s=10.0))
    assert not result.ok and "No output from Stela for 0 minutes in the phase 'installing'" in result.message


def test_the_total_time_limit_ends_a_run_that_keeps_talking():
    host = FakeHost([MENU, CHOSEN, INSTALL] + [["STEP DETAIL installer: format media | 1 ms=1"]] * 100)
    result, _ = run(host, Limits(total_s=30.0))
    assert not result.ok and "Gave up after 0 minutes" in result.message


# ── the real host: serial socket, qm sendkey, packet socket ──────────────────────────────────────────────

def config():
    return StelaConfig(vmid=478, name="macos27-stela", storage="local-lvm")


def test_the_serial_socket_is_read_into_lines_and_a_log_file(tmp_path):
    sock_path = tmp_path / "478.serial0"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        conn.sendall(b"HOLD first\r\nSTEP Starting macOS t=1.0\npartial")
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    host = PveHost(config(), tmp_path / "logs" / "s.serial.log")
    host.serial_path = str(sock_path)
    lines: list[str] = []
    with host:
        while len(lines) < 2:
            lines += host.poll(2.0)
    thread.join(timeout=5)
    server.close()
    assert lines == ["HOLD first\r", "STEP Starting macOS t=1.0"]
    assert (tmp_path / "logs" / "s.serial.log").read_bytes() == b"HOLD first\r\nSTEP Starting macOS t=1.0\npartial"


def test_poll_returns_nothing_when_the_serial_port_is_quiet(tmp_path):
    host = PveHost(config(), tmp_path / "s.log")
    assert host.poll(0.01) == []


def test_sendkey_runs_qm_sendkey(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(stela_install.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    PveHost(config(), tmp_path / "s.log").sendkey("tab")
    assert calls[0][0] == ["qm", "sendkey", "478", "tab"] and calls[0][1]["check"] is True


class FakeSniffer:
    def __init__(self, frames):
        self.frames = list(frames)
        self.bound = None
        self.closed = False

    def settimeout(self, _t):
        pass

    def bind(self, addr):
        self.bound = addr

    def recv(self, _n):
        if not self.frames:
            raise socket.timeout()
        return self.frames.pop(0)

    def close(self):
        self.closed = True


def dhcp_frame(mac_bytes):
    eth = b"\xff" * 6 + mac_bytes + b"\x08\x00"
    ip = bytes([0x45, 0, 0, 28, 0, 0, 0, 0, 64, 17, 0, 0]) + bytes(4) + b"\xff" * 4
    udp = (68).to_bytes(2, "big") + (67).to_bytes(2, "big") + bytes([0, 8, 0, 0])
    return eth + ip + udp


def test_the_packet_socket_records_only_dhcp_from_the_guest_mac(monkeypatch, tmp_path):
    mine = bytes.fromhex("BC24110001DE")
    sniffer = FakeSniffer([dhcp_frame(bytes.fromhex("BC24110001DF")), b"short", dhcp_frame(mine)])
    attempts = []

    def make_socket(family, kind, proto):
        attempts.append((family, kind))
        if len(attempts) == 1:
            raise OSError("no such device yet")     # the tap does not exist until the VM starts
        return sniffer

    fake_socket = SimpleNamespace(socket=make_socket, AF_PACKET=17, SOCK_RAW=3, htons=socket.htons, timeout=socket.timeout)
    monkeypatch.setattr(stela_install, "socket", fake_socket)
    host = PveHost(config(), tmp_path / "s.log")
    host._threads = [threading.Thread(target=host._sniff, daemon=True)]
    with host:
        while not host.dhcp_times():
            host.sleep(0.05)
    assert len(host.dhcp_times()) == 1 and sniffer.bound == ("tap478x", 0)
    assert host.now() >= host.dhcp_times()[0]


def test_the_default_log_path_is_per_vm_under_generated_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = default_log_path(478)
    assert path.parent == tmp_path / "generated" / "logs" and path.name.startswith("stela-478-")
    assert path.name.endswith(".serial.log")


@pytest.mark.parametrize("key", MENU_KEYS)
def test_every_menu_key_is_a_qm_key_name(key):
    assert key in ("ret", "tab")


def test_a_failed_sendkey_is_a_clear_error(monkeypatch, tmp_path):
    def broken(*a, **kw):
        raise stela_install.subprocess.CalledProcessError(1, "qm")

    monkeypatch.setattr(stela_install.subprocess, "run", broken)
    with pytest.raises(stela_install.SendKeyError, match="qm sendkey tab failed"):
        PveHost(config(), tmp_path / "s.log").sendkey("tab")
