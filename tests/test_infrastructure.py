import os
import subprocess

import pytest

from osx_proxmox_next import infrastructure
from osx_proxmox_next.infrastructure import ProxmoxAdapter, run_command
from adapter_fakes import patch_adapter_run


def test_run_timeout(monkeypatch):
    def raise_timeout(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=["qm", "status"], timeout=300)
        exc.stdout = "partial"
        exc.stderr = ""
        raise exc

    patch_adapter_run(monkeypatch, raise_timeout)
    adapter = ProxmoxAdapter()
    result = adapter.run(["qm", "status", "900"])
    assert result.ok is False
    assert result.returncode == 124
    assert "timed out" in result.output


def test_pvesm_wraps_binary(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    patch_adapter_run(monkeypatch, fake_run)
    adapter = ProxmoxAdapter()
    result = adapter.pvesm("status")
    assert result.ok is True
    assert calls[0][0] == "pvesm"
    assert calls[0][1] == "status"


def test_pvesh_wraps_binary(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    patch_adapter_run(monkeypatch, fake_run)
    adapter = ProxmoxAdapter()
    result = adapter.pvesh("get", "/cluster/nextid")
    assert result.ok is True
    assert calls[0][0] == "pvesh"
    assert calls[0][1] == "get"


def test_run_timeout_with_bytes_output(monkeypatch):
    """TimeoutExpired.stdout/stderr can be bytes — must not crash."""
    def raise_timeout(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=["qm", "start"], timeout=300)
        exc.stdout = b"partial output"
        exc.stderr = b"error bytes"
        raise exc

    patch_adapter_run(monkeypatch, raise_timeout)
    adapter = ProxmoxAdapter()
    result = adapter.run(["qm", "start", "900"])
    assert result.ok is False
    assert "partial output" in result.output
    assert "error bytes" in result.output


def test_run_timeout_with_none_output(monkeypatch):
    """TimeoutExpired.stdout/stderr can be None — must not crash."""
    def raise_timeout(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=["qm", "start"], timeout=300)
        exc.stdout = None
        exc.stderr = None
        raise exc

    patch_adapter_run(monkeypatch, raise_timeout)
    adapter = ProxmoxAdapter()
    result = adapter.run(["qm", "start", "900"])
    assert result.ok is False
    assert "timed out" in result.output


def test_run_nonzero_returncode(monkeypatch):
    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="fail")

    patch_adapter_run(monkeypatch, fake_run)
    adapter = ProxmoxAdapter()
    result = adapter.run(["qm", "status", "999"])
    assert result.ok is False
    assert result.returncode == 1
    assert "fail" in result.output


def test_run_command_success(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=b"hello\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(["echo", "hello"])
    assert result.ok is True
    assert result.returncode == 0
    assert "hello" in result.output


def test_run_command_failure(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, output=b"", stderr=b"fail")

    monkeypatch.setattr(subprocess, "run", fake_run)
    import pytest
    with pytest.raises(subprocess.CalledProcessError):
        run_command(["false"])


def test_run_command_file_not_found(monkeypatch):
    def fake_run(cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(["nonexistent-cmd"])
    assert result.ok is False
    assert result.returncode == 127
    assert "not found" in result.output


def test_run_command_timeout(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(["sleep", "100"])
    assert result.ok is False
    assert result.returncode == 124
    assert "timed out" in result.output


def test_commands_run_detached_from_the_terminal_so_qm_cannot_hang_the_tty_up(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    patch_adapter_run(monkeypatch, fake_run)
    assert ProxmoxAdapter().qm("shutdown", "101").ok
    assert seen["stdin"] is subprocess.DEVNULL and seen["start_new_session"] is True


# ── the whole process group goes down on every exit path (real subprocesses) ──────────────────

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")          # an unreaped zombie is already dead


def _spawner(pidfile) -> list[str]:
    """A command that starts a grandchild ignoring SIGTERM, records its pid and then waits for it."""
    return ["sh", "-c", f"(trap '' TERM; exec sleep 60) & echo $! > {pidfile}; wait"]


def _pid_of(pidfile) -> int:
    return int(pidfile.read_text())


def test_timeout_takes_the_grandchild_down_too(monkeypatch, tmp_path):
    pidfile = tmp_path / "pid"
    monkeypatch.setattr(infrastructure, "_SUBPROCESS_TIMEOUT", 2)
    result = ProxmoxAdapter().run(_spawner(pidfile))
    assert result.returncode == 124 and "timed out" in result.output and result.ok is False
    assert not _alive(_pid_of(pidfile))


def test_keyboard_interrupt_takes_the_grandchild_down_and_propagates(monkeypatch, tmp_path):
    pidfile = tmp_path / "pid"
    real_communicate = subprocess.Popen.communicate

    def interrupted(self, input=None, timeout=None):
        while not (pidfile.exists() and pidfile.read_text().strip()):
            try:
                self.wait(timeout=0.05)                     # a bounded wait for the grandchild to start
            except subprocess.TimeoutExpired:
                pass
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        ProxmoxAdapter().run(_spawner(pidfile))
    monkeypatch.setattr(subprocess.Popen, "communicate", real_communicate)
    assert not _alive(_pid_of(pidfile))


def test_a_normal_command_still_returns_its_output_and_exit_code():
    ok = ProxmoxAdapter().run(["sh", "-c", "echo out; echo err >&2"])
    assert (ok.ok, ok.returncode, ok.output) == (True, 0, "out\nerr")
    bad = ProxmoxAdapter().run(["sh", "-c", "echo boom >&2; exit 3"])
    assert (bad.ok, bad.returncode, bad.output) == (False, 3, "boom")
    assert ProxmoxAdapter().run(["no-such-binary-xyz"]).returncode == 127


def test_a_step_timeout_replaces_the_default_limit(monkeypatch):
    monkeypatch.setattr(infrastructure, "_SUBPROCESS_TIMEOUT", 1)
    assert ProxmoxAdapter().run(["bash", "-c", "sleep 2; echo late"], timeout=30).output == "late"
    assert ProxmoxAdapter().run(["bash", "-c", "sleep 5"]).returncode == 124
