from __future__ import annotations

import secrets
import string
import uuid
from dataclasses import dataclass

SMBIOS_MODELS: dict[str, str] = {
    "ventura": "MacPro7,1",
    "sonoma": "MacPro7,1",
    "sequoia": "MacPro7,1",
    "tahoe": "MacPro7,1",
}

DEFAULT_SMBIOS_MODEL = "MacPro7,1"

# Known valid board IDs for MacPro7,1
VALID_BOARD_IDS = [
    "Mac-5F5EDEB5FD3EFD52",
    "Mac-4B682C642B45593E",
    "Mac-827FAC58A8FDFA22",
]


@dataclass
class SmbiosIdentity:
    serial: str
    mlb: str
    uuid: str
    rom: str
    model: str


def generate_serial(apple_services: bool = False) -> str:
    """Generate serial. If apple_services=True, use valid format."""
    if apple_services:
        # Valid Apple serial format: 12 chars, mix of letters and numbers
        # Start with letter, contains mix
        chars = string.ascii_uppercase + string.digits
        first = secrets.choice(string.ascii_uppercase)
        rest = "".join(secrets.choice(chars) for _ in range(11))
        return first + rest
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(12))


def generate_mlb(apple_services: bool = False) -> str:
    """Generate MLB (Main Logic Board). If apple_services=True, use valid format."""
    if apple_services:
        # Valid MLB is 17 chars, typically uppercase letters and numbers
        # Use a known valid pattern or generate proper format
        chars = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(chars) for _ in range(17))
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(17))


def generate_uuid() -> str:
    return str(uuid.uuid4()).upper()


def generate_rom() -> str:
    return secrets.token_hex(6).upper()


def generate_mac() -> str:
    """Generate a static MAC address (local admin bit set)."""
    # First byte: 0x02 (locally administered, unicast)
    first_byte = secrets.randbelow(256) | 0x02 & 0xFE
    rest = [secrets.randbelow(256) for _ in range(5)]
    return ":".join(f"{(first_byte if i == 0 else rest[i-1]):02X}" for i in range(6))


def generate_vmgenid() -> str:
    """Generate a vmgenid UUID for Apple services."""
    return str(uuid.uuid4()).upper()


def model_for_macos(macos: str) -> str:
    return SMBIOS_MODELS.get(macos, DEFAULT_SMBIOS_MODEL)


def generate_smbios(macos: str, apple_services: bool = False) -> SmbiosIdentity:
    return SmbiosIdentity(
        serial=generate_serial(apple_services),
        mlb=generate_mlb(apple_services),
        uuid=generate_uuid(),
        rom=generate_rom(),
        model=model_for_macos(macos),
    )
