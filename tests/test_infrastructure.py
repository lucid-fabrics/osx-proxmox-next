import subprocess

from osx_proxmox_next.infrastructure import ProxmoxAdapter


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


def test_run_nonzero_returncode(monkeypatch):
    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="fail")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = ProxmoxAdapter()
    result = adapter.run(["qm", "status", "999"])
    assert result.ok is False
    assert result.returncode == 1
    assert "fail" in result.output
