"""Reads Stela's serial log, line by line: where the install is, and whether it has stopped for good.

Stela prints its whole log on the VM's serial port. The lines this reads are the ones Stela writes in its own words
(`STEP`, `STELA`, `HOLD`, `CHOICE`); everything else (the macOS guest's own kernel lines, device traces) is ignored."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["StelaTracker", "is_guest_dhcp"]

_MENU_RE = re.compile(r"^HOLD ")                                  # the boot menu is up and waiting for a key
_CHOSEN_RE = re.compile(r"^CHOICE \d+ \(Install macOS\)")         # the install was picked: the keys worked
_STEP_RE = re.compile(r"^STEP (?![owd] |DETAIL )(?P<title>.+?) t=[0-9.]+$")
_DETAIL_RE = re.compile(r"^STEP DETAIL installer: (?P<what>[^|]+?)(?: \||\s+ms=|$)")
_FATAL_RE = re.compile(r"^STELA (?:FATAL|STOPPED)\b(?P<rest>.*)")
_RESTART_RE = re.compile(r"^STELA RESTART\b")
_INSTALLER_STEP = "Starting the macOS installer"
_MACOS_STEP = "Starting macOS"


@dataclass
class StelaTracker:
    """Feed it serial lines; it says what is new in plain words and keeps the state a driver acts on."""

    menu_ready: bool = False
    install_chosen: bool = False
    installer_started: bool = False
    installer_done: bool = False       # Stela restarted the PC after the installer ran
    macos_started: int = 0             # how many times the installed macOS was started since the installer finished
    last_macos_start_line: int = -1
    last_restart_line: int = -1
    fatal: str = ""
    phase: str = "starting"
    lines_seen: int = 0
    _last_detail: str = field(default="", repr=False)

    def feed(self, line: str) -> list[str]:
        """The progress messages this line makes (usually none)."""
        self.lines_seen += 1
        line = line.rstrip("\r\n")
        out: list[str] = []
        if not self.menu_ready and _MENU_RE.match(line):
            self.menu_ready = True
            self.phase = "menu"
            out.append("Stela's menu is up")
        if not self.install_chosen and _CHOSEN_RE.match(line):
            self.install_chosen = True
            self.phase = "install chosen"
            out.append("Install macOS was chosen")
        fatal = _FATAL_RE.match(line)
        if fatal and not self.fatal:
            self.fatal = (fatal.group("rest").strip() or line)[:300]
            self.phase = "stopped"
            out.append(f"Stela stopped: {self.fatal}")
        if _RESTART_RE.match(line):
            self.last_restart_line = self.lines_seen
            if self.installer_started and not self.installer_done:
                self.installer_done = True
                self.phase = "installed"
                out.append("The installer finished and Stela restarted the PC")
            elif self.installer_done:
                out.append("Stela restarted the PC again (its own repair step)")
        step = _STEP_RE.match(line)
        if step:
            out.extend(self._on_step(step.group("title")))
        detail = _DETAIL_RE.match(line)
        if detail and not self.installer_done and detail.group("what") != self._last_detail:
            self._last_detail = detail.group("what")
            out.append(f"Installer: {self._last_detail}")
        return out

    def _on_step(self, title: str) -> list[str]:
        if title == _INSTALLER_STEP:
            self.installer_started = True
            self.phase = "installing"
            return ["The macOS installer is running (it downloads macOS from Apple, this takes a while)"]
        if title == _MACOS_STEP and self.installer_done:
            self.macos_started += 1
            self.last_macos_start_line = self.lines_seen
            self.phase = "macos booting"
            return [f"Starting the installed macOS (boot {self.macos_started})"]
        return []

    @property
    def restarted_since_macos_start(self) -> bool:
        """True when the PC restarted after the latest macOS start: that boot did not last."""
        return self.last_restart_line > self.last_macos_start_line >= 0


def is_guest_dhcp(frame: bytes, mac: str) -> bool:
    """True when this Ethernet frame is a DHCP request (UDP port 68 to 67) sent from `mac`."""
    if len(frame) < 42 or frame[6:12] != bytes.fromhex(mac.replace(":", "")):
        return False
    offset = 12
    ethertype = frame[offset:offset + 2]
    while ethertype == b"\x81\x00" and len(frame) >= offset + 6:   # skip VLAN tags
        offset += 4
        ethertype = frame[offset:offset + 2]
    if ethertype != b"\x08\x00":
        return False
    ip = offset + 2
    if len(frame) < ip + 20 or frame[ip] >> 4 != 4 or frame[ip + 9] != 17:
        return False
    udp = ip + (frame[ip] & 15) * 4
    if len(frame) < udp + 4:
        return False
    return int.from_bytes(frame[udp:udp + 2], "big") == 68 and int.from_bytes(frame[udp + 2:udp + 4], "big") == 67
