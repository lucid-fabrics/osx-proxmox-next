"""Drives a Stela install from the Proxmox host: the keys for its menu, the serial log, and the end.

The VM is started by the plan. From there everything Stela needs is the keyboard (`qm sendkey`), and everything it
says comes back on the serial port. The install is over when the installed macOS asks the network for an address
(the DHCP request of Setup Assistant) and does not restart again straight after."""

from __future__ import annotations

import queue
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .stela import StelaConfig, mac_for, tap_for
from .stela_progress import StelaTracker, is_guest_dhcp

__all__ = ["InstallResult", "Limits", "NEEDS_GPU", "PveHost", "StelaHost", "default_log_path", "run_install"]

# Stela's menu, as the live run found it (screens read with a screendump): Install macOS, the disk, the free space,
# the size for macOS, then on the confirm screen Tab from Back over to Install, and Install.
MENU_KEYS = ("ret", "ret", "ret", "ret", "tab", "ret")


@dataclass(frozen=True)
class Limits:
    settle_s: float = 6.0          # after the menu appears: the USB keyboard needs a moment before it listens
    key_gap_s: float = 2.0
    chosen_timeout_s: float = 90.0   # keys sent, but no "Install macOS was chosen" line
    stall_s: float = 900.0         # no serial line at all for this long
    total_s: float = 3 * 3600.0
    dhcp_grace_s: float = 45.0     # the installed macOS restarts on its own after its first start: a DHCP request must survive this


@dataclass
class InstallResult:
    ok: bool
    message: str
    seconds: dict[str, float] = field(default_factory=dict)     # when each phase was first reached, from the start


class StelaHost(Protocol):
    def poll(self, timeout: float) -> list[str]: ...
    def sendkey(self, key: str) -> None: ...
    def dhcp_times(self) -> list[float]: ...
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


NEEDS_GPU = ("macOS is installed on the VM's disk, but Stela cannot start it without a GPU. Pass a host GPU to the VM "
             "with: osx-next-cli stela finish --vmid <id> --gpu-pci <card>")


def run_install(host: StelaHost, say: Callable[[str], None], limits: Limits = Limits(),
                resume: bool = False) -> InstallResult:
    """Run until the install is done or cannot go on. The caller stops the VM on a failure.

    With `resume` the macOS install is already on the disk: no menu keys, the run only waits for the installed
    macOS to boot to Setup Assistant."""
    tracker = StelaTracker(installer_started=resume, installer_done=resume, install_chosen=resume, menu_ready=resume)
    start = host.now()
    seconds: dict[str, float] = {}
    keys_at = 0.0
    keys_sent = resume
    last_line_at = start
    macos_seen = 0
    macos_start_t = 0.0

    def mark(name: str) -> None:
        seconds.setdefault(name, round(host.now() - start, 1))

    def fail(why: str) -> InstallResult:
        say(why)
        return InstallResult(False, why, seconds)

    while True:
        now = host.now()
        if now - start > limits.total_s:
            return fail(f"Gave up after {int(limits.total_s // 60)} minutes in the phase '{tracker.phase}'.")
        lines = host.poll(1.0)
        if lines:
            last_line_at = host.now()
        for line in lines:
            for message in tracker.feed(line):
                say(message)
        if tracker.fatal:
            if tracker.installer_done and "without a GPU" in tracker.fatal:
                return fail(NEEDS_GPU)
            return fail(f"Stela stopped: {tracker.fatal}")
        if tracker.menu_ready and not resume:
            mark("menu")
        if not keys_sent and tracker.menu_ready:
            host.sleep(limits.settle_s)
            for key in MENU_KEYS:
                host.sendkey(key)
                host.sleep(limits.key_gap_s)
            keys_sent, keys_at = True, host.now()
            last_line_at = keys_at      # the lines that came in meanwhile are still to be read
            say("Sent the menu keys")
        if tracker.install_chosen:
            if not resume:
                mark("install chosen")
        elif keys_sent and host.now() - keys_at > limits.chosen_timeout_s:
            return fail("The menu keys had no effect: Stela never reported that the install was chosen.")
        if tracker.installer_started and not resume:
            mark("installer started")
        if tracker.installer_done and not resume:
            mark("installer done")
        if tracker.macos_started > macos_seen:
            macos_seen = tracker.macos_started
            macos_start_t = host.now()
            mark("macos first start")
        if macos_seen and not tracker.restarted_since_macos_start:
            requests = [t for t in host.dhcp_times() if t >= macos_start_t]
            if requests:
                seconds.setdefault("guest dhcp", round(requests[0] - start, 1))
            if requests and host.now() - requests[0] >= limits.dhcp_grace_s:
                mark("setup assistant")
                say("macOS is up: it asked the network for an address (Setup Assistant)")
                return InstallResult(True, "macOS 27 is installed and booted to Setup Assistant.", seconds)
        if host.now() - last_line_at > limits.stall_s:
            return fail(f"No output from Stela for {int(limits.stall_s // 60)} minutes in the phase '{tracker.phase}'.")


class PveHost:
    """The real thing: the VM's serial socket, `qm sendkey`, and a packet socket on the guest NIC's tap."""

    def __init__(self, config: StelaConfig, log_path: Path) -> None:
        self.vmid = str(config.vmid)
        self.mac = mac_for(config.vmid)
        self.tap = tap_for(config.vmid)
        self.serial_path = f"/var/run/qemu-server/{config.vmid}.serial0"
        self.log_path = log_path
        self._lines: queue.Queue[str] = queue.Queue()
        self._dhcp: list[float] = []
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._read_serial, daemon=True),
                         threading.Thread(target=self._sniff, daemon=True)]
        self._socks: list[socket.socket] = []

    def __enter__(self) -> "PveHost":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        for thread in self._threads:
            thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        for sock in self._socks:
            try:
                sock.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=5)

    def _read_serial(self) -> None:
        with self.log_path.open("ab") as log:
            while not self._stop.is_set():
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                self._socks.append(sock)
                try:
                    sock.connect(self.serial_path)
                except OSError:
                    sock.close()
                    self._stop.wait(1.0)
                    continue
                pending = b""
                while not self._stop.is_set():
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    log.write(data)
                    log.flush()
                    *whole, pending = (pending + data).split(b"\n")
                    for raw in whole:
                        self._lines.put(raw.decode("utf-8", "replace"))
                sock.close()

    def _sniff(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))   # 3 = every protocol
                sock.settimeout(1.0)
                sock.bind((self.tap, 0))
            except OSError:
                self._stop.wait(1.0)    # the tap does not exist until the VM starts
                continue
            self._socks.append(sock)
            while not self._stop.is_set():
                try:
                    frame = sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if is_guest_dhcp(frame, self.mac):
                    self._dhcp.append(time.monotonic())
            sock.close()

    def poll(self, timeout: float) -> list[str]:
        out: list[str] = []
        try:
            out.append(self._lines.get(timeout=timeout))
            while True:
                out.append(self._lines.get_nowait())
        except queue.Empty:
            pass
        return out

    def sendkey(self, key: str) -> None:
        subprocess.run(["qm", "sendkey", self.vmid, key], check=True, capture_output=True, timeout=30)

    def dhcp_times(self) -> list[float]:
        return list(self._dhcp)

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def default_log_path(vmid: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path.cwd() / "generated" / "logs" / f"stela-{vmid}-{stamp}.serial.log"
