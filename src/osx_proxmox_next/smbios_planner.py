from __future__ import annotations

import base64
import dataclasses
import re

from .domain import VmConfig
from .smbios import generate_mac, generate_rom_from_mac, generate_smbios, generate_vmgenid, model_for_macos

__all__ = [
    "_sanitize_smbios",
    "_encode_smbios_value",
    "_populate_smbios",
]


def _sanitize_smbios(val: str, *, allow_comma: bool = False) -> str:
    """Strip anything that isn't alphanumeric, hyphen, colon, or period.

    Only *model* names need commas (e.g. ``MacPro7,1``).
    """
    if allow_comma:
        return re.sub(r"[^a-zA-Z0-9\-:,.]", "", val)
    return re.sub(r"[^a-zA-Z0-9\-:.]", "", val)


def _encode_smbios_value(value: str) -> str:
    """Base64-encode a value for Proxmox smbios1 fields."""
    return base64.b64encode(value.encode()).decode()


def _populate_smbios(config: VmConfig) -> VmConfig:
    """Pre-generate SMBIOS identity and Apple services fields on config.

    Called once at the top of build_plan so downstream helpers just read fields.
    Returns a new VmConfig with the generated values applied.
    """
    if config.no_smbios:
        return config
    updates: dict = {}
    if not config.smbios_serial:
        identity = generate_smbios(config.macos, config.apple_services)
        updates["smbios_serial"] = identity.serial
        updates["smbios_uuid"] = identity.uuid
        updates["smbios_model"] = identity.model
        updates["smbios_mlb"] = identity.mlb
        updates["smbios_rom"] = identity.rom
        if identity.mac and not config.static_mac:
            updates["static_mac"] = identity.mac
    if not config.smbios_model and "smbios_model" not in updates:
        updates["smbios_model"] = model_for_macos(config.macos)
    if config.apple_services:
        if not config.vmgenid:
            updates["vmgenid"] = generate_vmgenid()
        static_mac = updates.get("static_mac") or config.static_mac
        if not static_mac:
            static_mac = generate_mac()
            updates["static_mac"] = static_mac
        updates["smbios_rom"] = generate_rom_from_mac(static_mac)
    return dataclasses.replace(config, **updates)
