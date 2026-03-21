"""Unit tests for smbios_planner helpers."""
from __future__ import annotations

import base64

import pytest

from osx_proxmox_next.domain import VmConfig
from osx_proxmox_next.smbios_planner import (
    _encode_smbios_value,
    _populate_smbios,
    _sanitize_smbios,
)


# ---------------------------------------------------------------------------
# _sanitize_smbios
# ---------------------------------------------------------------------------


def test_sanitize_smbios_keeps_alphanumeric() -> None:
    assert _sanitize_smbios("ABC123") == "ABC123"


def test_sanitize_smbios_keeps_hyphen_colon_period() -> None:
    assert _sanitize_smbios("A-B:C.D") == "A-B:C.D"


def test_sanitize_smbios_strips_spaces_and_special_chars() -> None:
    result = _sanitize_smbios("Hello World! @#$%")
    assert " " not in result
    assert "!" not in result
    assert result == "HelloWorld"


def test_sanitize_smbios_strips_comma_by_default() -> None:
    result = _sanitize_smbios("MacPro7,1")
    assert "," not in result
    assert result == "MacPro71"


def test_sanitize_smbios_allows_comma_when_flag_set() -> None:
    result = _sanitize_smbios("MacPro7,1", allow_comma=True)
    assert result == "MacPro7,1"


def test_sanitize_smbios_empty_string() -> None:
    assert _sanitize_smbios("") == ""


def test_sanitize_smbios_all_special_chars() -> None:
    assert _sanitize_smbios("!@#$%^&*()") == ""


def test_sanitize_smbios_long_string_preserved() -> None:
    val = "A" * 200
    assert _sanitize_smbios(val) == val


# ---------------------------------------------------------------------------
# _encode_smbios_value
# ---------------------------------------------------------------------------


def test_encode_smbios_value_basic() -> None:
    result = _encode_smbios_value("hello")
    assert result == base64.b64encode(b"hello").decode()


def test_encode_smbios_value_empty() -> None:
    result = _encode_smbios_value("")
    assert result == base64.b64encode(b"").decode()
    assert result == ""


def test_encode_smbios_value_returns_string() -> None:
    result = _encode_smbios_value("test")
    assert isinstance(result, str)


def test_encode_smbios_value_special_chars() -> None:
    val = "C$2!xR@k#9"
    result = _encode_smbios_value(val)
    decoded = base64.b64decode(result).decode()
    assert decoded == val


def test_encode_smbios_value_roundtrip() -> None:
    original = "ABCDEF123456"
    encoded = _encode_smbios_value(original)
    assert base64.b64decode(encoded).decode() == original


# ---------------------------------------------------------------------------
# _populate_smbios
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> VmConfig:
    defaults = dict(
        vmid=901,
        name="macos-test",
        macos="sequoia",
        cores=8,
        memory_mb=16384,
        disk_gb=128,
        bridge="vmbr0",
        storage="local-lvm",
    )
    defaults.update(kwargs)
    return VmConfig(**defaults)


def test_populate_smbios_returns_vmconfig() -> None:
    cfg = _make_config()
    result = _populate_smbios(cfg)
    assert isinstance(result, VmConfig)


def test_populate_smbios_generates_serial_when_missing() -> None:
    cfg = _make_config(smbios_serial="")
    result = _populate_smbios(cfg)
    assert result.smbios_serial != ""


def test_populate_smbios_does_not_overwrite_existing_serial() -> None:
    cfg = _make_config(smbios_serial="EXISTINGSERIAL")
    result = _populate_smbios(cfg)
    # When serial is already set, populate_smbios should not regenerate it
    assert result.smbios_serial == "EXISTINGSERIAL"


def test_populate_smbios_no_smbios_flag_returns_unchanged() -> None:
    cfg = _make_config(no_smbios=True, smbios_serial="KEEPME123456")
    result = _populate_smbios(cfg)
    # no_smbios=True skips all generation
    assert result.smbios_serial == "KEEPME123456"
    assert result is cfg or result.no_smbios is True


def test_populate_smbios_sets_model_when_missing() -> None:
    cfg = _make_config(smbios_serial="", smbios_model="")
    result = _populate_smbios(cfg)
    assert result.smbios_model != ""


def test_populate_smbios_apple_services_sets_vmgenid() -> None:
    cfg = _make_config(apple_services=True, vmgenid="")
    result = _populate_smbios(cfg)
    assert result.vmgenid != ""


def test_populate_smbios_apple_services_sets_smbios_rom() -> None:
    cfg = _make_config(apple_services=True)
    result = _populate_smbios(cfg)
    assert result.smbios_rom != ""


def test_populate_smbios_preserves_existing_vmgenid() -> None:
    existing = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
    cfg = _make_config(apple_services=True, vmgenid=existing)
    result = _populate_smbios(cfg)
    assert result.vmgenid == existing


def test_populate_smbios_different_macos_versions() -> None:
    for macos in ("ventura", "sonoma", "sequoia", "tahoe"):
        cfg = _make_config(macos=macos, smbios_serial="")
        result = _populate_smbios(cfg)
        assert result.smbios_serial != "", f"Expected serial for {macos}"
