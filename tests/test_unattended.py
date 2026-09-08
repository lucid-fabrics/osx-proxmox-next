"""Tests for the unattended install driver (BETA) and its bash parity."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from osx_proxmox_next.unattended import (
    CHARMAP,
    DONE_QUIET,
    INSTALL_REBOOT_TIMEOUT,
    PICKER_MIN_BYTES,
    PICKER_TIMEOUT,
    POLL_INTERVAL,
    RECOVERY_SETTLE,
    RECOVERY_STILL_FRAMES,
    RECOVERY_STILL_MAX,
    RECOVERY_TIMEOUT,
    TERMINAL_NAV,
    TOTAL_BUDGET,
    QmConsole,
    UnattendedError,
    _diskutil_size,
    erase_install_command,
    run_unattended_install,
)

REPO = Path(__file__).resolve().parent.parent
BASH_SCRIPT = REPO / "scripts/bash/osx-proxmox-next.sh"


# ---------------------------------------------------------------------------
# Install command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gb,shown", [(64, "68.7"), (128, "137.4"),
                                      (256, "274.9"), (512, "549.8")])
def test_diskutil_size_matches_what_diskutil_prints(gb: int, shown: str) -> None:
    assert _diskutil_size(gb) == shown


def test_erase_install_command_is_the_proven_one() -> None:
    """Exactly the command validated live on Intel and AMD Sequoia installs."""
    assert erase_install_command(128) == (
        "d=$(diskutil list | awk '/\\*137\\.4 GB/{print $NF; exit}') && "
        "diskutil eraseDisk APFS MACOS $d && "
        '"/Install macOS "*.app/Contents/Resources/startosinstall '
        "--agreetolicense --volume /Volumes/MACOS --nointeraction"
    )


def test_every_command_character_is_typeable() -> None:
    for ch in erase_install_command(128):
        assert ch.islower() or ch.isdigit() or ch.isupper() or ch in CHARMAP, ch


# ---------------------------------------------------------------------------
# Driver state machine (fake console + fake clock, no real sleeps)
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeConsole:
    """Frame sizes come from a schedule of (from_time, size) entries."""

    def __init__(self, clock: FakeClock, schedule: list[tuple[float, int]],
                 moving_until: float = 0.0):
        self.clock = clock
        self.schedule = schedule
        self.moving_until = moving_until
        self.keys: list[str] = []
        self.typed: list[str] = []
        self.qm_calls: list[tuple[str, ...]] = []
        self.stopped = False

    def frame_size(self) -> int:
        if self.stopped:
            return 0
        size = 0
        for start, value in self.schedule:
            if self.clock.now >= start:
                size = value
        return size

    def frame_hash(self) -> str:
        """An animating screen hashes differently on every poll."""
        size = self.frame_size()
        if not size:
            return ""
        if self.clock.now < self.moving_until:
            return f"{size}-moving-{self.clock.now}"
        return f"{size}-still"

    def sendkey(self, key: str) -> None:
        self.keys.append(key)

    def seq(self, keys, delay: float = 0.4) -> None:
        for key in keys:
            self.sendkey(key)
            self.clock.sleep(delay)

    def type_text(self, text: str) -> None:
        self.typed.append(text)
        self.clock.sleep(len(text) * 0.12)

    def qm(self, subcommand: str, *args: str) -> str:
        self.qm_calls.append((subcommand, *args))
        if subcommand == "stop":
            self.stopped = True
        if subcommand == "start":
            self.stopped = False
        if subcommand == "status":
            return "status: stopped" if self.stopped else "status: running"
        return ""


PICKER = 6_200_000
MACOS = 3_000_000


def _run(schedule: list[tuple[float, int]]):
    clock = FakeClock()
    console = FakeConsole(clock, schedule)
    events: list[str] = []
    summary = run_unattended_install(console, 128, on_event=events.append,
                                     clock=clock.monotonic, sleep=clock.sleep)
    return console, events, summary


def test_full_install_happy_path() -> None:
    """The stopped VM reads frame 0; after restart the schedule resumes,
    so the post-detach entries model the single-entry picker boots."""
    schedule = [
        (0, 0),              # firmware, no display yet
        (30, PICKER),        # first picker
        (60, 0),             # recovery booting
        (200, MACOS),        # recovery UI up
        (1000, PICKER),      # installer's first reboot -> detach happens here
        (1100, PICKER),      # single-entry picker after restart
        (1130, MACOS),       # installing
        (1700, PICKER),      # next stage boot
        (1730, MACOS),       # installed macOS, then quiet through DONE_QUIET
    ]
    console, events, summary = _run(schedule)
    # recovery is detached at the first install reboot
    assert ("stop",) in console.qm_calls
    assert ("set", "--delete", "ide2") in console.qm_calls
    assert ("start",) in console.qm_calls
    assert summary["reboots"] >= 2
    assert list(TERMINAL_NAV) == console.keys[1:1 + len(TERMINAL_NAV)]
    assert console.typed == [erase_install_command(128)]
    # only the menu navigation uses "right"; no blind picker entry-walking
    assert console.keys.count("right") == TERMINAL_NAV.count("right")
    assert any("Done" in e for e in events)


def test_terminal_nav_waits_for_a_still_screen() -> None:
    """A cold boot still animating Recovery Assistant at the end of the
    settle must not be navigated: the wrong menu bar is on screen."""
    clock = FakeClock()
    moving_until = RECOVERY_SETTLE + 300
    console = FakeConsole(clock, [(0, 0), (30, PICKER), (60, 0), (200, MACOS),
                                  (2000, PICKER), (2100, PICKER), (2130, MACOS),
                                  (2700, PICKER), (2730, MACOS)],
                          moving_until=moving_until)
    events: list[str] = []
    nav_at: list[float] = []
    original = console.sendkey

    def watched(key: str) -> None:
        if key == "ctrl-f2":
            nav_at.append(clock.now)
        original(key)

    console.sendkey = watched  # type: ignore[method-assign]
    run_unattended_install(console, 128, on_event=events.append,
                           clock=clock.monotonic, sleep=clock.sleep)
    assert nav_at and nav_at[0] >= moving_until
    assert not any("never stopped moving" in e for e in events)


def test_still_wait_gives_up_and_says_so() -> None:
    """A screen that never stops moving still gets navigated, with a note."""
    clock = FakeClock()
    console = FakeConsole(clock, [(0, 0), (30, PICKER), (60, 0), (200, MACOS),
                                  (2000, PICKER), (2100, PICKER), (2130, MACOS),
                                  (2700, PICKER), (2730, MACOS)],
                          moving_until=float("inf"))
    events: list[str] = []
    run_unattended_install(console, 128, on_event=events.append,
                           clock=clock.monotonic, sleep=clock.sleep)
    assert any("never stopped moving" in e for e in events)
    assert "ctrl-f2" in console.keys


def test_picker_timeout_raises() -> None:
    with pytest.raises(UnattendedError, match="OpenCore picker"):
        _run([(0, 0)])


def test_recovery_timeout_raises() -> None:
    with pytest.raises(UnattendedError, match="recovery"):
        _run([(0, 0), (30, PICKER)])  # picker forever, recovery never starts


def test_installer_never_reboots_raises() -> None:
    # Recovery loads and the command is typed, but no reboot ever happens.
    with pytest.raises(UnattendedError, match="first reboot"):
        _run([(0, 0), (30, PICKER), (60, 0), (200, MACOS)])


def test_budget_exhaustion_raises() -> None:
    # First reboot happens (recovery detached) but the picker then reappears
    # forever: the install never settles.
    schedule = [(0, 0), (30, PICKER), (60, 0), (200, MACOS), (1000, PICKER)]
    with pytest.raises(UnattendedError, match="did not finish"):
        _run(schedule)


def test_done_requires_at_least_one_boot_after_detach() -> None:
    """Quiet macOS frames alone must not be declared done: without a boot
    after the detach, the installer never even started stage 2."""
    with pytest.raises(UnattendedError):
        _run([(0, 0), (30, PICKER), (60, MACOS)])


# ---------------------------------------------------------------------------
# QmConsole plumbing
# ---------------------------------------------------------------------------


def test_qmconsole_typing_uses_charmap() -> None:
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv if isinstance(argv, list) else [argv])
        return subprocess.CompletedProcess(argv, 0)

    console = QmConsole(999, runner=runner, sleep=lambda s: None)
    console.type_text('A$ "x')
    keys = [c[3] for c in calls]
    assert keys == ["shift-a", "shift-4", "spc", "shift-apostrophe", "x"]


def test_qmconsole_frame_size_zero_without_dump() -> None:
    console = QmConsole(998, runner=lambda *a, **k: subprocess.CompletedProcess(a, 0),
                        sleep=lambda s: None)
    assert console.frame_size() == 0


def test_qmconsole_frame_hash(tmp_path: Path) -> None:
    """No dump means no hash; a dumped frame hashes its bytes."""
    console = QmConsole(997, runner=lambda *a, **k: subprocess.CompletedProcess(a, 0),
                        sleep=lambda s: None)
    console._probe = str(tmp_path / "frame.ppm")
    assert console.frame_hash() == ""

    def dumping_runner(*args, **kwargs):
        Path(console._probe).write_bytes(b"P6 frame")
        return subprocess.CompletedProcess(args, 0)

    console._run = dumping_runner
    assert console.frame_hash() == "e0dc4968b8f2767a4b4fe1f0db9e640c"


# ---------------------------------------------------------------------------
# Bash parity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bash_text() -> str:
    return BASH_SCRIPT.read_text()


def test_bash_constants_match_python(bash_text: str) -> None:
    expected = {
        "UNATTENDED_INSTALL_REBOOT_TIMEOUT": INSTALL_REBOOT_TIMEOUT,
        "UNATTENDED_PICKER_MIN_BYTES": PICKER_MIN_BYTES,
        "UNATTENDED_RECOVERY_SETTLE": RECOVERY_SETTLE,
        "UNATTENDED_RECOVERY_STILL_MAX": RECOVERY_STILL_MAX,
        "UNATTENDED_RECOVERY_STILL_FRAMES": RECOVERY_STILL_FRAMES,
        "UNATTENDED_PICKER_TIMEOUT": PICKER_TIMEOUT,
        "UNATTENDED_RECOVERY_TIMEOUT": RECOVERY_TIMEOUT,
        "UNATTENDED_DONE_QUIET": DONE_QUIET,
        "UNATTENDED_TOTAL_BUDGET": TOTAL_BUDGET,
        "UNATTENDED_POLL": POLL_INTERVAL,
    }
    for name, value in expected.items():
        m = re.search(rf"^{name}=(\d+)$", bash_text, re.M)
        assert m, name
        assert int(m.group(1)) == value, name


def test_bash_terminal_nav_matches_python(bash_text: str) -> None:
    assert "for key in " + " ".join(TERMINAL_NAV) + ";" in bash_text.replace("; do", ";")


def _bash_install_command(disk_gb: int) -> str:
    text = BASH_SCRIPT.read_text()
    start = text.index("UNATTENDED_PICKER_MIN_BYTES=")
    end = text.index("function unattended_wait_frame")
    result = subprocess.run(
        ["bash", "-c", text[start:end] + f"\nunattended_install_command {disk_gb}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("gb", [64, 128, 256])
def test_bash_generates_identical_install_command(gb: int) -> None:
    """Execute the real bash generator and diff against Python, byte for byte.
    Uses no bash-4 features, so it runs everywhere."""
    assert _bash_install_command(gb) == erase_install_command(gb)


def test_bash_wires_unattended_after_start(bash_text: str) -> None:
    assert 'UNATTENDED="no"' in bash_text
    assert re.search(r'if \[ "\$\{UNATTENDED:-no\}" == "yes" \]', bash_text)
    assert bash_text.index('qm start "$VMID"') < bash_text.index('unattended_install "$VMID"')


def test_bash_detaches_recovery_at_first_reboot(bash_text: str) -> None:
    fn = bash_text[bash_text.index("function unattended_install()"):]
    assert 'qm set "$vmid" --delete ide2' in fn
    assert fn.index("unattended_install_command") < fn.index("--delete ide2")
    assert 'qm sendkey "$vmid" right' not in fn  # no blind entry-walking


def test_bash_waits_for_a_still_screen_before_the_nav(bash_text: str) -> None:
    fn = bash_text[bash_text.index("function unattended_install()"):]
    assert fn.index('sleep "$UNATTENDED_RECOVERY_SETTLE"') \
        < fn.index('unattended_wait_still "$vmid"') < fn.index("ctrl-f2")
    assert "md5sum" in bash_text[bash_text.index("function unattended_frame_hash()"):]
