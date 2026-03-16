import subprocess

from osx_proxmox_next.infrastructure import ProxmoxAdapter, run_command


def test_run_timeout(monkeypatch):
    def raise_timeout(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=["qm", "status"], timeout=300)
        exc.stdout = "partial"
        exc.stderr = ""
        raise exc

    monkeypatch.setattr(subprocess, "run", raise_timeout)
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

    monkeypatch.setattr(subprocess, "run", fake_run)
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

    monkeypatch.setattr(subprocess, "run", fake_run)
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

    monkeypatch.setattr(subprocess, "run", raise_timeout)
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

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    adapter = ProxmoxAdapter()
    result = adapter.run(["qm", "start", "900"])
    assert result.ok is False
    assert "timed out" in result.output


def test_run_nonzero_returncode(monkeypatch):
    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="fail")

    monkeypatch.setattr(subprocess, "run", fake_run)
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
