"""Stand-in for subprocess.Popen that answers from a `subprocess.run`-style fake.

ProxmoxAdapter.run launches with Popen so it can take the whole process group down. Tests of its return values only
care what the command "printed", so they hand over the same (argv, **kw) fake they always did."""

from __future__ import annotations

import subprocess


def patch_adapter_run(monkeypatch, fake_run) -> None:
    class FakePopen:
        pid = 2 ** 22 + 1
        returncode = None

        def __init__(self, argv, **kw):
            self._argv, self._kw, self._timed_out = argv, kw, None

        def communicate(self, timeout=None):
            if self._timed_out:                             # the call after a kill collects what was printed
                return self._timed_out.stdout or "", self._timed_out.stderr or ""
            try:
                done = fake_run(self._argv, **self._kw)     # may raise TimeoutExpired
            except subprocess.TimeoutExpired as exc:
                self._timed_out = exc
                raise
            self.returncode = done.returncode
            return done.stdout, done.stderr

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr("osx_proxmox_next.infrastructure._kill_group", lambda proc: None)
