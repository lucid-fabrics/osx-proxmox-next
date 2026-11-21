from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Iterable

from ..preflight import FixCommand, PreflightCheck

log = logging.getLogger(__name__)

__all__ = ["collect_fixes", "run_fix_commands"]


def collect_fixes(checks: Iterable[PreflightCheck]) -> list[FixCommand]:
    """The fix commands of every failing check, in order, without repeats."""
    commands: list[FixCommand] = []
    for check in checks:
        if check.ok:
            continue
        for command in check.fix:
            if command not in commands:
                commands.append(command)
    return commands


def run_fix_commands(
    commands: Iterable[FixCommand],
    on_line: Callable[[str], None],
    popen: Callable[..., "subprocess.Popen"] = subprocess.Popen,
) -> bool:
    """Run each fix command in order and stream its output to *on_line*.

    Stops at the first command that fails. Returns True when all succeeded.
    Runs on the calling (worker) thread; *on_line* must hand over to the UI
    thread itself."""
    for command in commands:
        on_line(f"$ {command.display}")
        try:
            proc = popen(list(command.argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True)
        except OSError as exc:
            on_line(f"Could not start it: {exc}")
            return False
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line.rstrip("\n"))
        code = proc.wait()
        if code != 0:
            on_line(f"It exited with code {code}. Nothing after it was run.")
            return False
    return True
