"""Pytest configuration for Textual async tests.

Each test that calls asyncio.run() creates and destroys an event loop.
Textual's cleanup can race with loop teardown, causing 'Event loop is closed'
in subsequent tests. This fixture ensures each test gets a clean loop policy.
"""

from __future__ import annotations

import asyncio

import pytest

import temp_size


@pytest.fixture(autouse=True)
def _no_host_memory_limit(monkeypatch):
    """Pin the host-RAM headroom check to 'unknown' for all tests.

    max_vm_memory_mb() reads the live /proc/meminfo, so on a Linux CI runner
    (or a loaded dev box) form validation would depend on how much RAM the
    machine happens to have free. Tests that exercise the check re-patch it
    with an explicit value."""
    monkeypatch.setattr("osx_proxmox_next.defaults.max_vm_memory_mb", lambda: 0)
    monkeypatch.setattr("osx_proxmox_next.services.host_facts.max_vm_memory_mb", lambda: 0)


@pytest.fixture(autouse=True)
def _no_github_for_device_list(monkeypatch, tmp_path):
    """The Intel device list is derived from a WhateverXe release on GitHub. Tests never reach GitHub: with
    IGPU_DEVICES_OFFLINE set the engine uses the bundled list (labelled as a fallback). Tests of the release path
    unset it and hand the engine a fake `get` / `header_get`. The host cache goes to a temp dir; subprocesses (the
    bash twin) see the same through the environment."""
    monkeypatch.setenv("IGPU_DEVICES_CACHE", str(tmp_path / "devices-cache"))
    monkeypatch.setenv("IGPU_DEVICES_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _reset_event_loop_policy():
    """Reset the asyncio event loop policy before and after each test."""
    asyncio.set_event_loop_policy(None)
    yield
    # Force a fresh policy so a closed loop from one test
    # does not leak into the next test.
    asyncio.set_event_loop_policy(None)
    # Suppress any lingering 'Event loop is closed' from Textual threads
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            asyncio.set_event_loop(asyncio.new_event_loop())
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def _private_gpu_state_dir(monkeypatch, tmp_path_factory):
    """Keep the GPU attach records of a real host (or another test) out of every test."""
    monkeypatch.setattr("osx_proxmox_next.gpu.STATE_DIR", tmp_path_factory.mktemp("gpu-state"))


@pytest.fixture(autouse=True)
def _private_driver_state_dir(monkeypatch, tmp_path_factory):
    """Driver records, locks and the daily release check never touch the host's /var/lib folder."""
    monkeypatch.setenv("DRIVER_STATE_DIR", str(tmp_path_factory.mktemp("driver-state")))
    monkeypatch.setenv("IGPU_STATE_DIR", str(tmp_path_factory.mktemp("igpu-state")))


@pytest.fixture
def other_pid():
    """The pid of a live process that is not the test: what another running update looks like."""
    import subprocess
    proc = subprocess.Popen(["sleep", "30"])
    yield proc.pid
    proc.kill()
    proc.wait()


GITHUB_FAKE = {
    "b00t0x/WhateverXe": [
        ("0.9.0", False, "WhateverXe-0.9.0-RELEASE.zip", "2026-11-01T00:00:00Z", "9" * 64),
        ("0.8.0", False, "WhateverXe-0.8.0-RELEASE.zip", "2026-10-01T00:00:00Z", "8" * 64),
    ],
    "b00t0x/WhateverXe-Graphics": [
        ("v0.4.1", False, "WhateverXe-Graphics-0.4.1.pkg", "2026-10-02T00:00:00Z", "4" * 64),
    ],
    "nullmoth/nvidia-macos-driver": [
        ("v2.9", False, "nullmoth-nvidia-v2.9.tar.gz", "2026-11-10T00:00:00Z", "9" * 64),
        ("v2.8.1", False, "nullmoth-nvidia-v2.8.1.tar.gz", "2026-10-10T00:00:00Z", "8" * 64),
    ],
}
SUMS_NAMES = {"nullmoth/nvidia-macos-driver": "SHA256SUMS.txt"}


def fake_github_releases(repo: str) -> list[dict]:
    """The releases API payload for *repo* from GITHUB_FAKE, with a digest on every asset."""
    sums = SUMS_NAMES.get(repo, "SHA256SUMS")
    return [{"tag_name": tag, "prerelease": pre, "draft": False, "published_at": when,
             "assets": [{"name": name, "digest": "sha256:" + sha,
                         "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}"},
                        {"name": sums, "digest": "sha256:" + "0" * 64,
                         "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{sums}"}]}
            for tag, pre, name, when, sha in GITHUB_FAKE[repo]]


@pytest.fixture(autouse=True)
def _no_github(monkeypatch):
    """No test reaches GitHub: the release lookups answer from GITHUB_FAKE unless a test passes its own `get`."""
    def fake(url: str):
        if "/releases/tags/" in url:
            from osx_proxmox_next.igpu_engine import ReleaseError
            raise ReleaseError("GitHub has no such release.")
        repo = url.split("/repos/")[1].split("/releases")[0]
        return fake_github_releases(repo)
    monkeypatch.setattr("osx_proxmox_next.igpu_engine.github_get", fake)


@pytest.fixture(autouse=True)
def _no_host_pci_devices(monkeypatch, tmp_path):
    """The driver update looks at the passed-through device on /sys. A CI runner has real PCI devices at the addresses
    the fakes use, so by default the look-up finds nothing (the record decides). Tests of the look-up give a root."""
    from osx_proxmox_next import driver_engine

    real = driver_engine.live_kind
    empty = str(tmp_path / "no-sysfs")
    monkeypatch.setattr(driver_engine, "live_kind", lambda cfg, root="/sys": real(cfg, empty if str(root) == "/sys" else root))
    monkeypatch.setenv("SYSFS_ROOT", empty)


# Fails the run when the tests leave too much behind in the pytest base temp dir (see temp_size.py).
def pytest_sessionfinish(session, exitstatus):
    factory = getattr(session.config, "_tmp_path_factory", None)
    if factory is None:
        return
    total, sizes = temp_size.temp_size_report(str(factory.getbasetemp()))
    lines = temp_size.temp_size_problems(total, sizes, temp_size.TOTAL_LIMIT_MB, temp_size.DIR_LIMIT_MB)
    if not lines:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    for line in ["", *lines]:
        if reporter:
            reporter.write_line(line, red=True)
        else:
            print(line)
    if session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
