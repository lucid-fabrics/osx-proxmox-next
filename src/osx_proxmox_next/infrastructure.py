from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import subprocess


@dataclass
class CommandResult:
    ok: bool
    returncode: int
    output: str


_SUBPROCESS_TIMEOUT = 300
_EXIT_CODE_NOT_FOUND = 127
_EXIT_CODE_TIMEOUT = 124


_KILL_GRACE_SECONDS = 3


def _kill_group(proc: subprocess.Popen) -> None:
    """Take the child's whole process group down: SIGTERM, a short grace wait, then SIGKILL.

    The child is its own group leader (start_new_session), so its children (qemu-img, dpkg, the stages of a
    `bash -c` pipeline) share the group and would otherwise outlive it."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)         # also reaps grandchildren that ignored SIGTERM
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


class ProxmoxAdapter:
    def run(self, argv: list[str], timeout: int | None = None) -> CommandResult:
        limit = timeout or _SUBPROCESS_TIMEOUT
        try:
            # Detached from the terminal: a `qm shutdown` that shares it ends up hanging the tty up, and the TUI
            # that owns it dies with "Input/output error" (seen live). Nothing here ever reads a prompt.
            # Being detached also means Ctrl+C no longer reaches the child's group, so every exit path below
            # takes the group down itself.
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError:
            return CommandResult(ok=False, returncode=_EXIT_CODE_NOT_FOUND, output=f"Command not found: {argv[0]}")
        try:
            stdout, stderr = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            stdout, stderr = proc.communicate()
            output = f"Command timed out after {limit}s: {' '.join(argv)}\n{stdout or ''}{stderr or ''}"
            return CommandResult(ok=False, returncode=_EXIT_CODE_TIMEOUT, output=output.strip())
        except BaseException:
            _kill_group(proc)
            raise
        output = (stdout or "") + (stderr or "")
        return CommandResult(ok=(proc.returncode == 0), returncode=proc.returncode, output=output.strip())

    def qm(self, *args: str) -> CommandResult:
        return self.run(["qm", *args])

    def pvesm(self, *args: str) -> CommandResult:
        return self.run(["pvesm", *args])

    def volume_path(self, ref: str) -> str:
        """The device or file of a volume, made usable first: PVE 9 leaves guest logical volumes inactive after
        a boot (and a rolled-back thin volume keeps the activation-skip flag), so a disk that a stopped VM
        owns may not exist in /dev yet. Empty when the volume has no path."""
        path = self.pvesm("path", ref).output.strip()
        if path.startswith("/dev/") and not path.startswith(("/dev/zvol/", "/dev/rbd")):
            if self.run(["lvs", path]).ok:    # an LV (lvm, lvmthin); zfs, rbd and files need nothing
                self.run(["lvchange", "-ay", "-K", path])
        return path

    def pvesh(self, *args: str) -> CommandResult:
        return self.run(["pvesh", *args])


def run_command(cmd: list[str]) -> CommandResult:
    """Run an arbitrary command and return a CommandResult.

    Raises CommandResult with ok=False on FileNotFoundError or timeout;
    raises the original exception for CalledProcessError so callers can
    inspect stderr.
    """
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True)
        output = (proc.stdout or b"").decode(errors="replace")
        return CommandResult(ok=True, returncode=0, output=output.strip())
    except subprocess.CalledProcessError as exc:
        raise
    except FileNotFoundError:
        return CommandResult(ok=False, returncode=_EXIT_CODE_NOT_FOUND, output=f"Command not found: {cmd[0]}")
    except subprocess.TimeoutExpired as exc:
        return CommandResult(ok=False, returncode=_EXIT_CODE_TIMEOUT, output=f"Command timed out: {' '.join(cmd)}")
