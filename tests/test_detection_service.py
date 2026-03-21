"""Unit tests for detection_service."""
from __future__ import annotations

import pytest

from osx_proxmox_next.infrastructure import CommandResult, ProxmoxAdapter
from osx_proxmox_next.domain import DEFAULT_VMID, MIN_VMID, MAX_VMID
from osx_proxmox_next.defaults import DEFAULT_STORAGE
from osx_proxmox_next.services.detection_service import (
    detect_next_vmid,
    detect_storage_targets,
)


def _adapter(pvesm_result=None, pvesh_result=None, qm_result=None):
    """Build a mock ProxmoxAdapter with predictable responses."""

    class MockAdapter(ProxmoxAdapter):
        def pvesm(self, *args):
            return pvesm_result or CommandResult(ok=False, returncode=1, output="")

        def pvesh(self, *args):
            return pvesh_result or CommandResult(ok=False, returncode=1, output="")

        def qm(self, *args):
            return qm_result or CommandResult(ok=False, returncode=1, output="")

    return MockAdapter()


# ---------------------------------------------------------------------------
# detect_storage_targets
# ---------------------------------------------------------------------------


def test_detect_storage_targets_returns_list() -> None:
    adapter = _adapter(
        pvesm_result=CommandResult(ok=True, returncode=0, output="Name\nlocal-lvm active images\n"),
    )
    result = detect_storage_targets(adapter=adapter)
    assert isinstance(result, list)


def test_detect_storage_targets_happy_path() -> None:
    output = "Name  Type  Status  Total  Used  Available  %\nlocal-lvm  lvm  active  1000  200  800  20%\n"
    adapter = _adapter(pvesm_result=CommandResult(ok=True, returncode=0, output=output))
    result = detect_storage_targets(adapter=adapter)
    assert "local-lvm" in result


def test_detect_storage_targets_fallback_on_failure() -> None:
    adapter = _adapter(pvesm_result=CommandResult(ok=False, returncode=1, output="error"))
    result = detect_storage_targets(adapter=adapter)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert DEFAULT_STORAGE in result


def test_detect_storage_targets_includes_default_storage() -> None:
    # Even if pvesm returns other targets, DEFAULT_STORAGE must be present
    output = "Name  Type  Status\nother-storage  dir  active\n"
    adapter = _adapter(pvesm_result=CommandResult(ok=True, returncode=0, output=output))
    result = detect_storage_targets(adapter=adapter)
    assert DEFAULT_STORAGE in result


def test_detect_storage_targets_deduplicates() -> None:
    output = "Name  Type  Status\nlocal-lvm  lvm  active\nlocal-lvm  lvm  active\n"
    adapter = _adapter(pvesm_result=CommandResult(ok=True, returncode=0, output=output))
    result = detect_storage_targets(adapter=adapter)
    assert result.count("local-lvm") == 1


def test_detect_storage_targets_skips_inactive() -> None:
    output = "Name  Type  Status\nlocal-lvm  lvm  active\ninactive-store  lvm  inactive\n"
    adapter = _adapter(pvesm_result=CommandResult(ok=True, returncode=0, output=output))
    result = detect_storage_targets(adapter=adapter)
    assert "inactive-store" not in result


def test_detect_storage_targets_max_5_entries() -> None:
    lines = ["Name  Type  Status"]
    for i in range(10):
        lines.append(f"store{i}  lvm  active")
    output = "\n".join(lines)
    adapter = _adapter(pvesm_result=CommandResult(ok=True, returncode=0, output=output))
    result = detect_storage_targets(adapter=adapter)
    assert len(result) <= 5


# ---------------------------------------------------------------------------
# detect_next_vmid
# ---------------------------------------------------------------------------


def test_detect_next_vmid_returns_int() -> None:
    adapter = _adapter(pvesh_result=CommandResult(ok=True, returncode=0, output="901"))
    result = detect_next_vmid(adapter=adapter)
    assert isinstance(result, int)


def test_detect_next_vmid_from_pvesh_digit() -> None:
    adapter = _adapter(pvesh_result=CommandResult(ok=True, returncode=0, output="901"))
    result = detect_next_vmid(adapter=adapter)
    assert result == 901


def test_detect_next_vmid_fallback_to_qm_list() -> None:
    qm_output = "      VMID Name\n       200 vm200\n       300 vm300\n"
    adapter = _adapter(
        pvesh_result=CommandResult(ok=False, returncode=1, output="error"),
        qm_result=CommandResult(ok=True, returncode=0, output=qm_output),
    )
    result = detect_next_vmid(adapter=adapter)
    assert result == 301


def test_detect_next_vmid_fallback_to_default_when_both_fail() -> None:
    adapter = _adapter(
        pvesh_result=CommandResult(ok=False, returncode=1, output="error"),
        qm_result=CommandResult(ok=False, returncode=1, output="error"),
    )
    result = detect_next_vmid(adapter=adapter)
    assert result == DEFAULT_VMID


def test_detect_next_vmid_respects_min_vmid() -> None:
    adapter = _adapter(
        pvesh_result=CommandResult(ok=False, returncode=1, output=""),
        qm_result=CommandResult(ok=True, returncode=0, output="      VMID Name\n"),
    )
    result = detect_next_vmid(adapter=adapter)
    assert result >= MIN_VMID


def test_detect_next_vmid_respects_max_vmid() -> None:
    qm_output = f"      VMID Name\n       {MAX_VMID} bigvm\n"
    adapter = _adapter(
        pvesh_result=CommandResult(ok=False, returncode=1, output=""),
        qm_result=CommandResult(ok=True, returncode=0, output=qm_output),
    )
    result = detect_next_vmid(adapter=adapter)
    assert result <= MAX_VMID


def test_detect_next_vmid_pvesh_out_of_range_falls_back_to_qm() -> None:
    # pvesh returns a value outside valid range
    qm_output = "      VMID Name\n       500 vm500\n"
    adapter = _adapter(
        pvesh_result=CommandResult(ok=True, returncode=0, output="99999999"),
        qm_result=CommandResult(ok=True, returncode=0, output=qm_output),
    )
    result = detect_next_vmid(adapter=adapter)
    # Should not return 99999999 since it's > MAX_VMID
    assert MIN_VMID <= result <= MAX_VMID
