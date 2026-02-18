from __future__ import annotations

import secrets
import string
import uuid
from dataclasses import dataclass

SMBIOS_MODELS: dict[str, str] = {
    "ventura": "iMacPro1,1",
    "sonoma": "iMacPro1,1",
    "sequoia": "iMacPro1,1",
    "tahoe": "MacPro7,1",
}

DEFAULT_SMBIOS_MODEL = "iMacPro1,1"


@dataclass
class SmbiosIdentity:
    serial: str
    mlb: str
    uuid: str
    rom: str
    model: str


def generate_serial() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(12))


def generate_mlb() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(17))


def generate_uuid() -> str:
    return str(uuid.uuid4()).upper()


def generate_rom() -> str:
    return secrets.token_hex(6).upper()


def model_for_macos(macos: str) -> str:
    return SMBIOS_MODELS.get(macos, DEFAULT_SMBIOS_MODEL)


def generate_smbios(macos: str) -> SmbiosIdentity:
    return SmbiosIdentity(
        serial=generate_serial(),
        mlb=generate_mlb(),
        uuid=generate_uuid(),
        rom=generate_rom(),
        model=model_for_macos(macos),
    )
