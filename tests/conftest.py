"""Pytest configuration for Textual async tests.

Each test that calls asyncio.run() creates and destroys an event loop.
Textual's cleanup can race with loop teardown, causing 'Event loop is closed'
in subsequent tests. This fixture ensures each test gets a clean loop policy.
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _no_host_memory_limit(monkeypatch):
    """Pin the host-RAM headroom check to 'unknown' for all tests.

    max_vm_memory_mb() reads the live /proc/meminfo, so on a Linux CI runner
    (or a loaded dev box) form validation would depend on how much RAM the
    machine happens to have free. Tests that exercise the check re-patch it
    with an explicit value."""
    monkeypatch.setattr("osx_proxmox_next.defaults.max_vm_memory_mb", lambda: 0)
    monkeypatch.setattr("osx_proxmox_next._wizard_mixin.max_vm_memory_mb", lambda: 0)


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


GITHUB_FAKE = {
    "b00t0x/WhateverXe": [
        ("0.9.0", False, "WhateverXe-0.9.0-RELEASE.zip", "2026-11-01T00:00:00Z", "9" * 64),
        ("0.8.0", False, "WhateverXe-0.8.0-RELEASE.zip", "2026-10-01T00:00:00Z", "8" * 64),
    ],
    "b00t0x/WhateverXe-Graphics": [
        ("v0.4.1", False, "WhateverXe-Graphics-0.4.1.pkg", "2026-10-02T00:00:00Z", "4" * 64),
    ],
}


def fake_github_releases(repo: str) -> list[dict]:
    """The releases API payload for *repo* from GITHUB_FAKE, with a digest on every asset."""
    return [{"tag_name": tag, "prerelease": pre, "draft": False, "published_at": when,
             "assets": [{"name": name, "digest": "sha256:" + sha,
                         "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}"},
                        {"name": "SHA256SUMS", "digest": "sha256:" + "0" * 64,
                         "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/SHA256SUMS"}]}
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
