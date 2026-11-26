"""Shared values for the arm64 (beta) tests: a pinned source (ref and checksum) the way a tagged release carries it."""
from __future__ import annotations

REF = "v0.9.0"
SHA = "a" * 64
RENDERER_SHA = "b" * 64


def pin_release(monkeypatch) -> None:
    """Make the wizard and the CLI see a build that has a pinned release."""
    monkeypatch.setattr("osx_proxmox_next.arm64.DEFAULT_SOURCE_REF", REF)
    monkeypatch.setattr("osx_proxmox_next.arm64.DEFAULT_SOURCE_SHA256", SHA)
    monkeypatch.setattr("osx_proxmox_next.arm64_cli.DEFAULT_SOURCE_REF", REF)
    monkeypatch.setattr("osx_proxmox_next.arm64_cli.DEFAULT_SOURCE_SHA256", SHA)
