from __future__ import annotations

from dataclasses import dataclass
import subprocess


@dataclass
class CommandResult:
    ok: bool
    returncode: int
    output: str


class ProxmoxAdapter:
    def run(self, argv: list[str]) -> CommandResult:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=300)
            output = (proc.stdout or "") + (proc.stderr or "")
            return CommandResult(ok=(proc.returncode == 0), returncode=proc.returncode, output=output.strip())
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            output = f"Command timed out after 300s: {' '.join(argv)}\n{stdout}{stderr}"
            return CommandResult(ok=False, returncode=124, output=output.strip())

    def qm(self, *args: str) -> CommandResult:
        return self.run(["qm", *args])

    def pvesm(self, *args: str) -> CommandResult:
        return self.run(["pvesm", *args])

    def pvesh(self, *args: str) -> CommandResult:
        return self.run(["pvesh", *args])
