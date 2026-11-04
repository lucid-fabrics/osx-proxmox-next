"""BETA: drive a complete macOS install over the VM console, hands-off.

Reproduces at the keyboard level what a person does at the noVNC console:
boot the recovery entry from the OpenCore picker, open Terminal from the
recovery Utilities menu, erase the target disk, run startosinstall, then
keep selecting the installer volume in the picker across the install's
reboots until macOS sits at Setup Assistant.

Only two primitives touch the VM, both local root commands on the PVE
host: ``qm monitor <vmid>`` screendump (the frame's byte size tells the
screen state: ~6.2 MB = 1920x1080 OpenCore picker, ~3 MB = 1280x800
macOS) and ``qm sendkey``. No VNC, no image processing, no dependencies.

Verified end to end on Ventura and Sequoia (Intel i9-14900K), Sonoma and
Sequoia (AMD Ryzen 9950X), and previously Tahoe.

Keep constants and key sequences in sync with the bash installer's
unattended_install (scripts/bash/osx-proxmox-next.sh); tests diff both.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

__all__ = ["QmConsole", "UnattendedError", "erase_install_command", "run_unattended_install"]

# Frame-size heuristics (bytes of a raw PPM screendump).
PICKER_MIN_BYTES = 5_000_000       # 1920x1080 OpenCore picker
MACOS_MAX_BYTES = 4_500_000        # 1280x800 recovery / installer / Setup Assistant

# Phase timing (seconds).
PICKER_TIMEOUT = 600
RECOVERY_TIMEOUT = 900
INSTALL_REBOOT_TIMEOUT = 1800      # startosinstall prep before its first reboot
RECOVERY_SETTLE = 240              # minimum settle before the utilities window is looked for
RECOVERY_STILL_MAX = 600           # extra grace while the recovery screen is still moving
RECOVERY_STILL_FRAMES = 3          # identical consecutive frames = nothing animating
TERMINAL_OPEN_WAIT = 10
DONE_QUIET = 900                   # no picker for 15 min after reboots = install done
DONE_MOTION = 120                  # Setup Assistant redraws its menu-bar clock every minute
HUNG_STILL = 600                   # frame byte-identical for 10 min = macOS is wedged
PICKER_REPRESS = 30                # seconds between return presses at a parked picker
MAX_POWER_CYCLES = 2               # stop/start attempts on a wedged guest before giving up
TOTAL_BUDGET = 3 * 3600
POLL_INTERVAL = 6
KEY_DELAY = 0.4
TYPE_DELAY = 0.12

# Recovery menu path to Terminal: focus the menu bar, walk to Utilities,
# third item down. Stable across Ventura, Sonoma, Sequoia, and Tahoe recovery.
TERMINAL_NAV = ("ctrl-f2", "right", "right", "right", "right",
                "down", "down", "down", "ret")

# Characters the install command needs, mapped to QEMU sendkey names.
CHARMAP = {
    " ": "spc", ".": "dot", "/": "slash", "-": "minus", "_": "shift-minus",
    ",": "comma", ";": "semicolon", ":": "shift-semicolon", "=": "equal",
    "+": "shift-equal", "'": "apostrophe", '"': "shift-apostrophe",
    "\\": "backslash", "|": "shift-backslash", "*": "shift-8", "$": "shift-4",
    "(": "shift-9", ")": "shift-0", "&": "shift-7", "!": "shift-1",
    "{": "shift-bracket_left", "}": "shift-bracket_right",
    "<": "shift-comma", ">": "shift-dot", "?": "shift-slash",
    "#": "shift-3", "%": "shift-5", "@": "shift-2", "^": "shift-6",
    "~": "shift-grave_accent", "`": "grave_accent",
}


class UnattendedError(Exception):
    """A phase of the unattended install did not reach its expected state."""


def _diskutil_size(disk_gb: int) -> str:
    """The size diskutil prints for a disk_gb GiB QEMU disk, e.g. 128 -> 137.4.

    QEMU sizes are binary GiB; diskutil displays decimal GB to one decimal."""
    return f"{disk_gb * 1.073741824:.1f}"


def erase_install_command(disk_gb: int) -> str:
    """One shell line typed into recovery Terminal: erase the target disk
    (matched by its exact diskutil size, never a hardcoded device) and run
    the non-interactive installer."""
    size = _diskutil_size(disk_gb).replace(".", "\\.")
    return (
        f"d=$(diskutil list | awk '/\\*{size} GB/{{print $NF; exit}}') && "
        "diskutil eraseDisk APFS MACOS $d && "
        '"/Install macOS "*.app/Contents/Resources/startosinstall '
        "--agreetolicense --volume /Volumes/MACOS --nointeraction"
    )


class QmConsole:
    """Screen and keyboard of one VM via qm monitor/sendkey."""

    def __init__(self, vmid: int, runner: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
                 sleep: Callable[[float], None] = time.sleep):
        self.vmid = str(vmid)
        self._run = runner
        self._sleep = sleep
        self._probe = os.path.join(tempfile.gettempdir(), f"osx-next-unattended-{vmid}.ppm")

    def frame_size(self) -> int:
        try:
            os.unlink(self._probe)
        except FileNotFoundError:
            pass
        self._run(f"echo 'screendump {self._probe}' | qm monitor {self.vmid}",
                  shell=True, capture_output=True)
        self._sleep(0.6)
        try:
            return os.path.getsize(self._probe)
        except FileNotFoundError:
            return 0

    def probe_hash(self) -> str:
        """md5 of the screendump frame_size() last wrote; "" when there is none."""
        try:
            with open(self._probe, "rb") as probe:
                return hashlib.md5(probe.read()).hexdigest()
        except FileNotFoundError:
            return ""

    def frame_hash(self) -> str:
        """md5 of a fresh screendump; "" when the dump failed."""
        if not self.frame_size():
            return ""
        return self.probe_hash()

    def save_frame(self, dest: str) -> str:
        """Write the current screen to *dest* and return the path written.

        A stalled run says nothing about what is on the console, and the
        console is gone by the time anyone reads the log. PNG needs QEMU
        7.1 or newer; older hosts get the raw PPM next to it. Returns ""
        when neither lands."""
        ppm = dest[:-4] + ".ppm" if dest.endswith(".png") else dest + ".ppm"
        for command, path in ((f"screendump {dest} -f png", dest),
                              (f"screendump {ppm}", ppm)):
            self._run(f"echo '{command}' | qm monitor {self.vmid}",
                      shell=True, capture_output=True)
            self._sleep(0.6)
            try:
                if os.path.getsize(path) > 0:
                    return path
            except OSError:
                continue
        return ""

    def sendkey(self, key: str) -> None:
        self._run(["qm", "sendkey", self.vmid, key], capture_output=True)

    def qm(self, subcommand: str, *args: str) -> str:
        """Run a qm subcommand against this VM and return its output."""
        result = self._run(["qm", subcommand, self.vmid, *args],
                           capture_output=True, text=True)
        return (getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")

    def seq(self, keys: tuple[str, ...] | list[str], delay: float = KEY_DELAY) -> None:
        for key in keys:
            self.sendkey(key)
            self._sleep(delay)

    def type_text(self, text: str) -> None:
        for ch in text:
            if ch.isupper():
                key = "shift-" + ch.lower()
            elif ch in CHARMAP:
                key = CHARMAP[ch]
            else:
                key = ch
            self.sendkey(key)
            self._sleep(TYPE_DELAY)


def _wait(console: QmConsole, predicate: Callable[[int], bool], timeout: float,
          what: str, clock: Callable[[], float], sleep: Callable[[float], None],
          repress: str = "") -> int:
    """Poll the screen until *predicate* accepts its byte size.

    With *repress* set, that key is pressed again every PICKER_REPRESS
    seconds for as long as the screen is still the 1080p picker: OpenCanopy
    reaches its first 1080p frame before its input is ready, so on a slow
    boot disk the first return is dropped, and Timeout=0 then parks the
    picker forever (issue #142)."""
    deadline = clock() + timeout
    last_press = clock() - PICKER_REPRESS
    while clock() < deadline:
        size = console.frame_size()
        if predicate(size):
            return size
        if repress and size > PICKER_MIN_BYTES and clock() - last_press >= PICKER_REPRESS:
            console.sendkey(repress)
            last_press = clock()
        sleep(POLL_INTERVAL)
    raise UnattendedError(f"Timed out after {int(timeout)}s waiting for {what}")


def _wait_still(console: QmConsole, on_event: Callable[[str], None],
                clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Hold until the recovery screen stops animating.

    A cold first boot can still be in Recovery Assistant ("Examining
    volumes", a spinner) when the settle expires. Its menu bar carries no
    Utilities menu, so the Terminal navigation walks the wrong menu bar,
    the command is typed into the utilities window that appears later and
    its closing return runs the highlighted "Restore from Time Machine".
    Identical consecutive frames mean nothing is animating any more, which
    is only true once the utilities window is up."""
    deadline = clock() + RECOVERY_STILL_MAX
    seen = console.frame_hash()
    still = 1
    while clock() < deadline:
        sleep(POLL_INTERVAL)
        current = console.frame_hash()
        if current and current == seen:
            still += 1
            if still >= RECOVERY_STILL_FRAMES:
                return
        else:
            seen, still = current, 1
    on_event("Recovery screen never stopped moving; navigating anyway")


def _wait_first_reboot(console: QmConsole, on_event: Callable[[str], None],
                       clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Hold until startosinstall reboots the guest (the first 1080p frame).

    The guest's own restart can wedge inside an EFI runtime call instead:
    every vCPU spins and recovery Terminal stays frozen on "Restarting...".
    A screen unchanged for HUNG_STILL counts as that reboot, because the
    stop/start the caller does next is also the only thing that clears the
    wedge. startosinstall prints its percentage as it prepares, so a live
    install never sits that still."""
    deadline = clock() + INSTALL_REBOOT_TIMEOUT
    seen, last_change = "", clock()
    while clock() < deadline:
        if console.frame_size() > PICKER_MIN_BYTES:
            return
        digest = console.probe_hash()
        if digest != seen:
            seen, last_change = digest, clock()
        elif digest and clock() - last_change > HUNG_STILL:
            on_event(f"Guest froze while restarting (screen unchanged for "
                     f"{HUNG_STILL // 60} min); power-cycling")
            return
        sleep(POLL_INTERVAL)
    raise UnattendedError(f"Timed out after {INSTALL_REBOOT_TIMEOUT}s waiting for "
                          "the installer's first reboot")


def run_unattended_install(
    console: QmConsole,
    disk_gb: int,
    on_event: Callable[[str], None] = log.info,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Drive the install until macOS sits quiet at Setup Assistant.

    Returns {"reboots": N, "elapsed": seconds}. Raises UnattendedError when
    a phase times out; the VM is left as-is for manual inspection."""
    start = clock()

    on_event("Waiting for the OpenCore boot picker")
    _wait(console, lambda s: s > PICKER_MIN_BYTES, PICKER_TIMEOUT,
          "the OpenCore picker", clock, sleep)
    sleep(2)
    on_event("Booting macOS recovery (first picker entry)")

    # One return is not enough: it lands while OpenCanopy is still loading on
    # a slow boot disk and is dropped, leaving the picker parked forever.
    # Keep pressing while the 1080p picker is on screen, exactly as the
    # post-detach loop does.
    _wait(console, lambda s: 0 < s < MACOS_MAX_BYTES, RECOVERY_TIMEOUT,
          "recovery to start", clock, sleep, repress="ret")
    on_event(f"Recovery is booting; settling {RECOVERY_SETTLE}s for the utilities window")
    sleep(RECOVERY_SETTLE)
    _wait_still(console, on_event, clock, sleep)

    on_event("Opening Terminal from the Utilities menu")
    console.seq(TERMINAL_NAV)
    sleep(TERMINAL_OPEN_WAIT)

    command = erase_install_command(disk_gb)
    on_event("Typing the erase + startosinstall command (the VM disk is wiped now)")
    console.type_text(command)
    sleep(1)
    console.sendkey("ret")
    on_event("Installer launched; waiting for its first reboot")

    # First 1080p frame after launch = the install's first reboot (picker or
    # boot logo). Detach the recovery disk now, exactly what the manual flow
    # documents: from here the picker has a single real entry, so a blind
    # selection can never boot the wrong volume. A picker selection that
    # merely walks entries (right+ret) proved flaky: one missed keystroke
    # boots recovery and the install silently stalls there.
    _wait_first_reboot(console, on_event, clock, sleep)
    on_event("First reboot reached; detaching recovery so the picker has one entry")
    console.qm("stop")
    _wait_stopped(console, clock, sleep)
    console.qm("set", "--delete", "ide2")
    console.qm("start")
    on_event("Recovery detached; continuing hands-off")

    boots = 0
    last_big = clock()
    last_change = clock()
    last_press = clock() - PICKER_REPRESS
    cycles = 0
    seen = ""
    while clock() - start < TOTAL_BUDGET:
        size = console.frame_size()
        # macOS wedged behind the boot logo is as quiet as a finished install
        # (1280x800 logo) or as a parked picker (1080p logo). What separates
        # them is motion.
        # Running macOS always moves, if only because the Setup Assistant menu
        # bar ticks its clock every minute; a wedged kernel never redraws.
        digest = console.probe_hash()
        changed = bool(digest) and digest != seen
        if changed:
            seen = digest
            last_change = clock()
        if boots >= 1 and clock() - last_change > HUNG_STILL:
            # The guest's own reboot can wedge in an MP rendezvous with every
            # vCPU spinning and the framebuffer stuck on its last frame. qm
            # reset does not clear that; only a full stop/start does, and it
            # boots the finished install to Setup Assistant in about 100s.
            # Checked before the 1080p branch: a wedge on the 1080p boot logo
            # otherwise reads as a parked picker and gets pressed until the
            # budget runs out, while a real picker moves once a press lands.
            if cycles >= MAX_POWER_CYCLES:
                raise UnattendedError(
                    f"macOS appears hung: screen unchanged for {HUNG_STILL // 60} min"
                )
            cycles += 1
            on_event(f"Guest appears wedged (screen unchanged for "
                     f"{HUNG_STILL // 60} min); power-cycling")
            console.qm("stop")
            _wait_stopped(console, clock, sleep)
            console.qm("start")
            # The restarted guest earns its quiet window again: coming back
            # from a power cycle is not by itself proof the install finished.
            last_change = last_big = clock()
            seen = ""
        elif size > PICKER_MIN_BYTES:
            last_big = clock()
            if changed:
                boots += 1
                sleep(2)
                # Single-entry picker (Timeout=0 still waits) or an ignored
                # keystroke on the Apple-logo boot screen; either way safe.
                console.sendkey("ret")
                last_press = clock()
                on_event(f"1080p frame {boots}: confirmed the only boot entry")
                sleep(PICKER_REPRESS)
            elif clock() - last_press >= PICKER_REPRESS:
                # A byte-identical 1080p frame is a picker still parked on its
                # entry: Timeout=0 waits forever and the first return was
                # dropped. Press again, quietly, so the log keeps one line
                # per boot instead of one per poll.
                console.sendkey("ret")
                last_press = clock()
        # Done needs a screen that moved within DONE_MOTION: Setup Assistant
        # ticks its clock every minute. A guest that wedged a few minutes ago
        # is still under HUNG_STILL but already still; it must wait for the
        # hang guard above, not be reported as a finished install.
        elif (boots >= 1 and clock() - last_big > DONE_QUIET
              and clock() - last_change <= DONE_MOTION):
            elapsed = int(clock() - start)
            on_event(
                f"Done: macOS has been running without a reboot for "
                f"{DONE_QUIET // 60} min after {boots} boot(s). "
                "Setup Assistant should be on the console now."
            )
            return {"reboots": boots, "elapsed": elapsed}
        sleep(POLL_INTERVAL)

    raise UnattendedError(f"Install did not finish within {TOTAL_BUDGET // 3600}h "
                          f"({boots} boot(s) seen)")


def _wait_stopped(console: QmConsole, clock: Callable[[], float],
                  sleep: Callable[[float], None], timeout: float = 120) -> None:
    deadline = clock() + timeout
    while clock() < deadline:
        if "stopped" in console.qm("status"):
            return
        sleep(3)
    raise UnattendedError("VM did not stop when asked to (detach or power cycle)")
