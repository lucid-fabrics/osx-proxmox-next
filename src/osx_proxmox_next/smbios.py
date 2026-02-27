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

# ---------------------------------------------------------------------------
# Apple serial/MLB format constants
# ---------------------------------------------------------------------------

# Base-34 alphabet (no I, no O)
BASE34 = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"

# Year encoding chars — index 0-9 maps to years 2010-2019 (or 2020-2029 cycle)
_YEAR_CHARS = "CDFGHJKLMN"

# Per-model manufacturing data
# NOTE: duplicated in scripts/bash/osx-proxmox-next.sh — keep in sync
APPLE_PLATFORM_DATA: dict[str, dict] = {
    "MacPro7,1": {
        "model_codes": [
            "P7QM", "PLXV", "PLXW", "PLXX", "PLXY",
            "P7QJ", "P7QK", "P7QL", "P7QN", "P7QP",
            "NYGV", "K7GF", "K7GD", "N5RN",
        ],
        "board_codes": ["K3F7"],
        "country_codes": ["C02", "C07", "CK2"],
        "year_range": (2019, 2023),
    },
}

# MLB random-block pools
MLB_BLOCK1 = [
    "200", "600", "403", "404", "405", "303",
    "108", "207", "609", "501", "306", "102", "701", "301",
]
MLB_BLOCK2 = [f"Q{c}" for c in BASE34]


@dataclass
class SmbiosIdentity:
    serial: str
    mlb: str
    uuid: str
    rom: str
    model: str
    mac: str = ""


# ---------------------------------------------------------------------------
# Apple-format helpers
# ---------------------------------------------------------------------------

def _encode_year_week(year: int, week: int) -> tuple[str, str]:
    """Encode year and week into Apple serial year/week chars.

    Year char cycles every 10 years from 2010.
    Weeks 1-26 use the base year char; weeks 27-52 advance the year char by 1.
    Week char encodes position within the half-year.
    """
    decade_offset = (year - 2010) % 10
    if week <= 26:
        year_char = _YEAR_CHARS[decade_offset]
        week_index = week
    else:
        year_char = _YEAR_CHARS[(decade_offset + 1) % 10]
        week_index = week - 26
    week_char = BASE34[week_index]
    return year_char, week_char


