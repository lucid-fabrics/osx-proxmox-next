import re
import uuid as uuid_mod

from osx_proxmox_next.smbios import (
    generate_mlb,
    generate_rom,
    generate_serial,
    generate_smbios,
    generate_uuid,
    model_for_macos,
)


def test_serial_format() -> None:
    serial = generate_serial()
    assert len(serial) == 12
    assert re.fullmatch(r"[A-Z0-9]{12}", serial)


def test_mlb_format() -> None:
    mlb = generate_mlb()
    assert len(mlb) == 17
    assert re.fullmatch(r"[A-Z0-9]{17}", mlb)


def test_uuid_format() -> None:
    u = generate_uuid()
    parsed = uuid_mod.UUID(u)
    assert parsed.version == 4


def test_rom_format() -> None:
    rom = generate_rom()
    assert len(rom) == 12
    assert re.fullmatch(r"[A-F0-9]{12}", rom)


def test_model_for_known_macos() -> None:
    assert model_for_macos("ventura") == "MacPro7,1"
    assert model_for_macos("sonoma") == "MacPro7,1"
    assert model_for_macos("sequoia") == "MacPro7,1"
    assert model_for_macos("tahoe") == "MacPro7,1"


def test_model_for_unknown_macos() -> None:
    assert model_for_macos("unknown") == "MacPro7,1"


def test_generate_smbios_complete() -> None:
    identity = generate_smbios("sequoia")
    assert len(identity.serial) == 12
    assert len(identity.mlb) == 17
    assert len(identity.rom) == 12
    assert identity.model == "MacPro7,1"
    uuid_mod.UUID(identity.uuid)


def test_uniqueness() -> None:
    serials = {generate_serial() for _ in range(100)}
    assert len(serials) == 100

    uuids = {generate_uuid() for _ in range(100)}
    assert len(uuids) == 100

    mlbs = {generate_mlb() for _ in range(100)}
    assert len(mlbs) == 100