def _encode_line(line: int) -> str:
    """Encode production line (0-3399) into 3 base-34 chars (pure base-34)."""
    d1 = line // (34 * 34)
    d2 = (line // 34) % 34
    d3 = line % 34
    return BASE34[d1] + BASE34[d2] + BASE34[d3]


def _random_manufacturing_data(model: str) -> dict:
    """Generate random Apple manufacturing metadata for a given model."""
    platform = APPLE_PLATFORM_DATA[model]
    year_lo, year_hi = platform["year_range"]
    return {
        "country": secrets.choice(platform["country_codes"]),
        "year": year_lo + secrets.randbelow(year_hi - year_lo + 1),
        "week": 1 + secrets.randbelow(52),
        "line": secrets.randbelow(3400),
        "model_code": secrets.choice(platform["model_codes"]),
    }


def _build_apple_serial(mfg: dict) -> str:
    """Build a 12-char Apple-format serial from manufacturing data.

    Format: {country:3}{year:1}{week:1}{line:3}{model_code:4}
    """
    year_char, week_char = _encode_year_week(mfg["year"], mfg["week"])
    line_str = _encode_line(mfg["line"])
    return mfg["country"] + year_char + week_char + line_str + mfg["model_code"]


def _generate_apple_serial(model: str) -> str:
    """Generate a 12-char Apple-format serial number."""
    return _build_apple_serial(_random_manufacturing_data(model))


def _verify_mlb_checksum(mlb: str) -> bool:
    """Verify mod-34 alternating-weight (3/1) checksum on an MLB string."""
    checksum = 0
    for i, ch in enumerate(mlb):
        j = BASE34.index(ch)
        weight = 3 if ((i & 1) == (len(mlb) & 1)) else 1
        checksum += weight * j
    return checksum % 34 == 0


def _build_apple_mlb(mfg: dict, model: str) -> str:
    """Build a 17-char checksummed MLB from manufacturing data.

    Format: {country:3}{year_dec:1}{week_dec:2}{block1:3}{block2:2}{board:4}{block3:2}
    Block3 is computed analytically to satisfy the mod-34 checksum.
    """
    platform = APPLE_PLATFORM_DATA[model]
    board = secrets.choice(platform["board_codes"])

    country = mfg["country"]
    year_dec = str(mfg["year"] % 10)
    week_dec = f"{mfg['week']:02d}"

    block1 = secrets.choice(MLB_BLOCK1)
    block2 = secrets.choice(MLB_BLOCK2)

    # 15-char prefix (everything except block3)
    prefix = country + year_dec + week_dec + block1 + block2 + board

    # Compute block3 deterministically to satisfy checksum.
    # Block3 = "0{c}" where '0' is at position 15 (weight 3, value 0 → contributes 0)
    # and c is at position 16 (weight 1, value j16).
    # We need: prefix_sum + 0 + j16 ≡ 0 (mod 34)
    prefix_sum = 0
    for i, ch in enumerate(prefix):
        weight = 3 if ((i & 1) == (17 & 1)) else 1
        prefix_sum += weight * BASE34.index(ch)
    j16 = (-prefix_sum) % 34
    block3 = f"0{BASE34[j16]}"

    return prefix + block3


def _generate_apple_mlb(serial: str, model: str) -> str:
    """Generate a 17-char MLB consistent with a serial (standalone use).

    Decodes country from serial. Year/week are approximate due to the serial's
    half-year encoding ambiguity — use _build_apple_mlb with raw manufacturing
    data for exact consistency (as generate_smbios does).
    """
    platform = APPLE_PLATFORM_DATA[model]
    year_lo = platform["year_range"][0]

    year_char = serial[3]
    week_char = serial[4]

    year_idx = _YEAR_CHARS.index(year_char)
    week_offset = BASE34.index(week_char)

    actual_year = 2010 + year_idx
    while actual_year < year_lo:
        actual_year += 10

    mfg = {
        "country": serial[:3],
        "year": actual_year,
        "week": week_offset,
    }
    return _build_apple_mlb(mfg, model)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_serial(apple_services: bool = False, model: str = DEFAULT_SMBIOS_MODEL) -> str:
    """Generate serial. If apple_services=True, use Apple format."""
    if apple_services:
        return _generate_apple_serial(model)
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(12))


def generate_mlb(
    apple_services: bool = False,
    serial: str = "",
    model: str = DEFAULT_SMBIOS_MODEL,
) -> str:
    """Generate MLB. If apple_services=True, use Apple format with checksum."""
    if apple_services and serial:
        return _generate_apple_mlb(serial, model)
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(17))


def generate_uuid() -> str:
    return str(uuid.uuid4()).upper()


def generate_rom() -> str:
    return secrets.token_hex(6).upper()


def generate_rom_from_mac(mac: str) -> str:
    """Derive ROM from MAC address (6 bytes, no colons, uppercase)."""
    return mac.replace(":", "")[:12].upper()


def generate_mac() -> str:
    """Generate a static MAC address (local admin bit set)."""
    first_byte = (secrets.randbelow(256) | 0x02) & 0xFE
    rest = [secrets.randbelow(256) for _ in range(5)]
    return ":".join(f"{(first_byte if i == 0 else rest[i-1]):02X}" for i in range(6))


def generate_vmgenid() -> str:
    """Generate a vmgenid UUID for Apple services."""
    return str(uuid.uuid4()).upper()


def model_for_macos(macos: str) -> str:
    return SMBIOS_MODELS.get(macos, DEFAULT_SMBIOS_MODEL)


def generate_smbios(macos: str, apple_services: bool = False) -> SmbiosIdentity:
    model = model_for_macos(macos)
    mac = ""
    if apple_services:
        mac = generate_mac()
        rom = generate_rom_from_mac(mac)
        # Generate serial and MLB from shared manufacturing data
        # so year/week are exactly consistent (no half-year ambiguity).
        mfg = _random_manufacturing_data(model)
        serial = _build_apple_serial(mfg)
        mlb = _build_apple_mlb(mfg, model)
    else:
        rom = generate_rom()
        serial = generate_serial(apple_services=False)
        mlb = generate_mlb(apple_services=False)
    return SmbiosIdentity(
        serial=serial,
        mlb=mlb,
        uuid=generate_uuid(),
        rom=rom,
        model=model,
        mac=mac,
    )
